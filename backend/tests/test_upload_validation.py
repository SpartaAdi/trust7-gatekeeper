"""The upload gate: extension, size, and what the bytes actually are.

Every case here is a real way a bad upload used to get through and fail somewhere
useless. The point of each test is not that a 400 comes back — it is that the 400
comes back HERE, at the door, naming the actual problem, rather than as a provider
error inside the vision call or a `BadZipFile` inside python-docx.
"""

from __future__ import annotations

import importlib
import zlib

import pytest
from fastapi.testclient import TestClient

import config
from ingestion import filetype

DEMO_TOKEN = "upload-gate-token"

# Real signatures, minimal bodies. Enough for the gate; not valid documents, which is
# fine — every test here asserts on the gate's answer, not on parsing.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"trailer\n" * 8
DOCX = b"PK\x03\x04" + b"\x00" * 64
LEGACY_DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
DRAWIO = (
    b'<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>'
    b'<mxCell id="a" value="API" vertex="1" parent="0"/>'
    b"</root></mxGraphModel></diagram></mxfile>"
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main
    import storage

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    return TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})


def upload(client: TestClient, name: str, data: bytes):
    return client.post("/uploads", files={"file": (name, data, "application/octet-stream")})


# --------------------------------------------------------------------------- #
# Extension allowlist
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "name",
    [
        "malware.exe",
        "archive.zip",
        "sheet.xlsx",
        "deck.pptx",
        "notes.doc",       # legacy Word — deliberately not accepted
        "diagram.svg",
        "photo.bmp",
        "noextension",
    ],
)
def test_an_extension_outside_the_allowlist_is_refused(client, name) -> None:
    response = upload(client, name, PDF)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Unsupported file type" in detail
    # The allowed list is IN the message. A refusal that does not say what would be
    # accepted leaves the uploader guessing.
    assert ".drawio" in detail and ".pdf" in detail


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("sow.pdf", PDF), ("sow.docx", DOCX), ("sow.md", b"# A design\n"),
        ("sow.txt", b"A design.\n"), ("data.json", b'{"a": 1}'),
        ("data.yaml", b"a: 1\n"), ("data.csv", b"a,b\n1,2\n"),
        ("notes.rst", b"Design\n======\n"),
        ("design.drawio", DRAWIO), ("design.xml", DRAWIO),
        ("shot.png", PNG), ("shot.jpg", JPEG), ("shot.jpeg", JPEG),
        ("shot.gif", GIF), ("shot.webp", WEBP),
    ],
)
def test_every_allowed_type_with_matching_content_is_accepted(client, name, data) -> None:
    """The gate must not have become a wall. Each accepted type, with real bytes."""
    response = upload(client, name, data)

    assert response.status_code == 200, response.json()
    assert response.json()["filename"] == name
    assert response.json()["size_bytes"] == len(data)


# --------------------------------------------------------------------------- #
# Size
# --------------------------------------------------------------------------- #

def test_an_oversized_body_is_refused_with_413(client, monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1024)

    response = upload(client, "big.md", b"x" * 4096)

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "over the" in detail and "limit" in detail


def test_the_size_message_names_a_real_number_rather_than_rounding_to_the_limit() -> None:
    """The integer-division version said "is 25 MB; the limit is 25 MB" for 25.9 MB,
    which reads as a broken limit rather than a file that is over it."""
    message = filetype._too_big(int(25.9 * 1_048_576), 25 * 1_048_576)

    assert "25.9 MB" in message
    assert "25 MB limit" in message


def test_an_oversized_declared_length_is_refused_before_the_body_is_read() -> None:
    """The check that stops the server buffering 500 MB to reject it.

    Called directly: getting a TestClient to send a Content-Length larger than its
    body would test httpx, not this.
    """
    with pytest.raises(filetype.UnsupportedUpload, match="over the"):
        filetype.check_declared_size(str(500 * 1_048_576), 25 * 1_048_576)


@pytest.mark.parametrize("header", [None, "", "not-a-number", "-1"])
def test_a_missing_or_unparseable_content_length_is_not_an_error(header) -> None:
    """A declared length is a claim. Absent or malformed just means the body check
    is the one that decides, and that check always runs."""
    filetype.check_declared_size(header, 1024)


def test_an_empty_file_is_refused_by_name(client) -> None:
    response = upload(client, "empty.md", b"")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]
    assert "0 bytes" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Content vs extension — the check an allowlist cannot make
# --------------------------------------------------------------------------- #

def test_a_pdf_renamed_png_is_refused_here_not_inside_the_vision_call(client) -> None:
    """Without this it is accepted, base64'd, and sent to the vision model as an
    image — where the failure the user sees is a provider error."""
    response = upload(client, "diagram.png", PDF)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "named .png" in detail
    assert "PDF" in detail


def test_a_legacy_doc_renamed_docx_is_named_as_such(client) -> None:
    """"This is the old .doc format" is actionable. `BadZipFile` is not."""
    response = upload(client, "sow.docx", LEGACY_DOC)

    assert response.status_code == 400
    assert "legacy Microsoft Office" in response.json()["detail"]


