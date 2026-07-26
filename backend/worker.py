"""Async worker Lambda: runs the agent pipeline outside the HTTP request.

Invoked with InvocationType='Event' by the API, because a full review takes
minutes and an API Gateway request cannot wait that long. Failures are already
recorded in the status table by the pipeline; re-raising here surfaces them in
CloudWatch too.
"""

from __future__ import annotations

import logging
from typing import Any

from agent import pipeline

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    review_id = event["review_id"]
    log.info("Starting review %s", review_id)

    result = pipeline.run(
        review_id=review_id,
        title=event.get("title", ""),
        document_key=event.get("document_key", ""),
        diagram_key=event.get("diagram_key", ""),
        previous_review_id=event.get("previous_review_id", ""),
    )

    log.info(
        "Review %s complete: score %.1f, %d findings, %s tokens",
        review_id,
        result.overall_score,
        len(result.findings),
        result.token_usage,
    )
    return {"review_id": review_id, "overall_score": result.overall_score}
