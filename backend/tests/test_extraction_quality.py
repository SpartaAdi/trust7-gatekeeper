"""Low-confidence extraction warnings — the silent failure made visible.

The hard failures already raise: `documents.extract_text` refuses a PDF with no
text, `drawio.parse` refuses a file with no `<mxGraphModel>`, `normalize.ingest`
refuses an upload where both surfaces are empty. What none of them catches is the
PARTIAL case, and the partial case is indistinguishable from success — a 40-page
PDF with a text cover sheet and 39 scans produces a real score on a real heatmap
with nothing to say the design was mostly never read.

So these tests come in pairs. For each signal: it fires when extraction genuinely
fell short, and it stays silent on a legitimately terse design. The second half of
each pair is the more important one — a warning that fires on ordinary uploads
teaches reviewers to ignore the banner, and an ignored warning is worse than none.
"""

from __future__ import annotations

import importlib
import io

import pytest
from fastapi.testclient import TestClient

import config
import llm
import rubric
from ingestion import documents, quality
from schema import Component, Connection, DesignGraph, DiagramSource

DEMO_TOKEN = "quality-warning-token"

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _graph(components: int, connections: int = 0, notes: int = 0) -> DesignGraph:
    return DesignGraph(
        components=[
            Component(id=f"c{i}", label=f"Component {i}") for i in range(components)
        ],
        connections=[
            Connection(source_id=f"c{i}", target_id=f"c{i}") for i in range(connections)
        ],
        notes=[f"note {i}" for i in range(notes)],
        source=DiagramSource.IMAGE,
    )


# --------------------------------------------------------------------------- #
# Diagram images: near-empty relative to the file
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("components", [0, 1])
def test_a_large_image_yielding_almost_nothing_warns(components) -> None:
    warning = quality.image_extraction(_graph(components), 3_000_000, "screenshot.png")

    assert warning is not None
    assert warning.code == "diagram_near_empty"
    # The numbers are in `detail` so a reviewer can weigh the warning rather than
    # take it on trust.
    assert "3000000 bytes" in warning.detail
    # And the message says what to do instead.
    assert ".drawio" in warning.message


def test_a_small_image_is_not_judged_at_all() -> None:
    """An icon or a one-box crop yielding one component is expected, not suspicious.
    Judging it would fire the warning on legitimate uploads."""
    assert quality.image_extraction(_graph(1), 12_000, "icon.png") is None


def test_a_large_image_that_transcribed_properly_does_not_warn() -> None:
    assert quality.image_extraction(_graph(9, connections=11), 3_000_000, "arch.png") is None


def test_the_singular_is_used_for_one_component() -> None:
    """Prose detail, and it is read by the person deciding whether to re-upload."""
    warning = quality.image_extraction(_graph(1), 500_000, "a.png")

    assert warning is not None
    assert "1 component were" not in warning.message
    assert "1 component was" in warning.message


# --------------------------------------------------------------------------- #
# Diagram images: the model's own legibility report
# --------------------------------------------------------------------------- #

def test_a_low_confidence_transcription_warns_even_with_plenty_of_components() -> None:
    """The signal component-counting cannot see.

    The model can return a plausible eight-component graph and still report that
    half the labels were guesses. Only its own report catches that, which is why
    `ingestion/vision.py` asks for it.
    """
    warning = quality.vision_confidence(_graph(8), "low", [], "blurry.png")

    assert warning is not None
    assert warning.code == "vision_low_confidence"
    assert "confidence=low" in warning.detail


def test_a_high_confidence_transcription_does_not_warn() -> None:
    assert quality.vision_confidence(_graph(8), "high", [], "clear.png") is None


def test_medium_confidence_alone_is_not_enough_to_warn() -> None:
    """Almost no real diagram is perfectly legible. Warning on every `medium` is how
    a banner becomes wallpaper."""
    assert quality.vision_confidence(_graph(8), "medium", [], "ok.png") is None


