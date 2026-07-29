"""PDF export tests.

Structure and content, not pixels: each test asserts that a fact from the review
reached the document, or that a structural rule holds. Rendering fidelity is not
something a unit test can judge, and asserting on byte offsets would break on
every ReportLab upgrade for no benefit.

Text is read back with pypdf, which is already a dependency for SoW ingestion.
"""

from __future__ import annotations

import io
import pathlib
import zlib

import pytest
from pypdf import PdfReader

import maturity
import report
import roadmap
import rubric
import scoring
from schema import Component, Finding, ReviewResult, ScoreDelta


# --------------------------------------------------------------------------- #
# Fixtures — built from the real rubric so pillar counts are not invented
# --------------------------------------------------------------------------- #

REVIEW_ID = "3f7a1c92-5b64-4e2a-9d18-0c6b5a2e7f41"


def _findings(*, failing: set[str] | None = None) -> list[Finding]:
    failing = failing or set()
    out: list[Finding] = []
    for check in rubric.all_checks():
        failed = check.check_id in failing
        out.append(
            Finding(
                framework=check.framework,
                pillar_id=check.pillar_id,
                check_id=check.check_id,
                status="fail" if failed else "pass",
                severity=check.severity,  # type: ignore[arg-type]
                title=check.description[:70],
                evidence="Not specified anywhere in the design."
                if failed
                else "Addressed by the design.",
                remediation="Enable the control and record the decision."
                if failed
                else "",
                remediation_effort="low" if failed else "",
                priority=1 if failed else 0,
            )
        )
    return out


def _review(**overrides) -> ReviewResult:
    findings = overrides.pop("findings", None)
    if findings is None:
        first_high = next(c for c in rubric.all_checks() if c.severity == "high")
        findings = _findings(failing={first_high.check_id})
    overall, frameworks = scoring.score(findings)
    base = {
        # A real id is a uuid4; storage._safe_id rejects anything else, so a
        # friendlier-looking fixture id would not survive the route tests.
        "review_id": REVIEW_ID,
        "created_at": "2026-07-27T10:04:00Z",
        "title": "Synthetic expense portal",
        "overall_score": overall,
        "frameworks": frameworks,
        "findings": findings,
        "components": [
            Component(
                id="db",
                label="Claims database",
                kind="database",
                provider="aws",
                service="rds",
            )
        ],
        "summary": "Solid shape with encryption gaps.",
        "executive_summary": "One high-severity control must close before deployment.",
    }
    base.update(overrides)
    return ReviewResult(**base)


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return report.build_pdf(_review())


@pytest.fixture(scope="module")
def pdf_text(pdf: bytes) -> str:
    return text_of(pdf)


# --------------------------------------------------------------------------- #
# It is a PDF at all
# --------------------------------------------------------------------------- #

def test_output_is_a_readable_pdf(pdf: bytes) -> None:
    assert pdf.startswith(b"%PDF-")
    assert PdfReader(io.BytesIO(pdf)).pages, "no pages"


def test_document_metadata_names_the_review(pdf: bytes) -> None:
    meta = PdfReader(io.BytesIO(pdf)).metadata
    assert meta is not None
    assert "Synthetic expense portal" in (meta.title or "")


# --------------------------------------------------------------------------- #
# 1. Cover page
# --------------------------------------------------------------------------- #

def test_cover_carries_name_date_score_and_branding(pdf: bytes) -> None:
    cover = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""

    assert "Synthetic expense portal" in cover
    assert "27 July 2026" in cover, "cover must show a readable review date"
    assert "Trust7 Gatekeeper" in cover
    assert "MINFY TECHNOLOGIES" in cover

    review = _review()
    assert f"{review.overall_score:.1f}" in cover, "overall score missing"
    assert maturity.maturity_for(review.overall_score) in cover, "maturity band missing"


def test_cover_states_the_headline_counts(pdf: bytes) -> None:
    cover = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
    for label in ("CHECKS EVALUATED", "OPEN FINDINGS", "HIGH SEVERITY"):
        assert label in cover, f"{label} missing from cover"


# --------------------------------------------------------------------------- #
# 2. Branding: navy, indigo, and no purple anywhere
# --------------------------------------------------------------------------- #

