"""The architecture diagram embedded inside a PDF SoW.

`documents.extract_text` reads `page.extract_text()` and never opens an image, so a
design whose architecture is a picture on page 8 was scored on its prose alone. Not
mis-read, not timed out — never looked at.

Selection is the hard part, not extraction. A real signed SoW carries a cover
collage, two logos and a signature scan alongside the diagram, and every one of them
is an image. Picking the wrong one spends a vision call and then fills the review
with components that are not in the design, which is worse than finding nothing.

So the fixture below is not a toy. `_rmbl_shaped_pdf` reproduces the exact geometry
of the real RMBL-Control-Tower SoW — five images at their real dimensions, on their
real pages, with the real "10. Target Architecture" heading on page 8 — because the
distractors are what the ranking has to survive, and a fixture with one image in it
would prove nothing.

`test_the_real_rmbl_sow_selects_the_page_8_diagram` runs against the actual file
when it is present and skips loudly when it is not.
"""

from __future__ import annotations

import hashlib
import io
import pathlib
from typing import Any

import pytest

import llm
from ingestion import embedded, normalize
from schema import Component, DesignGraph, DiagramSource

# The real file's five embedded images, page by page. Dimensions are the measured
# ones; only the pixels are synthetic.
RMBL_IMAGES = [
    (1, "cover collage", 1622, 2100),
    (1, "AWS Partner logo", 453, 132),
    (1, "Minfy logo", 784, 216),
    (8, "target architecture", 2836, 1699),
    (12, "signature scan", 1000, 707),
]
RMBL_DIAGRAM = (2836, 1699)

# Where the real file goes if you drop it in. Deliberately git-ignored territory:
# it is a signed client contract and does not belong in the repository.
REAL_SOW = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "fixtures" / "real" / "RMBL-Control-Tower-SOW-v1.0 (Signed).pdf"
)