def test_a_jpeg_renamed_pdf_is_refused(client) -> None:
    response = upload(client, "sow.pdf", JPEG)

    assert response.status_code == 400
    assert "JPEG" in response.json()["detail"]


def test_content_that_matches_no_signature_at_all_is_refused_for_a_binary_type(
    client,
) -> None:
    """A .png must be a PNG. Nothing recognisable means it is not one."""
    response = upload(client, "diagram.png", b"just some text pretending to be a png")

    assert response.status_code == 400
    assert "not a PNG file" in response.json()["detail"]


def test_a_binary_file_renamed_markdown_is_refused(client) -> None:
    """The silent case this check exists for.

    `documents.extract_text` decodes with errors="replace" and never raises, so a
    JPEG named .md becomes a page of U+FFFD, is scored as a solution document, and
    produces a straight-faced review of nothing.
    """
    response = upload(client, "sow.md", JPEG + b"\xff\xfe\xfd" * 400)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "binary" in detail
    assert "JPEG" in detail, "the detected type should be named when it is known"


def test_null_bytes_alone_are_enough_to_refuse_a_text_type(client) -> None:
    """No text encoding this accepts produces a NUL, and a truncated binary leads
    with them."""
    response = upload(client, "notes.txt", b"design\x00\x00\x00document")

    assert response.status_code == 400
    assert "null bytes" in response.json()["detail"]


def test_a_binary_drawio_is_refused_because_even_a_compressed_export_is_xml(
    client,
) -> None:
    """draw.io's compressed format is base64 inside `<diagram>` — still text."""
    response = upload(client, "design.drawio", zlib.compress(b"x" * 500))

    assert response.status_code == 400
    assert "text file" in response.json()["detail"]


def test_a_compressed_drawio_export_is_still_accepted(client) -> None:
    """The other half of the test above: the real compressed format must pass.

    A gate that rejected draw.io's own default export would be worse than no gate,
    so this pins the shape rather than trusting the one above.
    """
    import base64
    import urllib.parse

    inner = b'<mxGraphModel><root><mxCell id="0"/></root></mxGraphModel>'
    deflated = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    payload = deflated.compress(urllib.parse.quote(inner).encode()) + deflated.flush()
    body = (
        b"<mxfile><diagram>"
        + base64.b64encode(payload)
        + b"</diagram></mxfile>"
    )

    assert upload(client, "design.drawio", body).status_code == 200


def test_smart_quotes_do_not_make_a_text_file_binary(client) -> None:
    """The tolerance is not zero, deliberately: a SoW pasted out of Word arrives
    with cp1252 punctuation, and one mis-encoded character must not block it."""
    body = "The design's aim is ".encode() + b"\x93scalable\x94" + b" throughput.\n" * 20

    assert upload(client, "sow.md", body).status_code == 200


def test_a_docx_is_only_required_to_be_a_zip(client) -> None:
    """Deliberately shallow. Every Office and OpenDocument format is a zip, so this
    module confirms "a zip" and leaves "a Word document" to python-docx — which has
    the parser and the user-facing message for it already."""
    assert upload(client, "sow.docx", DOCX).status_code == 200


def test_nothing_is_written_to_disk_when_an_upload_is_refused(client, tmp_path) -> None:
    """Every gate runs before `storage.save_upload`, so a rejected upload leaves
    no orphan on Render's disk."""
    before = set(tmp_path.rglob("*"))

    assert upload(client, "diagram.png", PDF).status_code == 400

    assert set(tmp_path.rglob("*")) == before


# --------------------------------------------------------------------------- #
# The module's own contract
# --------------------------------------------------------------------------- #

def test_unrecognised_content_is_accepted_rather_than_rejected() -> None:
    """The rule that keeps this from blocking legitimate uploads.

    This module can prove a MISMATCH, never a match. A file whose bytes match no
    signature and whose extension requires none has told us nothing, and rejecting
    on "no evidence" would block real files to catch nothing.
    """
    filetype.validate("design.drawio", b"<mxfile></mxfile>", limit=1_000_000)
    filetype.validate("notes.rst", b"Some design notes.", limit=1_000_000)


def test_every_allowed_suffix_is_either_signature_checked_or_text_checked() -> None:
    """No accepted extension may fall through both checks unnoticed.

    Without this, adding a suffix to `_ALLOWED_SUFFIXES` silently opts it out of
    content validation entirely — the gate would still look complete.
    """
    from api.routes import _ALLOWED_SUFFIXES

    unchecked = sorted(
        suffix
        for suffix in _ALLOWED_SUFFIXES
        if suffix not in filetype._REQUIRED and suffix not in filetype._TEXT_SUFFIXES
    )

    assert not unchecked, (
        f"{unchecked} are accepted by the route but content-checked by neither "
        f"_REQUIRED nor _TEXT_SUFFIXES in ingestion/filetype.py"
    )