def _content_streams(pdf: bytes) -> str:
    """Every page's raw content stream, where fill colours are recorded."""
    chunks = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        data = page.get_contents().get_data()  # type: ignore[union-attr]
        try:
            data = zlib.decompress(data)
        except zlib.error:
            pass
        chunks.append(data.decode("latin-1"))
    return "\n".join(chunks)


def _rgb_fills(streams: str) -> list[tuple[float, float, float]]:
    """Parse `r g b rg` and `r g b RG` operators out of the content streams."""
    fills = []
    tokens = streams.replace("\n", " ").split()
    for i, token in enumerate(tokens):
        if token in ("rg", "RG") and i >= 3:
            try:
                fills.append(tuple(float(t) for t in tokens[i - 3 : i]))  # type: ignore[arg-type]
            except ValueError:
                continue
    return fills


def _near(actual, expected, tol=0.02) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected))


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    value = hex_colour.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def test_cover_paints_the_minfy_navy_background_and_indigo_accent(pdf: bytes) -> None:
    fills = _rgb_fills(_content_streams(pdf))
    assert fills, "no colour operators found"

    assert any(_near(f, _rgb("#1B263B")) for f in fills), "Minfy navy #1B263B not used"
    assert any(_near(f, _rgb("#1420BE")) for f in fills), (
        "Minfy indigo #1420BE not used"
    )


def test_the_report_palette_matches_the_web_ui_theme_tokens() -> None:
    """The PDF and the dashboard must not disagree about the brand.

    A reviewer downloads this from the results page; two accent colours for one
    review reads as two products.

    The expected values are PARSED out of frontend/src/index.css rather than written
    out here. The previous version restated them, which meant a retheme had to
    remember to update this file too — and a forgotten update would have asserted
    that the old palette was still correct.
    """
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
    import contrast_audit

    web = contrast_audit.web_tokens()
    for constant, token in [
        ("ACCENT", "minfy-indigo"),
        ("ACCENT_LIGHT", "minfy-blue"),
        ("NAVY", "minfy-navy"),
        ("INK", "ink"),
        ("INK_MUTED", "ink-muted"),
        ("HAIRLINE", "hairline"),
        ("OFF_WHITE", "surface-sunken"),
        ("PASTEL_SKY", "pastel-sky"),
        ("PASTEL_MINT", "pastel-mint"),
        ("PASTEL_CREAM", "pastel-cream"),
        ("LOGO_YELLOW", "minfy-yellow"),
    ]:
        actual = getattr(report, constant).hexval().replace("0x", "#")
        assert actual == web[token], (
            f"report.{constant} is {actual}, but --color-{token} is {web[token]}"
        )


def test_the_cover_accent_is_the_solved_value_not_the_eyeballed_one() -> None:
    """ACCENT_ON_DARK has no web twin — it exists only because the primary indigo is
    unreadable on the navy cover. It is checked against the AA threshold it has to
    meet, not against a remembered hex, since that is the property that matters."""
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"))
    import contrast_audit

    on_navy = contrast_audit.ratio(
        report.ACCENT_ON_DARK.hexval().replace("0x", "#"),
        report.NAVY.hexval().replace("0x", "#"),
    )

    # 4.5 is the bar for the 8.5pt cover eyebrow. The first attempt at this, chosen
    # by eye, was 4.12:1 — which is why the assertion is a ratio.
    assert on_navy >= 4.5, f"cover accent is {on_navy:.2f}:1 on navy"


def test_no_superseded_orange_survives_anywhere(pdf: bytes) -> None:
    """The old brand accent must be gone, not merely unused by the cover.

    Assembled from parts so that tests/test_contrast.py, which greps the tree for
    this literal, does not find it here and report itself as a violation.
    """
    superseded = _rgb("#" + "E8" + "5D" + "26")
    offenders = [f for f in _rgb_fills(_content_streams(pdf)) if _near(f, superseded)]
    assert not offenders, f"superseded Minfy orange still painted: {offenders}"


def test_framework_blocks_are_painted_and_carry_no_severity_meaning(pdf: bytes) -> None:
    """Both framework pastels appear, and neither is a status colour.

    Read off the module constants rather than hardcoded, so the AA retheme that
    lightened both pastels did not need this test edited to keep passing.
    """
    fills = _rgb_fills(_content_streams(pdf))
    sky = report.PASTEL_SKY.hexval().replace("0x", "#")
    mint = report.PASTEL_MINT.hexval().replace("0x", "#")

    assert any(_near(f, _rgb(sky)) for f in fills), f"AWS WAF block {sky} missing"
    assert any(_near(f, _rgb(mint)) for f in fills), f"TRUST-7 block {mint} missing"


