"""HTTP routes: upload, submit, poll, fetch."""

from __future__ import annotations

import pathlib
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import storage
from schema import ReviewResult, ReviewStatus

router = APIRouter()

_ALLOWED_SUFFIXES = frozenset(
    {
        ".pdf", ".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml",
        ".drawio", ".xml",
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
    }
)


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #


class UploadRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class UploadResponse(BaseModel):
    key: str
    upload_url: str
    expires_in: int


@router.post("/uploads", response_model=UploadResponse)
def create_upload(request: UploadRequest) -> UploadResponse:
    """Issue a presigned URL so the browser uploads straight to S3."""
    name = pathlib.Path(request.filename).name
    suffix = "".join(pathlib.Path(name.lower()).suffixes[-1:])
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix or name!r}. Allowed: "
            f"{', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )

    key = f"uploads/{uuid.uuid4()}/{name}"
    return UploadResponse(
        key=key,
        upload_url=storage.presigned_put(key, request.content_type),
        expires_in=config.UPLOAD_URL_TTL_SECONDS,
    )


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #


class ReviewRequest(BaseModel):
    document_key: str = ""
    diagram_key: str = ""
    title: str = ""
    previous_review_id: str = Field(
        default="",
        description="Set to re-review a revised design and get a score delta "
        "against that earlier review.",
    )


class ReviewAccepted(BaseModel):
    review_id: str
    status_url: str
    result_url: str


@router.post("/reviews", response_model=ReviewAccepted, status_code=202)
def create_review(request: ReviewRequest) -> ReviewAccepted:
    """Accept a design for review and start the pipeline asynchronously."""
    if not request.document_key and not request.diagram_key:
        raise HTTPException(
            status_code=400,
            detail="Provide document_key, diagram_key, or both.",
        )
    for key in (request.document_key, request.diagram_key):
        if key and not storage.object_exists(key):
            raise HTTPException(status_code=400, detail=f"No uploaded object at {key!r}.")
    if request.previous_review_id and storage.get_review(request.previous_review_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Previous review {request.previous_review_id!r} not found.",
        )

    review_id = str(uuid.uuid4())
    storage.put_status(ReviewStatus.initial(review_id))
    storage.invoke_worker(
        {
            "review_id": review_id,
            "title": request.title,
            "document_key": request.document_key,
            "diagram_key": request.diagram_key,
            "previous_review_id": request.previous_review_id,
        }
    )

    return ReviewAccepted(
        review_id=review_id,
        status_url=f"/reviews/{review_id}/status",
        result_url=f"/reviews/{review_id}",
    )


@router.get("/reviews/{review_id}/status", response_model=ReviewStatus)
def get_status(review_id: str) -> ReviewStatus:
    """Per-stage progress. The UI polls this while the pipeline runs."""
    status = storage.get_status(review_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"No review {review_id!r}.")
    return status


@router.get("/reviews/{review_id}", response_model=ReviewResult)
def get_review(review_id: str) -> ReviewResult:
    """The finished review as structured JSON."""
    result = storage.get_review(review_id)
    if result is None:
        status = storage.get_status(review_id)
        if status is not None and status.state != "complete":
            raise HTTPException(
                status_code=409,
                detail=f"Review {review_id!r} is {status.state}; poll "
                f"/reviews/{review_id}/status.",
            )
        raise HTTPException(status_code=404, detail=f"No review {review_id!r}.")
    return result
