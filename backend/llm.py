"""The only module that talks to the Anthropic SDK.

Every model call in the pipeline goes through `complete_json`, so the provider
dependency is one seam rather than a coupling spread across the codebase.

Claude API direct — not Bedrock. Pay-per-token, no provisioned throughput.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, Iterable

import anthropic

import config

log = logging.getLogger(__name__)

# Effort is the primary cost lever. Reasoning-heavy stages get more, mechanical
# ones get less; see agent/stages.py for the per-stage choice.
Effort = str

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelRefusal(RuntimeError):
    """The request was declined by the model's safety classifiers."""


@functools.lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=config.anthropic_api_key())


def _text_of(message: Any) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def _usage_of(message: Any) -> dict[str, int]:
    usage = message.usage
    return {
        "input_tokens": usage.input_tokens or 0,
        "output_tokens": usage.output_tokens or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def complete_json(
    *,
    system: list[dict[str, Any]],
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    effort: Effort = "high",
    max_tokens: int = 16000,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run one structured-output request and return (parsed JSON, token usage).

    `system` blocks are sent verbatim so callers can place their own
    `cache_control` breakpoint on the stable prefix — the rubric is identical on
    every request, and caching it is most of the cost saving available here.
    """
    request: dict[str, Any] = {
        "model": config.MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    }

    message = _call_with_fallback(request)

    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "explanation", "") or ""
        raise ModelRefusal(f"Model declined the request. {detail}".strip())
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            "Model response hit max_tokens before completing the JSON output; "
            "raise max_tokens for this stage."
        )

    return json.loads(_text_of(message)), _usage_of(message)


def _call_with_fallback(request: dict[str, Any]) -> Any:
    """Send the request with a server-side refusal fallback, degrading if unavailable.

    A benign governance review can trip a safety classifier when the design under
    review is itself security tooling, so the fallback is worth having. It sits
    behind a beta flag, so a rejection of the flag falls back to a plain call
    rather than failing the review.
    """
    try:
        return _client().beta.messages.create(
            **request, betas=[_FALLBACK_BETA], fallbacks="default"
        )
    except anthropic.BadRequestError as exc:
        if "fallback" not in str(exc).lower():
            raise
        log.warning("Server-side fallbacks unavailable, retrying without: %s", exc)
        return _client().messages.create(**request)


def image_block(media_type: str, data_b64: str) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data_b64},
    }


def sum_usage(usages: Iterable[dict[str, int]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            total[key] = total.get(key, 0) + value
    return total
