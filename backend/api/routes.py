"""HTTP routes: upload, submit, poll, fetch."""

from __future__ import annotations

import logging
import pathlib
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field

import cancel
import config
import llm
import report
import share
import storage
from agent import pipeline
from schema import ReviewResult, ReviewStatus, ScoreDelta

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
    context: str = Field(
        default="",
        description="Optional free text describing purpose and use case. Offered by "
        "the UI only for a diagram-only submission, where there is no SoW to carry "
        "it. UNTRUSTED input: capped and fenced, never treated as instructions.",
    )
    previous_review_id: str = Field(
        default="",
        description="Set to re-review a revised design and get a score delta "
        "against that earlier review.",
    )


class ReviewAccepted(BaseModel):
    review_id: str
    status_url: str
    result_url: str


# A reviewer's own OpenRouter key, spent instead of the server's for their reviews.
#
# A HEADER and not a body field, deliberately. FastAPI answers a malformed body
# with a 422 that echoes the offending input back to the client, so one bad
# neighbouring field would put the key in a response — the exact thing it must
# never appear in. Headers are not echoed by that handler.
OPENROUTER_KEY_HEADER = "X-OpenRouter-Key"


@router.post(
    "/reviews/{review_id}/reanalyze", response_model=ReviewAccepted, status_code=202
)
def reanalyze(
    review_id: str,
    request: ReviewRequest,
    background: BackgroundTasks,
    x_openrouter_key: str = Header(default=""),
) -> ReviewAccepted:
    """Re-review a revised design against an earlier review.

    The prior review comes from the path, so the caller can't accidentally submit
    a re-review that compares against nothing. Any `previous_review_id` in the
    body is ignored.
    """
    return _start_review(
        request.model_copy(update={"previous_review_id": review_id}),
        background,
        x_openrouter_key,
    )


@router.post("/reviews", response_model=ReviewAccepted, status_code=202)
def create_review(
    request: ReviewRequest,
    background: BackgroundTasks,
    x_openrouter_key: str = Header(default=""),
) -> ReviewAccepted:
    """Accept a design for review and start the pipeline in the background."""
    return _start_review(request, background, x_openrouter_key)


