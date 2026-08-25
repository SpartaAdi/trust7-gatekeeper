"""Did we actually read the design? Deterministic checks on what extraction produced.

Every check here compares what came out of extraction against what went in, and
raises an `IngestWarning` when the two are too far apart. None of them stops a
review — see the note on `IngestWarning` in schema.py — because a partially-read
design is still worth reviewing. It is only not worth presenting as though it were
complete.

The failure being caught is silent by construction. The hard failures already
raise: `documents.extract_text` refuses a PDF with no text at all,
`drawio.parse` refuses a file with no `<mxGraphModel>`, and `normalize.ingest`
refuses an upload where document and diagram are both empty. What none of them
catches is the PARTIAL case, and the partial case looks identical to success:

* a 40-page PDF whose first page is a text cover sheet and whose other 39 are
  scans — extraction returns the cover page, and 45 checks get scored against it;
* a 3 MB architecture screenshot the vision model reads two boxes out of;
* a draw.io export with 60 shapes of which 3 carry labels, because the author put
  the text in a legend image.

All three produce a real score, on a real heatmap, with no indication that the
design was mostly never seen. That is worse than an error, because an error is
visible.

## Thresholds

Every threshold here is a deliberate underestimate, and the reason is asymmetric
cost: a missed warning leaves a reviewer where they already are today, while a
false warning on a legitimately terse design teaches them to ignore the banner —
and a warning nobody reads is worse than none. So each one fires only where the
gap is not arguable, and each carries its numbers in `detail` so a reviewer can
judge rather than trust.
"""

from __future__ import annotations

import re

from schema import DesignGraph, IngestWarning

# --------------------------------------------------------------------------- #
# Diagram images
# --------------------------------------------------------------------------- #

# Below this, an image is too small to be a real architecture diagram and a sparse
# result from it is expected rather than suspicious — an icon, a logo, a cropped
# screenshot of one box.
MIN_IMAGE_BYTES_TO_JUDGE = 60_000

# At or below this many components, an image that large did not transcribe.
#
# 2 rather than a proportion of file size: there is no reliable relationship
# between the byte size of a PNG and how many boxes it contains — a screenshot's
# size is driven by resolution and colour depth, not content. What IS reliable is
# that a 60 KB+ image yielding zero or one component was not read, whatever it
# depicted. A photograph of a cat lands here too, which is the intended overlap
# with the relevance gate: two independent signals on the same upload.
MAX_COMPONENTS_FOR_NEAR_EMPTY = 1


def image_extraction(
    graph: DesignGraph, image_bytes: int, filename: str
) -> IngestWarning | None:
    """Warn when a substantial image yielded almost no graph."""
    if image_bytes < MIN_IMAGE_BYTES_TO_JUDGE:
        return None
    if len(graph.components) > MAX_COMPONENTS_FOR_NEAR_EMPTY:
        return None

    return IngestWarning(
        code="diagram_near_empty",
        message=(
            f"Almost nothing could be read from the uploaded diagram — "
            f"{len(graph.components)} "
            f"{'component was' if len(graph.components) == 1 else 'components were'} "
            f"extracted from an image of {image_bytes / 1024:.0f} KB. The review "
            f"below was scored on that, so it reflects very little of the diagram. "
            f"Upload the .drawio source, or a higher-resolution export, for a "
            f"review worth reading."
        ),
        detail=(
            f"{filename}: {image_bytes} bytes, {len(graph.components)} components, "
            f"{len(graph.connections)} connections, {len(graph.notes)} notes"
        ),
    )


