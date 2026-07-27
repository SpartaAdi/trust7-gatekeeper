"""HTTP routes: upload, submit, poll, fetch."""

from __future__ import annotations

import logging
import pathlib
import uuid

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

import config
import storage
from agent import pipeline
from schema import ReviewResult, ReviewStatus

log = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_SUFFIXES = frozenset(
    {
        ".pdf", ".docx", ".txt", ".md", ".markdown", ".rst", ".csv", ".json",
        ".yaml", ".yml",
        ".drawio", ".xml",
        ".png", ".jpg", ".jpeg", ".gif", ".webp",
    }
)


class UploadResponse(BaseModel):
    key: str
    filename: str
    size_bytes: int


@router.post("/uploads", response_model=UploadResponse)
async def create_upload(file: UploadFile = File(...)) -> UploadResponse:
    """Accept a file and return the key to reference it by."""
    name = pathlib.Path(file.filename or "").name
    suffix = pathlib.Path(name.lower()).suffix
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {suffix or name!r}. Allowed: "
            f"{', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"{name!r} is empty.")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{name!r} is {len(data) // 1_048_576} MB; the limit is "
            f"{config.MAX_UPLOAD_BYTES // 1_048_576} MB.",
        )

    return UploadResponse(
        key=storage.save_upload(name, data), filename=name, size_bytes=len(data)
    )


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


@router.post(
    "/reviews/{review_id}/reanalyze", response_model=ReviewAccepted, status_code=202
)
def reanalyze(
    review_id: str, request: ReviewRequest, background: BackgroundTasks
) -> ReviewAccepted:
    """Re-review a revised design against an earlier review.

    The prior review comes from the path, so the caller can't accidentally submit
    a re-review that compares against nothing. Any `previous_review_id` in the
    body is ignored.
    """
    return _start_review(
        request.model_copy(update={"previous_review_id": review_id}), background
    )


@router.post("/reviews", response_model=ReviewAccepted, status_code=202)
def create_review(
    request: ReviewRequest, background: BackgroundTasks
) -> ReviewAccepted:
    """Accept a design for review and start the pipeline in the background."""
    return _start_review(request, background)


def _start_review(
    request: ReviewRequest, background: BackgroundTasks
) -> ReviewAccepted:
    if not request.document_key and not request.diagram_key:
        raise HTTPException(
            status_code=400, detail="Provide document_key, diagram_key, or both."
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

    # Runs after the response is sent, on the threadpool — a review takes minutes,
    # far longer than any client will hold a request open. The pipeline records
    # its own failures in the status file, so the UI sees them either way.
    background.add_task(
        _run_pipeline,
        review_id=review_id,
        title=request.title,
        document_key=request.document_key,
        diagram_key=request.diagram_key,
        previous_review_id=request.previous_review_id,
    )

    return ReviewAccepted(
        review_id=review_id,
        status_url=f"/reviews/{review_id}/status",
        result_url=f"/reviews/{review_id}",
    )


def _run_pipeline(**kwargs: str) -> None:
    try:
        pipeline.run(**kwargs)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — already in the status file; log and move on
        log.exception("Review %s failed", kwargs.get("review_id"))


class PillarSummary(BaseModel):
    framework: str
    pillar_id: str
    pillar_name: str
    score: float
    checks_evaluated: int


class ReviewSummary(BaseModel):
    review_id: str
    title: str
    created_at: str
    overall_score: float
    open_findings: int
    high_severity_open: int
    has_delta: bool
    pillars: list[PillarSummary]


@router.get("/reviews", response_model=list[ReviewSummary])
def list_reviews() -> list[ReviewSummary]:
    """Past reviews, newest first. Backs the history landing page."""
    return [ReviewSummary.model_validate(item) for item in storage.list_reviews()]


@router.get("/reviews/{review_id}/status", response_model=ReviewStatus)
def get_status(review_id: str) -> ReviewStatus:
    """Per-stage progress. The UI polls this while the pipeline runs."""
    try:
        status = storage.get_status(review_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if status is None:
        raise HTTPException(status_code=404, detail=f"No review {review_id!r}.")
    return status


@router.get("/reviews/{review_id}", response_model=ReviewResult)
def get_review(review_id: str) -> ReviewResult:
    """The finished review as structured JSON."""
    try:
        result = storage.get_review(review_id)
        status = None if result else storage.get_status(review_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result is None:
        if status is not None and status.state != "complete":
            raise HTTPException(
                status_code=409,
                detail=f"Review {review_id!r} is {status.state}; poll "
                f"/reviews/{review_id}/status.",
            )
        raise HTTPException(status_code=404, detail=f"No review {review_id!r}.")
    return result