def _start_review(
    request: ReviewRequest, background: BackgroundTasks, api_key: str = ""
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
        context=request.context,
        previous_review_id=request.previous_review_id,
        # Held only for the life of this background task, and only in the
        # ContextVar `pipeline.run` sets from it. Never reaches storage: the
        # status and result records below are written from the pipeline's own
        # models, neither of which has a field for it.
        api_key=api_key.strip(),
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


_TERMINAL_STATES = frozenset({"complete", "error", "cancelled"})


@router.post("/reviews/{review_id}/cancel", response_model=ReviewStatus)
def cancel_review(review_id: str) -> ReviewStatus:
    """Stop a running review.

    Two things happen, and both are needed. The flag is registered, which is what
    stops the pipeline before its next stage — the part that actually saves money,
    since five unstarted stages cost more than one abandoned response. Then the
    transport is closed, which aborts the request already on the wire rather than
    leaving a thread receiving a response nobody will read.

    A call that is fully sent and merely awaiting a reply may still complete and
    bill; that is accepted rather than fought. Nothing here tries to un-bill work
    the provider has already done.

    Cancelling an already-finished review is a conflict, not a no-op: a `complete`
    review has a stored result, and answering 200 here would suggest that result
    had been withdrawn when it has not.
    """
    try:
        status = storage.get_status(review_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if status is None:
        raise HTTPException(status_code=404, detail=f"No review {review_id!r}.")
    if status.state in _TERMINAL_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"Review {review_id!r} is already {status.state}.",
        )

    cancel.request(review_id)
    llm.abort_in_flight()

    # Written here as well as by the pipeline, because the pipeline only notices at
    # its next stage boundary and the reviewer who just clicked the button should
    # not have to wait for that to see it. The pipeline's own write follows and
    # marks which stage was interrupted.
    status.state = "cancelled"
    status.error = ""
    storage.put_status(status)
    return status


# --------------------------------------------------------------------------- #
# Read-only share link
#
# One completed review, no findings text, no auth token. See share.py for why the
# token is derived rather than stored, and why the link outlives a restart but the
# review behind it does not.
# --------------------------------------------------------------------------- #


class SharedPillar(BaseModel):
    framework: str
    pillar_id: str
    pillar_name: str
    score: float
    checks_evaluated: int
    checks_passed: int


class SharedReview(BaseModel):
    """The scoreboard and its movement — deliberately not the findings.

    A share link bypasses the demo gate, so whatever this returns is readable by
    anyone holding the URL. Scores and their direction of travel are what a link
    is for; evidence, rationale and remediation text are the parts of a review
    most likely to quote a customer's design back, so none of them is here.

    `component_count` rather than the components themselves, for the same reason:
    the count carries the sense of scale without naming anyone's architecture.
    """

    review_id: str
    title: str
    created_at: str
    overall_score: float
    frameworks: list[str] = Field(default_factory=list)
    pillars: list[SharedPillar] = Field(default_factory=list)
    open_findings: int
    high_severity_open: int
    component_count: int
    delta: ScoreDelta | None = None
    expires_note: str = Field(
        description="Stated, not implied: local-data/ is ephemeral on Render's "
        "free tier, so the review behind a valid link disappears on restart."
    )


class ShareLink(BaseModel):
    review_id: str
    token: str
    path: str = Field(description="API path a client can fetch the shared review from.")
    expires_note: str


@router.get("/reviews/{review_id}/share", response_model=ShareLink)
def create_share_link(review_id: str) -> ShareLink:
    """Mint the share token for a completed review. Gated — issuing is not sharing.

    Deterministic: calling this twice returns the same token, because the token is
    derived from the review id rather than generated and stored. That is what
    makes a link survive a restart, and it also means there is nothing to
    invalidate here — rotating DEMO_ACCESS_TOKEN revokes every link at once.
    """
    if not share.sharing_enabled():
        raise HTTPException(status_code=409, detail=share.SHARING_DISABLED_REASON)

    # Reuses the completed-review lookup, so an unfinished or missing review
    # cannot be shared: it 404s or 409s exactly as fetching it would.
    result = get_review(review_id)

    return ShareLink(
        review_id=result.review_id,
        token=share.token_for(result.review_id),
        path=f"/shared/{result.review_id}",
        expires_note=share.EPHEMERAL_NOTE,
    )


@router.get("/shared/{review_id}", response_model=SharedReview)
def get_shared_review(review_id: str, t: str = "") -> SharedReview:
    """A shared review, read-only, authorised by the `t` token rather than the gate.

    Every failure answers 404 with the same message — bad token, unknown id,
    unfinished review, sharing switched off. Distinguishing them would let anyone
    holding a link enumerate which review ids exist, which is the one thing an
    ungated route must not offer.
    """
    not_found = HTTPException(status_code=404, detail="No shared review at this link.")

    try:
        if not share.is_valid(review_id, t):
            raise not_found
        result = storage.get_review(review_id)
    except ValueError:
        # Malformed id — same answer as a wrong token, for the same reason.
        raise not_found from None
    if result is None:
        raise not_found

    open_findings = [f for f in result.findings if f.status in ("fail", "partial")]
    return SharedReview(
        review_id=result.review_id,
        title=result.title,
        created_at=result.created_at,
        overall_score=result.overall_score,
        frameworks=[framework.framework_name for framework in result.frameworks],
        pillars=[
            SharedPillar(
                framework=pillar.framework,
                pillar_id=pillar.pillar_id,
                pillar_name=pillar.pillar_name,
                score=pillar.score,
                checks_evaluated=pillar.checks_evaluated,
                checks_passed=pillar.checks_passed,
            )
            for framework in result.frameworks
            for pillar in framework.pillars
        ],
        open_findings=len(open_findings),
        high_severity_open=sum(1 for f in open_findings if f.severity == "high"),
        component_count=len(result.components),
        delta=result.delta,
        expires_note=share.EPHEMERAL_NOTE,
    )


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


@router.get(
    "/reviews/{review_id}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def get_report(review_id: str) -> Response:
    """The review as a formatted PDF.

    Rendered on demand rather than cached: a review is a few hundred kilobytes of
    JSON and generation is well under a second, so storing a second artefact
    would add an invalidation problem for no gain.
    """
    result = get_review(review_id)  # reuses the 404/409/400 handling above

    diagram: tuple[str, bytes] | None = None
    if result.diagram_key:
        try:
            diagram = (
                pathlib.Path(result.diagram_key).name,
                storage.get_object(result.diagram_key),
            )
        except (ValueError, OSError) as exc:
            # The uploaded file is gone (Render's free-tier disk is ephemeral) or
            # the key is unusable. The report is still worth producing without it.
            log.warning("Diagram %r unavailable for %s: %s", result.diagram_key, review_id, exc)

    pdf = report.build_pdf(result, diagram)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{report.filename_for(result)}"',
            "Content-Length": str(len(pdf)),
        },
    )


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
