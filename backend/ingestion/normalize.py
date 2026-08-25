"""Ingest and normalize: route each input, then merge into one NormalizedDesign."""

from __future__ import annotations

import pathlib

import storage
from ingestion import documents, drawio, embedded, fidelity, quality, vision
from schema import DataFidelity, DesignGraph, IngestWarning, NormalizedDesign

_DRAWIO_SUFFIXES = (".drawio", ".xml", ".drawio.xml")
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class UnsupportedDiagram(ValueError):
    pass


def ingest(
    *,
    review_id: str,
    title: str = "",
    document_key: str = "",
    diagram_key: str = "",
    context: str = "",
) -> tuple[NormalizedDesign, dict[str, int]]:
    """Fetch the uploads, parse each, and converge on the common schema.

    Also collects extraction-quality warnings as it goes — see `ingestion/quality.py`
    for what each one means and why none of them stops the review. They are gathered
    here rather than checked later because this is the only place that still holds
    both sides of the comparison: the raw bytes AND what parsing made of them.
    """
    warnings: list[IngestWarning] = []
    measured = DataFidelity()

    document_text = ""
    if document_key:
        raw = storage.get_object(document_key)
        document_text = documents.extract_text(raw, document_key)
        # The page count comes from the PDF itself, not from the extracted text: the
        # text only carries markers for pages that produced some, so it cannot see
        # the scanned pages that are the whole point of the check.
        sparse = quality.document_extraction(
            document_text,
            _display_name(document_key),
            documents.page_count(raw, document_key),
        )
        if sparse:
            warnings.append(sparse)

    graph = DesignGraph()
    usage: dict[str, int] = {}
    if diagram_key:
        data = storage.get_object(diagram_key)
        graph, usage, diagram_warnings, measured = parse_diagram(data, diagram_key)
        warnings.extend(diagram_warnings)
    elif document_key:
        # No diagram was uploaded, so look for one INSIDE the document. Until this
        # existed, a SoW whose architecture is a picture on page 8 was reviewed on
        # its prose alone — `documents.extract_text` reads text and never opens an
        # image, so the diagram was not mis-read, it was never looked at.
        #
        # Deliberately only in the `elif`: an explicit diagram upload is the
        # submitter telling us which diagram to review, and it keeps winning
        # unconditionally. This never overrides, never merges, and never runs when
        # one was given.
        graph, usage, measured = _ingest_embedded_diagram(
            storage.get_object(document_key), document_key, warnings
        )

    if not document_text and not graph.components:
        raise ValueError(
            "Nothing reviewable was supplied: provide a solution document, an "
            "architecture diagram, or both."
        )

    design = NormalizedDesign(
        review_id=review_id,
        title=title or _default_title(document_key, diagram_key),
        document_text=document_text,
        # Passed through verbatim; the model's validator caps and strips it, and
        # `as_prompt_context` fences it. Nothing here interprets it.
        context=context,
        graph=graph,
        warnings=warnings,
        # Only the two ingestion-time numbers are set here. The grounding count
        # comes from the remediate stage and is filled in by the pipeline.
        fidelity=measured,
    )
    return design, usage


def _ingest_embedded_diagram(
    raw: bytes, document_key: str, warnings: list[IngestWarning]
) -> tuple[DesignGraph, dict[str, int], DataFidelity]:
    """Read the architecture diagram embedded in a document, if there is one.

    Routed through `parse_diagram` rather than calling `vision.parse` directly, and
    that is the point of the design: the selected image goes down the SAME path an
    explicitly uploaded image takes, so it gets the same vision prompt, the same two
    quality warnings, the same fidelity measurement, and `DiagramSource.IMAGE`. Every
    consumer that already handles an image upload — scoring, the results page, the
    re-review reference graph, Segment 7's grounding haystack — needs no new code,
    because as far as they can tell nothing new happened.

    Returns empty on every miss, and misses are the common case: not a PDF, no
    images, nothing above the area floor. All of those cost no model call.
    """
    selected = embedded.select_diagram(raw)
    if selected is None:
        return DesignGraph(), {}, DataFidelity()

    # The name carries the page, because "where did these components come from?" is
    # the first question a reviewer asks about a diagram they did not upload. It
    # reaches them through the quality warnings' `detail`, which render the filename.
    shown = (
        f"{_display_name(document_key)} (page {selected.page}, "
        f"{selected.width}x{selected.height})"
    )
    graph, usage, diagram_warnings, measured = parse_diagram(
        selected.data, f"{shown}{_extension_for(selected.media_type)}"
    )
    warnings.extend(diagram_warnings)
    return graph, usage, measured


def _extension_for(media_type: str) -> str:
    """`parse_diagram` dispatches on the SUFFIX, so a synthetic name needs a real one."""
    for suffix, media in _IMAGE_MEDIA_TYPES.items():
        if media == media_type:
            return suffix
    return ".png"


def _display_name(key: str) -> str:
    """The filename a person recognises, out of a storage key.

    Keys look like `uploads/<uuid>/whiteboard-photo.png`. Warning `detail` is rendered
    to the reviewer, and the uuid is noise to them — they identify the file by name.
    """
    return pathlib.Path(key).name or key


def parse_diagram(
    data: bytes, filename: str
) -> tuple[DesignGraph, dict[str, int], list[IngestWarning], DataFidelity]:
    """Dispatch to the deterministic path or the vision path by file type.

    Each path gets the quality check and the fidelity measurement that can say
    something about it, and neither is meaningful on the other path — which is why
    both are applied here rather than to the merged graph:

    * **draw.io** can be compared against the shapes in its own XML, so its coverage
      is EXACT and needs no model. There is nothing to OCR.
    * **an image** has no ground truth for what it contains, so its coverage is an
      ESTIMATE against a second reader. A structural ratio is not even definable.

    Keeping them on separate fields of `DataFidelity` rather than one "coverage"
    number is what stops an exact figure and an estimate being read as the same
    kind of thing.
    """
    lower = filename.lower()
    shown = _display_name(filename)
    if lower.endswith(_DRAWIO_SUFFIXES):
        graph = drawio.parse(data)
        unparsed = quality.drawio_extraction(graph, data, shown)
        return (
            graph,
            {},
            [unparsed] if unparsed else [],
            DataFidelity(structural=fidelity.structural_coverage(graph, data)),
        )

    suffix = pathlib.Path(lower).suffix
    if suffix in _IMAGE_MEDIA_TYPES:
        graph, usage, confidence, illegible = vision.parse(
            data, _IMAGE_MEDIA_TYPES[suffix]
        )
        warnings = [
            warning
            for warning in (
                quality.image_extraction(graph, len(data), shown),
                quality.vision_confidence(graph, confidence, illegible, shown),
            )
            if warning
        ]
        return (
            graph,
            usage,
            warnings,
            DataFidelity(ocr_proxy=fidelity.ocr_coverage_proxy(graph, data)),
        )

    raise UnsupportedDiagram(
        f"Cannot parse diagram {filename!r}. Supported: "
        f"{', '.join(_DRAWIO_SUFFIXES)} (parsed directly) or "
        f"{', '.join(sorted(_IMAGE_MEDIA_TYPES))} (read using AI vision)."
    )


def _default_title(document_key: str, diagram_key: str) -> str:
    source = document_key or diagram_key
    return pathlib.Path(source).stem.replace("-", " ").replace("_", " ").strip() or "Untitled design"
