"""Ingest and normalize: route each input, then merge into one NormalizedDesign."""

from __future__ import annotations

import pathlib

import storage
from ingestion import documents, drawio, quality, vision
from schema import DesignGraph, IngestWarning, NormalizedDesign

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
        graph, usage, diagram_warnings = parse_diagram(data, diagram_key)
        warnings.extend(diagram_warnings)

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
    )
    return design, usage


def _display_name(key: str) -> str:
    """The filename a person recognises, out of a storage key.

    Keys look like `uploads/<uuid>/whiteboard-photo.png`. Warning `detail` is rendered
    to the reviewer, and the uuid is noise to them — they identify the file by name.
    """
    return pathlib.Path(key).name or key


def parse_diagram(
    data: bytes, filename: str
) -> tuple[DesignGraph, dict[str, int], list[IngestWarning]]:
    """Dispatch to the deterministic path or the vision path by file type.

    Each path gets the quality check that can say something about it: draw.io can be
    compared against the shapes in its own XML, an image against its byte size and
    the model's own legibility report. Neither check is meaningful on the other path,
    which is why they are applied here rather than to the merged graph.
    """
    lower = filename.lower()
    shown = _display_name(filename)
    if lower.endswith(_DRAWIO_SUFFIXES):
        graph = drawio.parse(data)
        unparsed = quality.drawio_extraction(graph, data, shown)
        return graph, {}, [unparsed] if unparsed else []

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
        return graph, usage, warnings

    raise UnsupportedDiagram(
        f"Cannot parse diagram {filename!r}. Supported: "
        f"{', '.join(_DRAWIO_SUFFIXES)} (parsed directly) or "
        f"{', '.join(sorted(_IMAGE_MEDIA_TYPES))} (read using AI vision)."
    )


def _default_title(document_key: str, diagram_key: str) -> str:
    source = document_key or diagram_key
    return pathlib.Path(source).stem.replace("-", " ").replace("_", " ").strip() or "Untitled design"