def vision_confidence(
    graph: DesignGraph, confidence: str, illegible: list[str], filename: str
) -> IngestWarning | None:
    """Warn when the vision model itself reported it could not read the diagram.

    Distinct from `image_extraction` and worth having alongside it: the model can
    return a plausible-looking graph of eight components and still report that half
    the labels were guesses. Component count cannot see that; only the model's own
    report can, which is why `ingestion/vision.py` asks for it.
    """
    if confidence not in ("low", "medium") and not illegible:
        return None
    # `medium` alone is not enough to warn about — a diagram is rarely perfectly
    # legible, and warning on every one of them is how a banner gets ignored.
    if confidence == "medium" and not illegible:
        return None

    listed = "; ".join(illegible[:5])
    measured = (
        f"{filename}: model-reported confidence={confidence or 'unreported'}, "
        f"{len(graph.components)} components extracted"
        + (f"; illegible: {listed}" if listed else "")
    )

    # A high-confidence read that named something it could not make out is a bounded
    # gap, not a bad transcription, and must not be reported as one. Both cases used
    # to return the same code and the same "read with low confidence" sentence — so a
    # real run that reported confidence=high with 22 components and ONE unreadable
    # sub-label told the reviewer the diagram was read with low confidence, directly
    # above a detail line quoting the model saying the opposite.
    #
    # Only an explicit `high` earns this. An unreported confidence with illegible
    # items keeps the cautious wording below: silence is not a high-confidence report
    # any more than it is a low-confidence one.
    if confidence == "high":
        count = len(illegible)
        return IngestWarning(
            code="vision_minor_gaps",
            message=(
                f"The diagram was read with high confidence overall, but "
                f"{'one detail was' if count == 1 else f'{count} details were'} "
                f"unclear: {listed}. That is a bounded gap in an otherwise legible "
                f"diagram, not a reason to doubt the components and connections "
                f"below."
            ),
            detail=measured,
        )

    return IngestWarning(
        code="vision_low_confidence",
        message=(
            "The diagram was read with low confidence, so some components or "
            "connections below may be wrong or missing. Upload the .drawio source "
            "if you have it — that path is parsed exactly rather than interpreted."
        ),
        detail=measured,
    )


# --------------------------------------------------------------------------- #
# draw.io XML
# --------------------------------------------------------------------------- #

# Below this many shapes in the file there is nothing to compare against.
MIN_DRAWIO_SHAPES_TO_JUDGE = 10

# Fraction of the file's labelled shapes that must survive into the graph.
#
# Counted against LABELLED shapes only, not all of them. `drawio.parse`
# deliberately drops unlabelled shapes — a real diagram is full of arrows,
# containers and decoration that carry no reviewable meaning — so measuring against
# every `vertex="1"` would warn on every well-drawn diagram in existence.
MIN_DRAWIO_YIELD = 0.5


def drawio_extraction(
    graph: DesignGraph, raw: bytes, filename: str
) -> IngestWarning | None:
    """Warn when a draw.io file held many labelled shapes but few became components.

    This is a parser-coverage check, not a user-error check: if it fires, the most
    likely explanation is that `ingestion/drawio.py` did not understand a construct
    in the file. It is reported to the reviewer anyway, because the consequence
    lands on them either way — a design scored on a third of its diagram.

    Compressed exports are skipped rather than inflated: the shape count would need
    the payload decoded a second time, `drawio.parse` has already done that work,
    and a wrong count is worse than no check. `notes` are counted as survivors —
    an annotation that became a note was parsed correctly, just not as a component.
    """
    labelled = _labelled_shape_count(raw)
    if labelled < MIN_DRAWIO_SHAPES_TO_JUDGE:
        return None

    survived = len(graph.components) + len(graph.notes)
    if survived >= labelled * MIN_DRAWIO_YIELD:
        return None

    return IngestWarning(
        code="drawio_mostly_unparsed",
        message=(
            f"Only {survived} of about {labelled} labelled shapes in this draw.io "
            f"file were understood, so the review below covers part of the diagram "
            f"rather than all of it. Check whether the diagram uses shapes or "
            f"groupings this parser does not recognise."
        ),
        detail=(
            f"{filename}: ~{labelled} labelled shapes in the XML, "
            f"{len(graph.components)} components + {len(graph.notes)} notes "
            f"extracted ({survived / labelled:.0%} yield)"
        ),
    )


