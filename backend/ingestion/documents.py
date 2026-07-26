"""Text extraction from solution documents / SoWs.

Deterministic: PDFs via pypdf, everything else decoded as text. No model call.
"""

from __future__ import annotations

import io

# Guards the prompt against a pathological upload. Generous enough for a full SoW.
MAX_CHARS = 400_000


class UnsupportedDocument(ValueError):
    pass


def extract_text(data: bytes, filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        text = _pdf_text(data)
    elif lower.endswith(".docx"):
        text = _docx_text(data)
    elif lower.endswith((".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise UnsupportedDocument(
            f"Cannot extract text from {filename!r}. Supported: .pdf, .docx, .txt, "
            ".md, .rst, .csv, .json, .yaml. (.doc is not supported — save as .docx.)"
        )

    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[document truncated at the ingestion limit]"
    return text


def _docx_text(data: bytes) -> str:
    """Paragraphs and table cells — a SoW's requirements are often tabular."""
    from docx import Document

    document = Document(io.BytesIO(data))
    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(f"[page {i}]\n{p}" for i, p in enumerate(pages, 1) if p)