def test_medium_confidence_with_named_illegible_regions_does_warn() -> None:
    """`medium` plus something specific it could not read is a real report."""
    warning = quality.vision_confidence(
        _graph(8), "medium", ["all arrow labels", "the bottom-right legend"], "part.png"
    )

    assert warning is not None
    assert "all arrow labels" in warning.detail


def test_an_unreported_confidence_is_not_treated_as_low() -> None:
    """Silence is not a low-confidence report.

    OpenRouter documents schema enforcement as varying by provider, so the field can
    simply be absent. Warning on that would fire on every provider that drops it.
    """
    assert quality.vision_confidence(_graph(8), "", [], "a.png") is None


def test_vision_reports_its_confidence_out_of_the_parser(monkeypatch) -> None:
    """The wiring: the model's report has to leave `vision.parse` to be usable."""
    from ingestion import vision

    monkeypatch.setattr(
        llm,
        "complete_json",
        lambda **kwargs: (
            {
                "components": [
                    {"id": "a", "label": "A", "kind": "compute",
                     "provider": "aws", "service": ""}
                ],
                "connections": [],
                "notes": [],
                "extraction_confidence": "low",
                "illegible": ["the label under the gateway"],
            },
            {},
        ),
    )

    graph, _, confidence, illegible = vision.parse(b"fake", "image/png")

    assert confidence == "low"
    assert illegible == ["the label under the gateway"]
    # And the two report fields must NOT leak into the common schema both diagram
    # paths converge on — the draw.io path has no equivalent to report.
    assert not hasattr(graph, "extraction_confidence")
    assert len(graph.components) == 1


def test_an_off_enum_confidence_value_is_ignored_rather_than_raising(monkeypatch) -> None:
    from ingestion import vision

    monkeypatch.setattr(
        llm, "complete_json",
        lambda **kwargs: (
            {"components": [], "connections": [], "notes": [],
             "extraction_confidence": "quite good, thanks"},
            {},
        ),
    )

    assert vision.parse(b"fake", "image/png")[2] == ""


# --------------------------------------------------------------------------- #
# draw.io: shapes in the file vs components out of the parser
# --------------------------------------------------------------------------- #

def _drawio(labelled: int, unlabelled: int = 0) -> bytes:
    cells = "".join(
        f'<mxCell id="c{i}" value="Service {i}" vertex="1" parent="1"/>'
        for i in range(labelled)
    ) + "".join(
        f'<mxCell id="u{i}" vertex="1" parent="1"/>' for i in range(unlabelled)
    )
    return f"<mxfile><diagram><mxGraphModel><root>{cells}</root></mxGraphModel></diagram></mxfile>".encode()


def test_a_drawio_file_whose_shapes_mostly_failed_to_parse_warns() -> None:
    """If this fires the likely cause is our parser, not the user — but the
    consequence lands on the reviewer either way: a design scored on a fraction of
    its diagram."""
    warning = quality.drawio_extraction(_graph(2), _drawio(30), "design.drawio")

    assert warning is not None
    assert warning.code == "drawio_mostly_unparsed"
    assert "yield" in warning.detail


def test_a_drawio_file_that_parsed_cleanly_does_not_warn() -> None:
    assert quality.drawio_extraction(_graph(28), _drawio(30), "design.drawio") is None


def test_unlabelled_shapes_are_not_counted_against_the_parser() -> None:
    """`drawio.parse` drops unlabelled shapes deliberately — a real diagram is full
    of arrows, containers and decoration carrying no reviewable meaning. Measuring
    against every vertex would warn on every well-drawn diagram in existence."""
    assert quality.drawio_extraction(
        _graph(10), _drawio(labelled=10, unlabelled=200), "design.drawio"
    ) is None


def test_annotations_that_became_notes_count_as_parsed() -> None:
    """A shape that became a note was understood, just not as a component."""
    assert quality.drawio_extraction(
        _graph(5, notes=10), _drawio(12), "design.drawio"
    ) is None