def _labelled_shape_count(raw: bytes) -> int:
    """Shapes in the XML carrying a non-empty label. 0 for a compressed export.

    A regex, not an XML parse: this is a sanity count against a file the real
    parser has already read, and it must not be able to fail on a file the real
    parser accepted. `<object label="...">` wrappers are counted too, since
    draw.io puts the label there and leaves the inner `mxCell`'s `value` empty.
    """
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a count, never a failure
        return 0

    if "mxGraphModel" not in text:
        # A compressed export. `drawio.parse` inflates it; this check does not, so
        # it declines to judge rather than guessing.
        return 0

    vertices = re.findall(r'<mxCell\b[^>]*\bvertex="1"[^>]*>', text)
    labelled = sum(1 for cell in vertices if re.search(r'\bvalue="[^"]+"', cell))
    labelled += len(re.findall(r'<(?:object|UserObject)\b[^>]*\blabel="[^"]+"', text))
    return labelled


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #

# Characters per page below which a PDF page was probably an image.
#
# 120 is about two lines of prose. A real page of a solution document runs to
# 1,500-3,000 characters, and even a sparse title or section-break page clears 120
# — so a document averaging less than this across its pages is mostly scans with a
# text cover sheet, which is precisely the case `extract_text` cannot catch because
# it did find *some* text.
MIN_CHARS_PER_PAGE = 120

# Fewer pages than this and the average is not meaningful — a one-page diagram
# export with a caption is a legitimate upload. Compared against the PDF's REAL page
# count, not against the highest `[page N]` marker; see `document_extraction`.
MIN_PAGES_TO_JUDGE = 3

_PAGE_MARKER = re.compile(r"^\[page (\d+)\]$", re.MULTILINE)


def document_extraction(
    text: str, filename: str, total_pages: int = 0
) -> IngestWarning | None:
    """Warn when a multi-page PDF yielded far too little text for its page count.

    `total_pages` comes from `documents.page_count` and is NOT derivable from `text`.
    That is the correction this function needed: the `[page N]` markers
    `documents._pdf_text` writes exist only for pages that produced text, so the
    highest marker is the last READABLE page. Reading it as the page count meant a
    40-page PDF whose only text is a cover sheet presented as a 1-page document,
    fell under `MIN_PAGES_TO_JUDGE`, and passed silently — the exact upload this
    check exists to catch, missed by the check.

    The signal is the GAP between `total_pages` and the number of markers. `0` means
    the count was unavailable (not a PDF, or an unreadable one), and there is nothing
    to compare against, so no judgement is made.
    """
    markers = [int(n) for n in _PAGE_MARKER.findall(text)]
    if not markers or total_pages < MIN_PAGES_TO_JUDGE:
        # No markers: not a PDF, or one that produced nothing — and the latter
        # already raised in `documents.extract_text`, so there is nothing to add.
        return None

    pages_with_text = len(markers)
    # Stripped of the markers themselves, so `[page 12]` — nine characters of our own
    # making — cannot pad a scanned document over the threshold on markers alone.
    body = _PAGE_MARKER.sub("", text).strip()
    per_page = len(body) / total_pages
    silent_pages = total_pages - pages_with_text

    if per_page >= MIN_CHARS_PER_PAGE and not silent_pages:
        return None
    if per_page >= MIN_CHARS_PER_PAGE and silent_pages < total_pages / 2:
        # Some pages are legitimately blank or image-only — a cover, a divider, an
        # appendix screenshot. Only warn once most of the document is missing.
        return None

    return IngestWarning(
        code="document_sparse_text",
        message=(
            f"Very little text could be read from this document: about "
            f"{per_page:.0f} characters per page across {total_pages} pages, with "
            f"{silent_pages} page{'' if silent_pages == 1 else 's'} yielding "
            f"nothing at all. It is most likely a scanned document, and the review "
            f"below covers only the part that could be read. OCR is not supported "
            f"— upload a text-based export for a complete review."
        ),
        detail=(
            f"{filename}: {total_pages} pages, {pages_with_text} with text, "
            f"{len(body)} characters total, {per_page:.0f} per page"
        ),
    )