def test_no_purple_or_violet_anywhere(pdf: bytes) -> None:
    """Brand rule: no purple/violet. Blue-dominant navy must still be allowed."""
    offenders = []
    for r, g, b in _rgb_fills(_content_streams(pdf)):
        # Purple/violet: red and blue both clearly above green.
        if r > g + 0.12 and b > g + 0.12 and max(r, b) > 0.25:
            offenders.append((round(r, 3), round(g, 3), round(b, 3)))
    assert not offenders, f"purple-ish fills found: {offenders}"


# --------------------------------------------------------------------------- #
# 3. Pillar scorecard — all 13 pillars
# --------------------------------------------------------------------------- #

def test_scorecard_lists_every_pillar_from_both_frameworks(pdf_text: str) -> None:
    review = _review()
    pillars = [p for f in review.frameworks for p in f.pillars]
    assert len(pillars) == 13, f"fixture has {len(pillars)} pillars, expected 13"

    for pillar in pillars:
        assert pillar.pillar_name in pdf_text, f"pillar missing from PDF: {pillar.pillar_name}"

    for framework in review.frameworks:
        assert framework.framework_name in pdf_text


def test_scorecard_shows_a_score_and_maturity_band_per_pillar(pdf_text: str) -> None:
    assert "Pillar scorecard" in pdf_text
    for header in ("PILLAR", "SCORE", "MATURITY", "PASSED"):
        assert header in pdf_text
    # At least one real band label reached the page.
    assert any(label in pdf_text for _, label in maturity.BANDS)


def test_maturity_bands_match_the_frontend_contract() -> None:
    """Guards the duplication in frontend/src/maturity.ts against drift."""
    assert maturity.BANDS == (
        (90, "Pioneering"),
        (75, "Certified"),
        (60, "Governed"),
        (40, "Managed"),
        (0, "Aware"),
    )
    assert maturity.maturity_for(100) == "Pioneering"
    assert maturity.maturity_for(89.9) == "Certified"
    assert maturity.maturity_for(0) == "Aware"


# --------------------------------------------------------------------------- #
# Opacity encoding — colour-blind safe, consistent with the history heatmap
# --------------------------------------------------------------------------- #

def test_swatch_encodes_score_as_opacity_matching_the_heatmap() -> None:
    """Same formula as HistoryView.tsx: 0.12 floor + 0.88 of the score."""
    assert report.swatch_alpha(0, True) == pytest.approx(0.12)
    assert report.swatch_alpha(100, True) == pytest.approx(1.0)
    assert report.swatch_alpha(50, True) == pytest.approx(0.56)
    assert report.swatch_alpha(42, False) == pytest.approx(0.06)


def test_swatch_opacity_is_monotonic_in_score() -> None:
    alphas = [report.swatch_alpha(s, True) for s in range(0, 101, 10)]
    assert alphas == sorted(alphas)
    assert len(set(alphas)) == len(alphas), "scores must map to distinct shades"


def test_swatch_is_monochrome_so_it_survives_greyscale() -> None:
    """Encoding by depth of one hue, not by hue, is what makes it CVD-safe."""
    shades = [report._blend_on_white(report.NAVY, report.swatch_alpha(s, True))
              for s in (0, 25, 50, 75, 100)]
    # Every shade is a scaling of the same navy vector toward white, so the
    # ordering by luminance is strict and no hue distinction carries meaning.
    lum = [0.299 * c.red + 0.587 * c.green + 0.114 * c.blue for c in shades]
    assert lum == sorted(lum, reverse=True), "darker must mean higher score"


def test_unevaluated_pillar_is_distinguishable_from_a_zero_score() -> None:
    assert report.swatch_alpha(0, True) > report.swatch_alpha(0, False)


# --------------------------------------------------------------------------- #
# 4. Findings grouped by severity, each with remediation
# --------------------------------------------------------------------------- #

