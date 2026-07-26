"""S3 and DynamoDB access.

Results and progress records are stored as JSON strings rather than nested
DynamoDB maps: no Decimal round-tripping, one attribute to read, and the item
shape stays decoupled from the Pydantic models.
"""

from __future__ import annotations

import functools
import json
import time
from typing import Any

import boto3

import config
from schema import ReviewResult, ReviewStatus


@functools.lru_cache(maxsize=1)
def _s3():
    return boto3.client("s3")


@functools.lru_cache(maxsize=1)
def _dynamodb():
    return boto3.resource("dynamodb")


@functools.lru_cache(maxsize=1)
def _reviews_table():
    return _dynamodb().Table(config.REVIEWS_TABLE)


@functools.lru_cache(maxsize=1)
def _status_table():
    return _dynamodb().Table(config.REVIEW_STATUS_TABLE)


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #


def presigned_put(key: str, content_type: str) -> str:
    """A short-lived URL the browser uploads straight to S3 with.

    Uploads bypass Lambda entirely, so the 6 MB invocation payload limit doesn't
    cap document or diagram size.
    """
    return _s3().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": config.UPLOADS_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=config.UPLOAD_URL_TTL_SECONDS,
    )


def get_object(key: str) -> bytes:
    return _s3().get_object(Bucket=config.UPLOADS_BUCKET, Key=key)["Body"].read()


def object_exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=config.UPLOADS_BUCKET, Key=key)
        return True
    except _s3().exceptions.ClientError:
        return False


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #


def put_review(result: ReviewResult) -> None:
    _reviews_table().put_item(
        Item={
            "review_id": result.review_id,
            "created_at": result.created_at,
            "overall_score": str(result.overall_score),
            "result": result.model_dump_json(),
        }
    )


def get_review(review_id: str) -> ReviewResult | None:
    item = _reviews_table().get_item(Key={"review_id": review_id}).get("Item")
    if not item:
        return None
    return ReviewResult.model_validate_json(item["result"])


# --------------------------------------------------------------------------- #
# Progress (polled by the UI)
# --------------------------------------------------------------------------- #


def put_status(status: ReviewStatus) -> None:
    status.updated_at = _now()
    _status_table().put_item(
        Item={
            "review_id": status.review_id,
            "status": status.model_dump_json(),
            "expires_at": int(time.time()) + config.STATUS_TTL_SECONDS,
        }
    )


def get_status(review_id: str) -> ReviewStatus | None:
    item = _status_table().get_item(Key={"review_id": review_id}).get("Item")
    if not item:
        return None
    return ReviewStatus.model_validate_json(item["status"])


# --------------------------------------------------------------------------- #
# Worker dispatch
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=1)
def _lambda_client():
    return boto3.client("lambda")


def invoke_worker(payload: dict[str, Any]) -> None:
    """Kick off the pipeline asynchronously so the API can return 202 immediately.

    An analysis run takes minutes; an HTTP API request cannot wait that long.
    """
    _lambda_client().invoke(
        FunctionName=config.WORKER_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
