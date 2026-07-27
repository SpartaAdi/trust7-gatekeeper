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

# Structured outputs live on the beta endpoint in anthropic 0.75.0 — `output_config`
# is not a parameter of the non-beta `messages.create` at all — and the server
# gates it behind this flag. The SDK's own `parse()` helper appends the same one.
_STRUCTURED_OUTPUTS_BETA = "structured-outputs-2025-11-13"


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

    message = _create_with_retry(request)

    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "explanation", "") or ""
        raise ModelRefusal(f"Model declined the request. {detail}".strip())
    if message.stop_reason == "max_tokens":
        raise RuntimeError(
            "Model response hit max_tokens before completing the JSON output; "
            "raise max_tokens for this stage."
        )

    return json.loads(_text_of(message)), _usage_of(message)


def _create(request: dict[str, Any]) -> Any:
    """The single point where this codebase calls the Anthropic SDK."""
    return _client().beta.messages.create(
        **request, betas=[_STRUCTURED_OUTPUTS_BETA]
    )


def _create_with_retry(request: dict[str, Any]) -> Any:
    """Send the request, retrying once on failure.

    Two distinct failure modes, each with the retry that actually helps:

    `BadRequestError` usually means the server rejected a parameter this SDK or
    account cannot use. Repeating the same call would fail identically, so the
    retry drops the optional tuning parameters and keeps only what the caller
    depends on — the JSON schema, without which the response would not parse.

    `APIStatusError` covers overload and server faults that outlived the SDK's
    own retries (it retries 429/5xx twice by default), so here one plain repeat
    is the whole strategy — but only for statuses that can actually change on a
    retry. Repeating a 401 or a 404 just doubles the latency before the same
    failure.
    """
    try:
        return _create(request)
    except anthropic.BadRequestError as exc:
        simplified = _without_tuning(request)
        if simplified is None:
            raise
        log.warning("Request rejected (%s); retrying without tuning parameters.", exc)
        return _create(simplified)
    except anthropic.APIStatusError as exc:
        if not _is_transient(exc.status_code):
            raise
        log.warning("Transient API error %s; retrying once.", exc.status_code)
        return _create(request)


def _is_transient(status_code: int) -> bool:
    """Statuses where the identical request might succeed on a second attempt."""
    return status_code >= 500 or status_code in (408, 409, 429)


def _without_tuning(request: dict[str, Any]) -> dict[str, Any] | None:
    """Strip optional parameters, keeping the output schema. None if nothing to strip.

    `output_config.format` stays: the caller parses the response as JSON, so
    dropping the schema would turn a clear API error into a JSON decode failure.
    """
    simplified = {k: v for k, v in request.items() if k != "thinking"}
    output_config = simplified.get("output_config")
    if isinstance(output_config, dict) and "effort" in output_config:
        simplified["output_config"] = {
            k: v for k, v in output_config.items() if k != "effort"
        }
    return simplified if simplified != request else None


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
