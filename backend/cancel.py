"""Cancellation registry for in-flight reviews.

A review runs on FastAPI's threadpool in this same process, so the signal is an
in-memory set rather than anything on disk. The status file records that a review
*was* cancelled — that is what the UI polls — but it cannot be the signal itself:
`_Progress.start` writes `state="running"` at the top of every stage, so a cancel
landing between two stages would be overwritten by the next stage's own write.

Deliberately not a queue or an event per review. The only question anyone asks is
"has this been cancelled", and the answer never goes back to False: a cancelled
review is finished, and a new run gets a new review id.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


class Cancelled(RuntimeError):
    """Raised where a cancelled review would otherwise do more work.

    Distinct from every other pipeline exception on purpose. A cancellation is not
    a failure, and the difference has to survive all the way to the screen —
    `pipeline.run` records it as `cancelled` rather than `error`, and stores no
    result.
    """


_lock = threading.Lock()
_cancelled: set[str] = set()


def request(review_id: str) -> None:
    """Mark a review cancelled. Idempotent."""
    with _lock:
        _cancelled.add(review_id)
    log.info("Review %s cancellation requested", review_id)


def is_cancelled(review_id: str) -> bool:
    with _lock:
        return review_id in _cancelled


def check(review_id: str) -> None:
    """Raise if this review has been cancelled.

    Called between stages, which is where cancellation actually saves money: the
    call already in flight may well complete and bill, but the five that would have
    followed it never start.
    """
    if is_cancelled(review_id):
        raise Cancelled(f"Review {review_id} was cancelled by the reviewer.")


def clear(review_id: str) -> None:
    """Forget a review, once its run has stopped.

    Without this the set grows for the lifetime of the process. Called from the
    pipeline's `finally`, so it runs whether the review completed, failed, or was
    cancelled.
    """
    with _lock:
        _cancelled.discard(review_id)


def reset() -> None:
    """Drop every registration. Tests only — there is no such thing in the app."""
    with _lock:
        _cancelled.clear()