def test_findings_are_grouped_under_severity_headings() -> None:
    checks = rubric.all_checks()
    failing = {
        next(c for c in checks if c.severity == "high").check_id,
        next(c for c in checks if c.severity == "medium").check_id,
        next(c for c in checks if c.severity == "low").check_id,
    }
    text = text_of(report.build_pdf(_review(findings=_findings(failing=failing))))

    for heading in ("High severity", "Medium severity", "Low severity"):
        assert heading in text, f"missing severity group: {heading}"

    # Order matters: high must precede medium, which must precede low.
    assert text.index("High severity") < text.index("Medium severity") < text.index(
        "Low severity"
    )


def test_each_open_finding_carries_its_remediation_text(pdf_text: str) -> None:
    assert "REMEDIATION" in pdf_text
    assert "Enable the control and record the decision." in pdf_text
    assert "low effort" in pdf_text


def test_open_finding_titles_and_check_ids_reach_the_document() -> None:
    high = next(c for c in rubric.all_checks() if c.severity == "high")
    text = text_of(report.build_pdf(_review(findings=_findings(failing={high.check_id}))))
    assert high.check_id in text
    assert "Not met" in text


def test_passing_and_not_applicable_counts_are_reported(pdf_text: str) -> None:
    assert "checks met" in pdf_text
    assert "not applicable" in pdf_text


def test_a_clean_design_says_so_instead_of_an_empty_section() -> None:
    text = text_of(report.build_pdf(_review(findings=_findings(failing=set()))))
    assert "No gaps found" in text
    assert "High severity" not in text


# --------------------------------------------------------------------------- #
# Summaries and delta
# --------------------------------------------------------------------------- #

def test_executive_summary_and_assessment_are_included(pdf_text: str) -> None:
    assert "Executive summary" in pdf_text
    assert "One high-severity control must close before deployment." in pdf_text
    assert "Solid shape with encryption gaps." in pdf_text


def test_re_review_delta_is_reported_when_present() -> None:
    delta = ScoreDelta(
        previous_review_id="rev-0",
        previous_overall_score=61.0,
        current_overall_score=74.5,
        change=13.5,
        resolved_checks=["sec_a", "sec_b"],
        new_checks=[],
        unchanged_failures=["ops_c"],
    )
    text = text_of(report.build_pdf(_review(delta=delta)))
    assert "Change since the previous review" in text
    assert "61.0" in text and "74.5" in text
    assert "improved" in text
    assert "2 resolved" in text


# --------------------------------------------------------------------------- #
# 5. Appendix
# --------------------------------------------------------------------------- #

def _png(width: int = 120, height: int = 80) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 37, 64)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_appendix_embeds_an_uploaded_image_diagram() -> None:
    pdf = report.build_pdf(_review(), diagram=("architecture.png", _png()))
    text = text_of(pdf)
    assert "Appendix" in text
    assert "architecture.png" in text

    # The image itself is an XObject, not text — assert it was actually embedded.
    embedded = [
        name
        for page in PdfReader(io.BytesIO(pdf)).pages
        for name in (page.images or [])
    ]
    assert embedded, "no image XObject found in the PDF"


def test_appendix_explains_a_drawio_upload_rather_than_embedding_nothing() -> None:
    text = text_of(report.build_pdf(_review(), diagram=("design.drawio", b"<mxfile/>")))
    assert "design.drawio" in text
    assert "parsed" in text.lower()
    assert "Components identified" in text
    assert "Claims database" in text


def test_appendix_states_plainly_when_no_diagram_was_retained() -> None:
    text = text_of(report.build_pdf(_review(), diagram=None))
    assert "No architecture diagram was retained" in text


def test_a_corrupt_image_degrades_instead_of_failing_the_export() -> None:
    """Render's disk is ephemeral; a truncated upload must not 500 the download."""
    pdf = report.build_pdf(_review(), diagram=("broken.png", b"\x89PNG\r\n\x1a\nnope"))
    assert pdf.startswith(b"%PDF-")
    assert "could not be embedded" in text_of(pdf)


# --------------------------------------------------------------------------- #
# Escaping — findings come from attacker-controlled uploads
# --------------------------------------------------------------------------- #

def test_markup_in_a_finding_is_escaped_not_interpreted() -> None:
    """ReportLab's Paragraph parses markup, so injected tags must not render."""
    findings = _findings()
    findings[0].status = "fail"
    findings[0].title = '<b>bold</b> & <font color="purple">purple</font>'
    findings[0].remediation = "</para><para>break out"
    findings[0].priority = 1

    pdf = report.build_pdf(_review(findings=findings))
    text = text_of(pdf)

    # The tags survive as literal characters rather than styling the document.
    assert "<b>bold</b>" in text
    assert "&" in text
    # And the injected purple never becomes an actual fill colour.
    test_no_purple_or_violet_anywhere(pdf)


