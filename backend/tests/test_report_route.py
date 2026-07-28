"""The download endpoint: GET /reviews/{id}/report.pdf.

Exercised through the real app so the response headers a browser depends on —
content type and the attachment filename — are covered, not just the bytes.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import storage
from schema import ReviewStatus
from tests.test_report import REVIEW_ID, _review


RUNNING_ID = "9c8d7e6f-1a2b-3c4d-5e6f-708192a3b4c5"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """A client backed by a scratch data directory, not the developer's."""
    monkeypatch.setattr(storage.config, "DATA_DIR", tmp_path)
    # The demo gate fronts every route; tests/test_demo_gate.py covers the gate.
    monkeypatch.setattr(storage.config, "DEMO_ACCESS_TOKEN", "route-demo-token")
    import main

    return TestClient(
        main.app,
        headers={storage.config.DEMO_TOKEN_HEADER: "route-demo-token"},
    )


def test_downloads_a_pdf_with_an_attachment_filename(client: TestClient) -> None:
    storage.put_review(_review())

    response = client.get(f"/reviews/{REVIEW_ID}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="trust7-synthetic-expense-portal.pdf"'
    )
    assert response.content.startswith(b"%PDF-")
    assert int(response.headers["content-length"]) == len(response.content)
    assert PdfReader(io.BytesIO(response.content)).pages


def test_embeds_the_reviews_own_diagram_when_it_is_still_on_disk(
    client: TestClient,
) -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (100, 60), (10, 37, 64)).save(buffer, format="PNG")
    key = storage.save_upload("architecture.png", buffer.getvalue())
    storage.put_review(_review(diagram_key=key))

    response = client.get(f"/reviews/{REVIEW_ID}/report.pdf")

    assert response.status_code == 200
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(io.BytesIO(response.content)).pages
    )
    assert "architecture.png" in text


def test_still_returns_a_report_when_the_upload_has_been_reclaimed(
    client: TestClient,
) -> None:
    """Render's free-tier disk is ephemeral, so a dangling key is expected."""
    storage.put_review(_review(diagram_key="uploads/gone-forever/design.drawio"))

    response = client.get(f"/reviews/{REVIEW_ID}/report.pdf")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


def test_unknown_review_is_404_not_an_empty_pdf(client: TestClient) -> None:
    response = client.get("/reviews/11111111-2222-3333-4444-555555555555/report.pdf")
    assert response.status_code == 404


def test_a_running_review_is_409_rather_than_a_partial_report(
    client: TestClient,
) -> None:
    status = ReviewStatus.initial(RUNNING_ID)
    status.state = "running"
    storage.put_status(status)

    response = client.get(f"/reviews/{RUNNING_ID}/report.pdf")

    assert response.status_code == 409
    assert "status" in response.json()["detail"]


def test_a_traversal_attempt_in_the_review_id_is_rejected(client: TestClient) -> None:
    response = client.get("/reviews/..%2f..%2fetc%2fpasswd/report.pdf")
    assert response.status_code in (400, 404)