def test_a_small_drawio_file_is_not_judged() -> None:
    assert quality.drawio_extraction(_graph(1), _drawio(4), "tiny.drawio") is None


def test_a_compressed_export_declines_to_judge_rather_than_guessing() -> None:
    """The shape count would need the payload inflated a second time, and a wrong
    count is worse than no check."""
    compressed = b"<mxfile><diagram>7VvbcuI4EP0aP-4Ujg==</diagram></mxfile>"

    assert quality.drawio_extraction(_graph(0), compressed, "c.drawio") is None


def test_object_wrapped_labels_are_counted() -> None:
    """draw.io puts the label on the `<object>` wrapper and leaves the inner
    mxCell's `value` empty, so counting only mxCell values would see zero."""
    body = b"<mxfile><diagram><mxGraphModel><root>" + b"".join(
        f'<object label="Service {i}" id="o{i}"><mxCell vertex="1"/></object>'.encode()
        for i in range(20)
    ) + b"</root></mxGraphModel></diagram></mxfile>"

    assert quality._labelled_shape_count(body) == 20


# --------------------------------------------------------------------------- #
# Documents: text per page
# --------------------------------------------------------------------------- #

def _pdf_text(pages: dict[int, int]) -> str:
    """Rebuild what `documents._pdf_text` produces: `[page N]` for readable pages."""
    return "\n\n".join(f"[page {n}]\n{'x' * chars}" for n, chars in sorted(pages.items()))