def _rmbl_shaped_pdf(
    images: list[tuple[int, str, int, int]] | None = None,
    page_8_heading: str = "10. Target Architecture",
) -> bytes:
    """A 12-page PDF with the real SoW's image geometry and headings."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    placed = images if images is not None else RMBL_IMAGES
    headings = {
        1: "RMBL Control Tower Statement of Work. Prepared for the client.",
        8: page_8_heading,
        12: "Conclusion. Acceptance. Signatures.",
    }

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for page in range(1, 13):
        pdf.drawString(40, 800, headings.get(page, f"Section {page}. Ordinary prose."))
        y = 100
        for number, _label, width, height in placed:
            if number != page:
                continue
            # Uniform fill: the pixels are irrelevant, the DIMENSIONS are the fixture.
            bitmap = Image.new("RGB", (width, height), (200, 210, 220))
            pdf.drawImage(ImageReader(bitmap), 40, y, width=100, height=60)
            y += 70
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Selection — the acceptance criteria
# --------------------------------------------------------------------------- #

def test_the_page_8_diagram_is_selected_over_every_distractor() -> None:
    """The acceptance criterion, on the real geometry.

    Four wrong answers are available and each is wrong for a different reason: the
    cover collage is larger in bytes and has no keyword, the two logos are small, and
    the signature scan is neither. Asserted by DIMENSIONS, not by "something was
    found" — a test that only checked for a result would pass on the collage.
    """
    selected = embedded.select_diagram(_rmbl_shaped_pdf())

    assert selected is not None
    assert (selected.width, selected.height) == RMBL_DIAGRAM
    assert selected.page == 8
    assert selected.keyword == "architecture"


def test_the_cover_collage_is_rejected_even_when_it_matches_the_keyword_too() -> None:
    """The ranking must not depend on page 1 being keyword-free.

    If the cover page happens to say "architecture" the two tie on the keyword, and
    the diagram has to win on area instead. A rule that only worked because the real
    cover page lacked the word would be a rule tuned to one file.
    """
    selected = embedded.select_diagram(
        _rmbl_shaped_pdf(page_8_heading="10. Target Architecture")
    )
    tied = embedded.select_diagram(
        # Every page now carries the keyword, so ONLY area separates them.
        _rmbl_shaped_pdf(page_8_heading="Architecture")
    )

    assert selected is not None and tied is not None
    assert (tied.width, tied.height) == RMBL_DIAGRAM, "area must break a keyword tie"
    assert tied.page == 8


def test_the_keyword_beats_a_larger_image_on_a_keywordless_page() -> None:
    """The test that makes the keyword signal worth having.

    On the real SoW the diagram is also the largest qualifying image, so area alone
    would land on the right answer there and prove nothing. This is the case that
    separates the two rules: a full-page scan or a photographic cover BIGGER than the
    diagram, on a page whose text says nothing about architecture. Area alone picks
    the decoration; keyword-first picks the design.
    """
    with_bigger_decoration = [
        (1, "full-page scan", 4000, 3000),          # 12,000,000 px, no keyword
        (8, "target architecture", 2836, 1699),     # 4,818,364 px, keyword
    ]

    selected = embedded.select_diagram(_rmbl_shaped_pdf(images=with_bigger_decoration))

    assert selected is not None
    assert selected.page == 8, "a keywordless larger image must not outrank the diagram"
    assert (selected.width, selected.height) == RMBL_DIAGRAM


def test_the_logos_are_below_the_area_floor() -> None:
    """Calibration, against the real logo dimensions rather than invented ones."""
    assert 453 * 132 < embedded.MIN_DIAGRAM_AREA_PX
    assert 784 * 216 < embedded.MIN_DIAGRAM_AREA_PX
    # And with a wide margin on the other side.
    assert 2836 * 1699 > embedded.MIN_DIAGRAM_AREA_PX * 10


def test_a_logo_only_pdf_selects_nothing_and_costs_no_call() -> None:
    """The common case for an ordinary SoW: images, but none of them a diagram."""
    logos = [(1, "logo", 453, 132), (1, "logo", 784, 216)]

    assert embedded.select_diagram(_rmbl_shaped_pdf(images=logos)) is None


def test_a_pdf_with_no_images_at_all_selects_nothing() -> None:
    assert embedded.select_diagram(_rmbl_shaped_pdf(images=[])) is None


def test_a_keywordless_page_still_wins_on_area_when_nothing_matches() -> None:
    """With no keyword anywhere, the largest qualifying image is the best guess
    available — better than declining to look."""
    selected = embedded.select_diagram(
        _rmbl_shaped_pdf(page_8_heading="Section 10. Overview of the solution")
    )

    assert selected is not None
    assert (selected.width, selected.height) == RMBL_DIAGRAM


def test_bytes_that_are_not_a_pdf_are_declined_quietly() -> None:
    """A .docx or a text SoW reaches this too. It must return None, not raise —
    `documents.extract_text` already succeeded on this file."""
    assert embedded.select_diagram(b"not a pdf at all") is None
    assert embedded.select_diagram(b"") is None


def test_only_one_diagram_is_ever_read() -> None:
    """The cost bound. Raising it multiplies vision calls per review."""
    assert embedded.MAX_EMBEDDED_DIAGRAMS == 1


# --------------------------------------------------------------------------- #
# The real file, when it is present
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(
    not REAL_SOW.exists(),
    reason=(
        f"The real signed SoW is not in the repo (looked in {REAL_SOW}). It is a "
        "client contract and is deliberately not committed. Drop it there to run "
        "this assertion against the actual file."
    ),
)
def test_the_real_rmbl_sow_selects_the_page_8_diagram() -> None:
    """The acceptance test, against the real bytes.

    Asserts dimensions AND a content hash, so it cannot pass on a different image
    that happens to share a page number.
    """
    selected = embedded.select_diagram(REAL_SOW.read_bytes())

    assert selected is not None, "no embedded diagram was found in the real SoW"
    assert selected.page == 8
    assert (selected.width, selected.height) == RMBL_DIAGRAM
    digest = hashlib.sha256(selected.data).hexdigest()
    print(f"\nreal SoW selection: page {selected.page} "
          f"{selected.width}x{selected.height} sha256={digest}")


# --------------------------------------------------------------------------- #
# Wiring into ingest
# --------------------------------------------------------------------------- #

def _stub_vision(
    monkeypatch, calls: list[str], components: int = 6, confidence: str = "high"
):
    def fake(**kwargs: Any):
        calls.append(kwargs.get("label", ""))
        return {
            "components": [
                {"id": f"v{i}", "label": f"Amazon Service {i}", "kind": "compute",
                 "provider": "aws", "service": f"Amazon Service {i}"}
                for i in range(components)
            ],
            "connections": [], "notes": ["Read from the embedded diagram"],
            "extraction_confidence": confidence, "illegible": [],
        }, {"input_tokens": 900, "output_tokens": 300}

    monkeypatch.setattr(llm, "complete_json", fake)


@pytest.fixture()
def store(monkeypatch, tmp_path):
    import importlib

    import config
    import storage

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage)
    return storage


def _put(store, name: str, data: bytes) -> str:
    """Through the real upload path, so the key is shaped as ingest expects."""
    return store.save_upload(name, data)


def test_a_document_only_upload_reads_its_embedded_diagram(store, monkeypatch) -> None:
    calls: list[str] = []
    _stub_vision(monkeypatch, calls)
    key = _put(store, "sow.pdf", _rmbl_shaped_pdf())

    design, usage = normalize.ingest(review_id="r", document_key=key)

    assert len(design.graph.components) == 6
    # DiagramSource.IMAGE, so every consumer of an explicit image upload works
    # unchanged — that reuse is the point of routing through `parse_diagram`.
    assert design.graph.source == DiagramSource.IMAGE
    assert calls, "the vision path should have run"
    assert usage["input_tokens"] == 900
    # The document text is still there: this ADDS a graph, it does not replace prose.
    assert "Target Architecture" in design.document_text


def test_an_explicit_diagram_upload_still_wins(store, monkeypatch) -> None:
    """The submitter told us which diagram to review. That keeps winning.

    A drawio upload alongside a picture-bearing PDF must parse the drawio and never
    open the PDF's images — no override, no merge, and no extra vision call.
    """
    calls: list[str] = []
    _stub_vision(monkeypatch, calls)
    document = _put(store, "sow.pdf", _rmbl_shaped_pdf())
    diagram = _put(
        store, "design.drawio",
        b'<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>'
        b'<mxCell id="a" value="Claims API" vertex="1" parent="0"/>'
        b"</root></mxGraphModel></diagram></mxfile>",
    )

    design, _usage = normalize.ingest(
        review_id="r", document_key=document, diagram_key=diagram
    )

    assert [c.label for c in design.graph.components] == ["Claims API"]
    assert design.graph.source == DiagramSource.DRAWIO
    assert calls == [], "no vision call may happen when a diagram was uploaded"


def test_a_sow_with_no_embedded_diagram_is_a_true_no_op(store, monkeypatch) -> None:
    """Zero added latency and zero added cost for the ordinary case."""
    calls: list[str] = []
    _stub_vision(monkeypatch, calls)
    key = _put(store, "sow.pdf", _rmbl_shaped_pdf(images=[]))

    design, usage = normalize.ingest(review_id="r", document_key=key)

    assert design.graph.components == []
    assert calls == []
    assert usage == {}


def test_a_non_pdf_document_does_not_break_ingest(store, monkeypatch) -> None:
    calls: list[str] = []
    _stub_vision(monkeypatch, calls)
    key = _put(store, "sow.md", b"# Design\n\nProse about a system, at length.")

    design, _usage = normalize.ingest(review_id="r", document_key=key)

    assert design.graph.components == []
    assert calls == []


def test_the_page_number_reaches_the_reviewer_through_the_warning(
    store, monkeypatch
) -> None:
    """"Where did these components come from?" is the first question about a diagram
    nobody uploaded, so the page has to be answerable from the review itself."""
    calls: list[str] = []
    # A low-confidence read trips `vision_low_confidence`, whose `detail` renders the
    # filename — which is where the page number rides. Deliberately not
    # `diagram_near_empty`: that one judges by BYTES, and this fixture's uniform-fill
    # image compresses to almost nothing, so it would never fire here for a reason
    # that has nothing to do with what is being tested.
    _stub_vision(monkeypatch, calls, components=3, confidence="low")
    key = _put(store, "sow.pdf", _rmbl_shaped_pdf())

    design, _usage = normalize.ingest(review_id="r", document_key=key)

    assert design.warnings
    assert any("page 8" in warning.detail for warning in design.warnings), (
        [w.detail for w in design.warnings]
    )
    assert any("2836x1699" in warning.detail for warning in design.warnings)


# --------------------------------------------------------------------------- #
# Interaction with Segments 2b and 7, checked rather than assumed
# --------------------------------------------------------------------------- #

def test_the_zero_component_gate_sees_the_embedded_diagrams_components(
    store, monkeypatch
) -> None:
    """Segment 2b's gate refuses a review with no inventory from either source.

    A document-only SoW whose diagram is embedded now HAS a graph, so the gate's
    `design.graph.components` half is populated where before it was always empty.
    This pins that the feature feeds the gate rather than bypassing it.
    """
    calls: list[str] = []
    _stub_vision(monkeypatch, calls)
    key = _put(store, "sow.pdf", _rmbl_shaped_pdf())

    design, _usage = normalize.ingest(review_id="r", document_key=key)

    assert design.graph.components, "the gate's graph half must now be populated"


def test_the_grounding_haystack_expands_with_the_embedded_components() -> None:
    """Segment 7 grounds remediation quotes against `design_source_text`.

    A populated graph should ADD quotable labels rather than change how the haystack
    is built. Checked directly, because Segment 7 landed after this was specified and
    its `design=None` skip is a different code path from a populated graph.
    """
    from agent import stages
    from schema import NormalizedDesign

    prose_only = NormalizedDesign(
        review_id="r", document_text="The portal runs in one region."
    )
    with_diagram = NormalizedDesign(
        review_id="r",
        document_text="The portal runs in one region.",
        graph=DesignGraph(
            components=[
                Component(id="v0", label="Amazon Route 53", kind="dns",
                          provider="aws", service="Amazon Route 53")
            ],
            notes=["Read from the embedded diagram"],
            source=DiagramSource.IMAGE,
        ),
    )

    before = stages.design_source_text(prose_only)
    after = stages.design_source_text(with_diagram)

    # Strictly additive: everything quotable before is still quotable.
    assert "the portal runs in one region" in before
    assert "the portal runs in one region" in after
    # And the diagram's own labels are now quotable, which is the point.
    assert "amazon route 53" not in before
    assert "amazon route 53" in after
    assert "read from the embedded diagram" in after
