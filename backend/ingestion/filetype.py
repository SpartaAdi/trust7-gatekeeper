"""Upload validation at the door: extension, size, and what the bytes actually are.

Runs at `POST /uploads`, before anything parses the file. Three checks, in the
order they get cheaper to be wrong about:

1. **Extension** against an allowlist. Cheapest, and the only one that can be
   done without the bytes.
2. **Size**, against the declared `Content-Length` first and the real length
   second — see `check_declared_size` for why both.
3. **Content**, against the signature the extension implies.

The third is the one that earns its place. An extension allowlist answers "is
this a kind of file we accept", not "is this that kind of file". A `.png` holding
a PDF passes the allowlist and then reaches the vision model as a base64 image
the provider rejects; a `.docx` that is really a legacy `.doc` passes and then
fails inside python-docx as `BadZipFile`; a scanned photo renamed `.md` passes
and gets decoded to replacement characters and scored as a solution document.
Every one of those is diagnosable here in one line and confusing anywhere else.

## Why not python-magic / libmagic

It is a C dependency for eight signatures. The formats accepted here are fixed by
`_ALLOWED_SUFFIXES` in the route, all eight binary ones have a stable magic number
at offset 0 (or 8, for WEBP), and a wrong answer from this module blocks a real
upload — so the check being readable in one screen matters more than it being
exhaustive. Anything not recognised is ACCEPTED, not rejected: see `validate`.

Every message here is user-facing and surfaced verbatim by the upload view.
"""

from __future__ import annotations

import pathlib


class UnsupportedUpload(ValueError):
    """The upload cannot be accepted. The message is written for the uploader."""


# (offset, signature, human name). Checked against the bytes on disk, in order.
#
# DOCX is a zip (`PK\x03\x04`), which it shares with every other Office and
# OpenDocument format — so the zip signature confirms "a zip", and whether it is a
# Word document is settled by python-docx later. That is deliberate: this module's
# job is to reject what is obviously not the claimed type, not to re-implement a
# format parser.
_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (0, b"%PDF-", "PDF"),
    (0, b"\x89PNG\r\n\x1a\n", "PNG"),
    (0, b"\xff\xd8\xff", "JPEG"),
    (0, b"GIF87a", "GIF"),
    (0, b"GIF89a", "GIF"),
    (0, b"RIFF", "RIFF (WEBP container)"),
    (0, b"PK\x03\x04", "ZIP (a .docx is a zip)"),
    # Legacy OLE2 — .doc, .xls, .ppt. Named because a user uploading a .doc renamed
    # to .docx is a real and common mistake, and "this is the old .doc format" is a
    # far more useful answer than "this is not a zip".
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "legacy Microsoft Office (.doc/.xls/.ppt)"),
)

# Which signature each extension requires. An extension absent from this map has no
# signature requirement and is checked as text instead.
_REQUIRED: dict[str, tuple[str, ...]] = {
    ".pdf": ("PDF",),
    ".png": ("PNG",),
    ".jpg": ("JPEG",),
    ".jpeg": ("JPEG",),
    ".gif": ("GIF",),
    ".webp": ("RIFF (WEBP container)",),
    ".docx": ("ZIP (a .docx is a zip)",),
}

# Extensions that must decode as text. `.drawio` and `.xml` are here because
# draw.io's compressed export is still XML on the outside — the deflate payload is
# base64 inside a `<diagram>` element — so a valid draw.io file is always text.
_TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml",
     ".drawio", ".xml"}
)

# How much of the file the text check reads. A binary file renamed .md is binary in
# its first bytes; reading further would not change the answer and a 25 MB scan at
# the door would.
_TEXT_SNIFF_BYTES = 8192

# Proportion of undecodable bytes in the sniffed prefix above which the file is
# called binary. Not zero: a SoW pasted out of Word arrives with smart quotes,
# and one mis-encoded character must not block an upload.
_MAX_REPLACEMENT_RATIO = 0.10


def detect(data: bytes) -> str:
    """The format the bytes claim to be, or "" when no signature matches."""
    for offset, signature, name in _SIGNATURES:
        if data[offset:offset + len(signature)] == signature:
            return name
    return ""


