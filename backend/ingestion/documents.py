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
    elif lower.endswith((".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise UnsupportedDocument(
            f"Cannot extract text from {filename!r}. Supported: .pdf, .txt, .md, "
            ".rst, .csv, .json, .yaml."
        )

    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[document truncated at the ingestion limit]"
    return text


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(f"[page {i}]\n{p}" for i, p in enumerate(pages, 1) if p)