def _real_pdf(text_pages: int, blank_pages: int) -> bytes:
    """A genuine PDF: `text_pages` with selectable text, then `blank_pages` without.

    Built with reportlab rather than hand-assembled, because the bug this file now
    guards was invisible to a hand-written `[page N]` string. A reconstructed text
    fixture can only express pages that produced text, which is precisely the
    information the check must NOT rely on.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    for page in range(text_pages):
        pdf.setFont("Helvetica", 10)
        # A realistic page of prose, not one line: MIN_CHARS_PER_PAGE is about two
        # lines, so a one-line-per-page fixture would be legitimately sparse and
        # would test the threshold against itself rather than against a real document.
        for line in range(40):
            pdf.drawString(
                50, 790 - line * 18,
                f"Page {page + 1} line {line + 1}: the claims platform ingests "
                f"policy documents, extracts fields and routes them for review.",
            )
        pdf.showPage()
    for _ in range(blank_pages):
        # A drawn rectangle and no text: what a scanned page looks like to pypdf.
        pdf.rect(60, 200, 480, 560, stroke=1, fill=0)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_a_mostly_scanned_pdf_warns_when_only_its_cover_page_has_text() -> None:
    """A REGRESSION TEST, and the reason this fixture is a real PDF.

    The first implementation read the document's page count off the highest
    `[page N]` marker in the extracted text. Those markers exist only for pages that
    produced text — so this exact upload, 1 text page in front of 39 scans, looked
    like a 1-page document, fell under MIN_PAGES_TO_JUDGE, and passed with no
    warning. The check missed the only case it was written for, and every test at the
    time passed, because they all built the text fixture by hand and so could not
    express a page that produced nothing.
    """
    data = _real_pdf(text_pages=1, blank_pages=39)
    text = documents.extract_text(data, "scanned-sow.pdf")
    total = documents.page_count(data, "scanned-sow.pdf")

    # Extraction SUCCEEDED — this is the silent case, not an error case.
    assert text and total == 40

    warning = quality.document_extraction(text, "scanned-sow.pdf", total)

    assert warning is not None, "a 40-page PDF with one text page must warn"
    assert warning.code == "document_sparse_text"
    assert "40 pages" in warning.message
    assert "39 pages yielding" in warning.message
    assert "1 with text" in warning.detail
    assert "OCR is not supported" in warning.message


def test_the_page_count_cannot_be_derived_from_the_extracted_text() -> None:
    """The root cause, pinned directly.

    If these two ever agree for a partially-scanned PDF, the fix has been undone and
    the check is reading the readable-page count as the page count again.
    """
    import re

    data = _real_pdf(text_pages=1, blank_pages=39)
    text = documents.extract_text(data, "scanned.pdf")

    highest_marker = max(int(n) for n in re.findall(r"^\[page (\d+)\]$", text, re.M))

    assert highest_marker == 1
    assert documents.page_count(data, "scanned.pdf") == 40


def test_a_real_multi_page_text_pdf_does_not_warn() -> None:
    """The other half: a genuine text document must produce no banner."""
    data = _real_pdf(text_pages=12, blank_pages=0)

    assert quality.document_extraction(
        documents.extract_text(data, "sow.pdf"), "sow.pdf",
        documents.page_count(data, "sow.pdf"),
    ) is None


def test_page_count_is_zero_for_a_non_pdf() -> None:
    """0 means "no page structure to compare against", and the check declines."""
    assert documents.page_count(b"# A design\n", "sow.md") == 0
    assert documents.page_count(b"not a pdf at all", "broken.pdf") == 0


def test_a_normal_text_pdf_does_not_warn() -> None:
    assert quality.document_extraction(
        _pdf_text({n: 2_400 for n in range(1, 13)}), "sow.pdf", 12
    ) is None


def test_a_document_with_a_few_image_only_pages_does_not_warn() -> None:
    """A cover, a divider, an appendix screenshot. Only warn once most of the
    document is missing."""
    pages = {n: 2_000 for n in range(1, 11)}
    del pages[4]
    del pages[7]

    assert quality.document_extraction(_pdf_text(pages), "sow.pdf", 10) is None


def test_a_short_document_is_not_judged_on_its_average() -> None:
    """A one- or two-page diagram export with a caption is a legitimate upload."""
    assert quality.document_extraction(_pdf_text({1: 40, 2: 30}), "note.pdf", 2) is None


def test_a_non_pdf_document_is_not_judged() -> None:
    """No `[page N]` markers means no page structure to measure against — a .md or
    .docx has no pages, and inventing a threshold for one would be arbitrary."""
    assert quality.document_extraction(
        "# A design\n\nShort but deliberate.", "sow.md", 0
    ) is None


def test_an_unavailable_page_count_declines_to_judge() -> None:
    """`0` is "we could not count", not "zero pages". Judging on it would divide by
    zero, and guessing a count would invent the signal."""
    assert quality.document_extraction(_pdf_text({1: 10, 2: 10}), "sow.pdf", 0) is None


def test_the_page_markers_themselves_do_not_pad_the_character_count() -> None:
    """`[page 12]` is 9 characters of our own making. Counting them as document text
    would let a 40-page scan with no content clear the threshold on markers alone."""
    text = _pdf_text({n: 1 for n in range(1, 41)})

    warning = quality.document_extraction(text, "scan.pdf", 40)

    assert warning is not None, "40 pages of one character each must warn"


# --------------------------------------------------------------------------- #
# End to end, through the real routes
# --------------------------------------------------------------------------- #

def _pipeline_stub():
    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        required = set(schema.get("required", []))
        if "verdict" in required:
            return {"verdict": "reviewable", "subject": "a design",
                    "reason": "r", "confidence": "high"}, {}
        if "components" in required and "connections" in required:
            # The vision call: one component, and the model says it could barely read
            # the image.
            return {
                "components": [{"id": "a", "label": "Something", "kind": "unknown",
                                "provider": "unknown", "service": ""}],
                "connections": [], "notes": [],
                "extraction_confidence": "low",
                "illegible": ["essentially all of it"],
            }, {}
        if "design_summary" in required:
            # Non-empty on purpose — see agent/pipeline.py's zero-component gate.
            # An empty inventory from both sources rejects the review, which would
            # stop these extraction-warning tests before they can assert anything.
            return {"design_summary": "x",
                    "components": [{"id": "c0", "label": "Component 0",
                                    "kind": "compute", "provider": "aws",
                                    "service": "Amazon EC2", "attributes": []}],
                    "data_flows": [],
                    "observations": [], "absent": []}, {}
        if "findings" in required:
            return {"findings": [
                {"check_id": c.check_id, "status": "fail", "severity": c.severity,
                 "severity_rationale": "s", "title": c.description, "evidence": "e",
                 "affected_components": []}
                for c in rubric.all_checks()
            ]}, {}
        if "ranking" in required:
            return {"summary": "- s", "ranking": []}, {}
        return {"executive_summary": "s", "remediations": [], "use_case_notes": []}, {}
    return fake


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main
    import storage

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    monkeypatch.setattr(llm, "complete_json", _pipeline_stub())
    return TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})


def test_a_barely_readable_diagram_completes_the_review_carrying_warnings(client) -> None:
    """The requirement, end to end: a warning STATE, not a silent score.

    A 500 KB PNG yielding one component trips two independent signals — the
    near-empty check and the model's own report — and both must survive to the
    stored result rather than being logged and forgotten.
    """
    big_png = PNG_HEADER + b"\x00" * 500_000
    key = client.post(
        "/uploads", files={"file": ("screenshot.png", big_png, "image/png")}
    ).json()["key"]

    review_id = client.post("/reviews", json={"diagram_key": key}).json()["review_id"]

    status = client.get(f"/reviews/{review_id}/status").json()
    assert status["state"] == "complete", "a warning must not block the review"
    codes = {w["code"] for w in status["warnings"]}
    assert codes == {"diagram_near_empty", "vision_low_confidence"}

    # On the STORED result too. The status file is transient; a reviewer opening this
    # review tomorrow must still see that its diagram was unreadable.
    result = client.get(f"/reviews/{review_id}").json()
    assert {w["code"] for w in result["warnings"]} == codes
    assert result["overall_score"] is not None, "the review still produced a score"


def test_a_clean_upload_carries_no_warnings(client) -> None:
    """The other half: no banner on an ordinary submission."""
    sow = b"# Payments platform\n\n" + b"A managed API in front of a datastore. " * 60
    key = client.post(
        "/uploads", files={"file": ("sow.md", sow, "text/markdown")}
    ).json()["key"]

    review_id = client.post("/reviews", json={"document_key": key}).json()["review_id"]

    assert client.get(f"/reviews/{review_id}/status").json()["warnings"] == []
    assert client.get(f"/reviews/{review_id}").json()["warnings"] == []


def test_warnings_appear_on_the_status_before_the_review_finishes(
    client, monkeypatch
) -> None:
    """Publishing them onto the status is the whole point of doing it during ingest:
    a reviewer who learns NOW can stop the run and upload a better copy.

    Asserted by capturing the status writes as they happen, since in-process the
    background task completes before the POST returns.
    """
    import storage

    seen: list[tuple[str, list[str]]] = []
    real_put = storage.put_status

    def capture(status):
        seen.append((status.state, [w.code for w in status.warnings]))
        real_put(status)

    monkeypatch.setattr(storage, "put_status", capture)

    big_png = PNG_HEADER + b"\x00" * 500_000
    key = client.post(
        "/uploads", files={"file": ("s.png", big_png, "image/png")}
    ).json()["key"]
    client.post("/reviews", json={"diagram_key": key})

    # A write carrying warnings while still `running`, i.e. before the terminal one.
    running_with_warnings = [
        codes for state, codes in seen if state == "running" and codes
    ]
    assert running_with_warnings, (
        f"warnings only ever appeared on a terminal status: {seen}"
    )
    assert "diagram_near_empty" in running_with_warnings[0]
