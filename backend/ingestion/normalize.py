"""Ingest and normalize: route each input, then merge into one NormalizedDesign."""

from __future__ import annotations

import pathlib

import storage
from ingestion import documents, drawio, vision
from schema import DesignGraph, NormalizedDesign

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
    """Fetch the uploads, parse each, and converge on the common schema."""
    document_text = ""
    if document_key:
        raw = storage.get_object(document_key)
        document_text = documents.extract_text(raw, document_key)

    graph = DesignGraph()
    usage: dict[str, int] = {}
    if diagram_key:
        graph, usage = parse_diagram(storage.get_object(diagram_key), diagram_key)

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
    )
    return design, usage


def parse_diagram(data: bytes, filename: str) -> tuple[DesignGraph, dict[str, int]]:
    """Dispatch to the deterministic path or the vision path by file type."""
    lower = filename.lower()
    if lower.endswith(_DRAWIO_SUFFIXES):
        return drawio.parse(data), {}

    suffix = pathlib.Path(lower).suffix
    if suffix in _IMAGE_MEDIA_TYPES:
        return vision.parse(data, _IMAGE_MEDIA_TYPES[suffix])

    raise UnsupportedDiagram(
        f"Cannot parse diagram {filename!r}. Supported: "
        f"{', '.join(_DRAWIO_SUFFIXES)} (parsed directly) or "
        f"{', '.join(sorted(_IMAGE_MEDIA_TYPES))} (read using AI vision)."
    )


def _default_title(document_key: str, diagram_key: str) -> str:
    source = document_key or diagram_key
    return pathlib.Path(source).stem.replace("-", " ").replace("_", " ").strip() or "Untitled design"