def test_a_very_long_title_does_not_break_generation() -> None:
    findings = _findings()
    findings[0].status = "fail"
    findings[0].title = "overflow " * 200
    findings[0].priority = 1
    assert report.build_pdf(_review(findings=findings)).startswith(b"%PDF-")


# --------------------------------------------------------------------------- #
# How to improve — the three-phase roadmap
# --------------------------------------------------------------------------- #

def _spread_findings() -> list[Finding]:
    """One failing finding per phase, so all three appear in the report."""
    findings = _findings()
    by_id = {f.check_id: f for f in findings}
    checks = list(rubric.all_checks())

    # Immediate: cheap, high severity, one component.
    immediate = next(c for c in checks if c.severity == "high")
    f = by_id[immediate.check_id]
    f.status, f.severity = "fail", "high"
    f.remediation_effort, f.priority = "low", 1
    f.affected_components = ["claims-db"]
    f.title = "Encryption at rest is not specified"
    f.remediation = "Enable SSE-KMS on the table with a customer-managed key."

    # Short-term: a component or flow change.
    short = next(c for c in checks if c.check_id != immediate.check_id)
    f = by_id[short.check_id]
    f.status = "fail"
    f.remediation_effort, f.priority = "medium", 2
    f.affected_components = ["api-service"]
    f.title = "Secrets are passed as environment variables"
    f.remediation = "Read secrets from Secrets Manager at start-up."

    # Structural: spans components.
    structural = next(
        c for c in checks if c.check_id not in (immediate.check_id, short.check_id)
    )
    f = by_id[structural.check_id]
    f.status = "fail"
    f.remediation_effort, f.priority = "high", 3
    f.affected_components = ["claims-db", "api-service", "alb"]
    f.title = "Single-AZ deployment for the claims pipeline"
    f.remediation = "Move the claims pipeline to a multi-AZ deployment."

    return findings


def _roadmap_slice(pdf: bytes) -> str:
    """Just the roadmap section's text.

    Scoped deliberately: every title and remediation sentence in the roadmap also
    appears in the findings section further on, so an unscoped search would pass on
    that second copy and prove nothing about this section.
    """
    text = text_of(pdf)
    start = text.find("How to improve")
    assert start != -1, "roadmap section missing from the report"
    end = text.find("Findings and remediation", start)
    return text[start : end if end != -1 else len(text)]


@pytest.fixture(scope="module")
def roadmap_text() -> str:
    return _roadmap_slice(report.build_pdf(_review(findings=_spread_findings())))


def test_roadmap_section_names_all_three_phases(roadmap_text: str) -> None:
    assert "How to improve" in roadmap_text
    for label in ("Immediate", "Short-term", "Structural"):
        assert label in roadmap_text, f"{label} phase missing from the report"


def test_roadmap_reproduces_remediation_verbatim(roadmap_text: str) -> None:
    # The same no-client-rephrase rule the dashboard follows: the model's own
    # sentence reaches the page unchanged.
    for sentence in (
        "Enable SSE-KMS on the table with a customer-managed key.",
        "Read secrets from Secrets Manager at start-up.",
        "Move the claims pipeline to a multi-AZ deployment.",
    ):
        assert sentence in roadmap_text


def test_roadmap_states_the_inputs_that_decided_each_phase(roadmap_text: str) -> None:
    # Effort and component count are shown so a reviewer who disagrees with a
    # placement can see what drove it without reading the rule.
    assert "low effort" in roadmap_text
    assert "medium effort" in roadmap_text
    assert "high effort" in roadmap_text
    assert "3 components" in roadmap_text


def _phase_sections(slice_text: str) -> dict[str, str]:
    """The roadmap slice split into one text block per phase heading.

    Membership, not order, is what proves the report calls the shared rule: a second
    copy of the logic inside report.py can produce the right sequence by accident,
    but it cannot put a finding under the right heading by accident.
    """
    bounds = []
    for phase in roadmap.PHASE_ORDER:
        index = slice_text.find(roadmap.PHASE_LABEL[phase])
        assert index != -1, f"{phase} heading missing"
        bounds.append((phase, index))
    bounds.sort(key=lambda pair: pair[1])

    sections: dict[str, str] = {}
    for position, (phase, start) in enumerate(bounds):
        end = bounds[position + 1][1] if position + 1 < len(bounds) else len(slice_text)
        sections[phase] = slice_text[start:end]
    return sections