def check_declared_size(content_length: str | int | None, limit: int) -> None:
    """Refuse an oversized upload from its declared length, before reading a byte.

    The real check is on the bytes, further down — a declared length is a claim and
    can be absent or wrong. This is the one that stops the server buffering 500 MB
    to discover it is over a 25 MB limit, which is the difference between a 400 and
    a memory spike.

    Absent or unparseable is not an error: the body check still runs.
    """
    if content_length in (None, ""):
        return
    try:
        declared = int(content_length)
    except (TypeError, ValueError):
        return
    if declared > limit:
        raise UnsupportedUpload(_too_big(declared, limit))


def _too_big(size: int, limit: int) -> str:
    """One decimal place, deliberately.

    The integer-division version of this message said "is 25 MB; the limit is
    25 MB" for a 25.9 MB file, which reads as a bug in the limit rather than as a
    file that is over it.
    """
    return (
        f"This file is {size / 1_048_576:.1f} MB, over the "
        f"{limit / 1_048_576:.0f} MB limit. Upload a smaller export — for a PDF, "
        f"re-export it without embedded images; for a diagram, upload the .drawio "
        f"file rather than a screenshot."
    )


def validate(filename: str, data: bytes, *, limit: int) -> None:
    """Accept the upload, or raise `UnsupportedUpload` saying exactly what is wrong.

    Unrecognised content is ACCEPTED. This module can only prove a mismatch, never
    a match: a file whose bytes match no known signature and is not one of the text
    types has simply told us nothing, and rejecting on "no evidence" would block
    legitimate uploads to catch nothing. Every raise below names a positive
    finding — a signature that contradicts the extension, or bytes that cannot be
    text where text is required.
    """
    suffix = pathlib.Path(filename.lower()).suffix

    if not data:
        raise UnsupportedUpload(
            f"{filename!r} is empty — 0 bytes. Check the export completed, then "
            f"upload it again."
        )
    if len(data) > limit:
        raise UnsupportedUpload(_too_big(len(data), limit))

    required = _REQUIRED.get(suffix)
    if required:
        found = detect(data)
        if not found:
            raise UnsupportedUpload(
                f"{filename!r} is named {suffix} but its contents are not a "
                f"{required[0].split(' ')[0]} file. Check the file opens on your "
                f"machine, and that it was not renamed from another format."
            )
        if found not in required:
            raise UnsupportedUpload(
                f"{filename!r} is named {suffix} but its contents are "
                f"{found}. Convert it to a real {suffix} file, or upload it with "
                f"its correct extension."
            )
        return

    if suffix in _TEXT_SUFFIXES:
        _require_text(filename, data, suffix)


def _require_text(filename: str, data: bytes, suffix: str) -> None:
    """Reject a binary file wearing a text extension.

    Decoding with `errors="replace"` and counting the replacements, rather than
    catching `UnicodeDecodeError`: `documents.extract_text` and `drawio.parse` both
    decode this way and so never raise, which is exactly how a JPEG named `.md`
    reaches the model as a page of U+FFFD and gets scored. Counting is what turns
    that silent success into a refusal.
    """
    prefix = data[:_TEXT_SNIFF_BYTES]

    # A NUL byte in a text file is decisive on its own — no text encoding this
    # accepts produces one, and it is what a truncated binary usually leads with.
    if b"\x00" in prefix:
        raise UnsupportedUpload(_not_text(filename, suffix, detect(data), "null bytes"))

    decoded = prefix.decode("utf-8", errors="replace")
    if not decoded:
        return
    replacements = decoded.count("�")
    if replacements / len(decoded) > _MAX_REPLACEMENT_RATIO:
        raise UnsupportedUpload(
            _not_text(
                filename, suffix, detect(data),
                f"{replacements} undecodable bytes in the first {len(decoded)}",
            )
        )


def _not_text(filename: str, suffix: str, detected: str, evidence: str) -> str:
    identified = f" It looks like a {detected} file." if detected else ""
    return (
        f"{filename!r} is named {suffix}, which must be a text file, but its "
        f"contents are binary ({evidence}).{identified} Upload the text or "
        f"XML export rather than a binary file with a text extension."
    )
