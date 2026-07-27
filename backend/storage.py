"""Persistence: JSON files and uploaded blobs under `local-data/`.

    local-data/
      uploads/<upload-id>/<filename>
      reviews/<review-id>.json
      status/<review-id>.json

Writes go to a temp file in the same directory and are then renamed, because
rename is atomic on POSIX. Without that, the UI polls `status/` every 1.5s and
would eventually read a half-written file.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
import uuid

import config
from schema import ReviewResult, ReviewStatus

_UPLOADS = "uploads"
_REVIEWS = "reviews"
_STATUS = "status"

# Upload keys are `uploads/<uuid>/<filename>` and are echoed back by the client,
# so they are untrusted input and must never escape the data directory.
_KEY_PATTERN = re.compile(r"^uploads/[0-9a-f-]{36}/[^/\\]{1,255}$")


def _dir(name: str) -> pathlib.Path:
    path = config.DATA_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_atomic(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except BaseException:
        pathlib.Path(temp_name).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #


def save_upload(filename: str, data: bytes) -> str:
    """Store an uploaded file and return its key."""
    safe_name = pathlib.Path(filename).name or "upload"
    key = f"{_UPLOADS}/{uuid.uuid4()}/{safe_name}"
    _write_atomic(config.DATA_DIR / key, data)
    return key


def _upload_path(key: str) -> pathlib.Path:
    """Resolve an upload key, refusing anything that escapes the data directory."""
    if not _KEY_PATTERN.match(key):
        raise ValueError(f"Malformed upload key: {key!r}")
    path = (config.DATA_DIR / key).resolve()
    # Belt and braces: the pattern already forbids separators and `..`, but a
    # symlink inside the data directory could still redirect the resolved path.
    if not path.is_relative_to(config.DATA_DIR):
        raise ValueError(f"Upload key escapes the data directory: {key!r}")
    return path


def get_object(key: str) -> bytes:
    return _upload_path(key).read_bytes()


def object_exists(key: str) -> bool:
    try:
        return _upload_path(key).is_file()
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #


def put_review(result: ReviewResult) -> None:
    _write_atomic(
        _dir(_REVIEWS) / f"{result.review_id}.json",
        result.model_dump_json(indent=2).encode(),
    )


def get_review(review_id: str) -> ReviewResult | None:
    path = _dir(_REVIEWS) / f"{_safe_id(review_id)}.json"
    if not path.is_file():
        return None
    return ReviewResult.model_validate_json(path.read_text())


def list_reviews() -> list[dict[str, object]]:
    """Summaries for the history view, newest first.

    Reads each stored review but returns only what the list renders — findings
    and evidence are the bulk of a review record and the history page shows
    none of it.
    """
    summaries: list[dict[str, object]] = []
    for path in _dir(_REVIEWS).glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            # A truncated file from an interrupted write should not break the
            # whole history page.
            continue
        findings = record.get("findings", [])
        summaries.append(
            {
                "review_id": record.get("review_id", path.stem),
                "title": record.get("title", ""),
                "created_at": record.get("created_at", ""),
                "overall_score": record.get("overall_score", 0.0),
                "open_findings": sum(
                    1 for f in findings if f.get("status") in ("fail", "partial")
                ),
                "high_severity_open": sum(
                    1
                    for f in findings
                    if f.get("severity") == "high"
                    and f.get("status") in ("fail", "partial")
                ),
                "has_delta": record.get("delta") is not None,
                "pillars": [
                    {
                        "framework": pillar.get("framework", ""),
                        "pillar_id": pillar.get("pillar_id", ""),
                        "pillar_name": pillar.get("pillar_name", ""),
                        "score": pillar.get("score", 0.0),
                        "checks_evaluated": pillar.get("checks_evaluated", 0),
                    }
                    for framework in record.get("frameworks", [])
                    for pillar in framework.get("pillars", [])
                ],
            }
        )
    return sorted(summaries, key=lambda item: str(item["created_at"]), reverse=True)


# --------------------------------------------------------------------------- #
# Progress (polled by the UI)
# --------------------------------------------------------------------------- #


def put_status(status: ReviewStatus) -> None:
    status.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_atomic(
        _dir(_STATUS) / f"{status.review_id}.json", status.model_dump_json().encode()
    )


def get_status(review_id: str) -> ReviewStatus | None:
    path = _dir(_STATUS) / f"{_safe_id(review_id)}.json"
    if not path.is_file():
        return None
    return ReviewStatus.model_validate_json(path.read_text())


def _safe_id(review_id: str) -> str:
    """Review ids come from the URL, so they cannot be trusted as filenames."""
    if not re.fullmatch(r"[0-9a-fA-F-]{1,64}", review_id):
        raise ValueError(f"Malformed review id: {review_id!r}")
    return review_id