def test_roadmap_agrees_with_the_shared_grouping_rule() -> None:
    """The report must not re-derive phases; it calls the same function.

    Every finding must appear under the heading the shared rule assigns it, and
    under no other — so a drifting second implementation inside report.py fails
    here even if it happens to render the findings in the same order.
    """
    findings = _spread_findings()
    sections = _phase_sections(
        _roadmap_slice(report.build_pdf(_review(findings=findings)))
    )
    grouped = roadmap.group_by_phase(findings)

    for phase in roadmap.PHASE_ORDER:
        for finding in grouped[phase]:
            excerpt = finding.title[:40]
            assert excerpt in sections[phase], (
                f"{finding.check_id} should be under {phase}"
            )
            for other in roadmap.PHASE_ORDER:
                if other != phase:
                    assert excerpt not in sections[other], (
                        f"{finding.check_id} also appears under {other}"
                    )

    # And the headings themselves run Immediate -> Short-term -> Structural.
    order = sorted(
        roadmap.PHASE_ORDER,
        key=lambda phase: _roadmap_slice(
            report.build_pdf(_review(findings=findings))
        ).find(roadmap.PHASE_LABEL[phase]),
    )
    assert list(order) == list(roadmap.PHASE_ORDER)

def test_roadmap_is_omitted_when_nothing_is_open() -> None:
    text = text_of(report.build_pdf(_review(findings=_findings())))
    assert "How to improve" not in text


def test_roadmap_shows_an_empty_phase_rather_than_hiding_it() -> None:
    # Only a cheap high-severity gap: Short-term and Structural are empty, and both
    # still appear. "Structural (0)" is a result, not an omission.
    findings = _findings()
    high = next(c for c in rubric.all_checks() if c.severity == "high")
    target = next(f for f in findings if f.check_id == high.check_id)
    target.status, target.remediation_effort, target.priority = "fail", "low", 1
    target.affected_components = ["claims-db"]

    text = text_of(report.build_pdf(_review(findings=findings)))
    assert "Structural" in text
    assert "Nothing in this phase." in text


def test_roadmap_uses_only_existing_theme_colours(pdf: bytes) -> None:
    # The section introduces no new fill; the palette assertions elsewhere in this
    # file cover the document as a whole, and this pins the roadmap specifically by
    # rendering a review that has one.
    spread = report.build_pdf(_review(findings=_spread_findings()))
    test_no_purple_or_violet_anywhere(spread)
    fills = _rgb_fills(_content_streams(spread))
    known = [
        _rgb(h)
        for h in (
            "#1B263B", "#1420BE", "#1C55BB", "#6896F4", "#FFFFFF", "#F6F7FD",
            "#CCD2DC", "#47525E", "#EEF5FE", "#E5FAED", "#FBF8DE", "#AEBAC7",
            "#8E9DAC", "#FDC921",
        )
    ]
    for fill in fills:
        # Navy at partial opacity is blended to a real RGB by the swatch ramp, so a
        # fill is acceptable if it is a known token OR a grey-blue on that ramp.
        if any(_near(fill, k) for k in known):
            continue
        r, g, b = fill
        assert b >= r, f"unexpected fill {fill} is not on the navy ramp"


# --------------------------------------------------------------------------- #
# Download filename
# --------------------------------------------------------------------------- #

def test_filename_is_derived_from_the_title_and_is_safe() -> None:
    assert report.filename_for(_review()) == "trust7-synthetic-expense-portal.pdf"


@pytest.mark.parametrize(
    "title",
    ["../../etc/passwd", 'quote"and;semi', "  ", "a/b\\c", "Ünïcödé design"],
)
def test_filename_never_contains_path_or_quote_characters(title: str) -> None:
    name = report.filename_for(_review(title=title))
    assert name.startswith("trust7-") and name.endswith(".pdf")
    for bad in ("/", "\\", "..", '"', ";", " "):
        assert bad not in name, f"{bad!r} survived in {name!r}"
