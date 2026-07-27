"""PDF export tests.

Structure and content, not pixels: each test asserts that a fact from the review
reached the document, or that a structural rule holds. Rendering fidelity is not
something a unit test can judge, and asserting on byte offsets would break on
every ReportLab upgrade for no benefit.

Text is read back with pypdf, which is already a dependency for SoW ingestion.
"""

from __future__ import annotations

import io
import zlib

import pytest
from pypdf import PdfReader

import maturity
import report
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
# 2. Branding: navy, orange, and no purple anywhere
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


def test_cover_paints_the_minfy_navy_background_and_orange_accent(pdf: bytes) -> None:
    fills = _rgb_fills(_content_streams(pdf))
    assert fills, "no colour operators found"

    def near(actual, expected, tol=0.02) -> bool:
        return all(abs(a - e) <= tol for a, e in zip(actual, expected))

    navy = (0x0A / 255, 0x25 / 255, 0x40 / 255)
    orange = (0xE8 / 255, 0x5D / 255, 0x26 / 255)
    assert any(near(f, navy) for f in fills), "Minfy navy #0A2540 not used"
    assert any(near(f, orange) for f in fills), "Minfy orange #E85D26 not used"


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
