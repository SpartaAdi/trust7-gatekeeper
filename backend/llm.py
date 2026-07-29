"""The only module that talks to an LLM provider.

Every model call in the pipeline goes through `complete_json`, so the provider
dependency is one seam rather than a coupling spread across the codebase. Two
adapters sit behind that seam, chosen by `config.LLM_PROVIDER`:

* **openrouter** (default) — OpenAI-compatible client pointed at OpenRouter,
  currently `moonshotai/kimi-k2.6`.
* **anthropic** — the original Claude-direct path, intact and tested. Reverting
  is `LLM_PROVIDER=anthropic` plus a restart; no code change.

Callers speak one internal dialect — Anthropic-shaped `system` and `content`
blocks — and each adapter translates. That keeps `agent/stages.py` and
`ingestion/vision.py` provider-agnostic.

## Schema enforcement

`enforce_schema` runs on the parsed response for **both** providers, and it is
the real guarantee. Provider-side structured output is a request, not a promise:
OpenRouter's own documentation says that among models accepting a JSON schema,
"some guarantee schema-conforming output, while others translate your schema or
treat it as a strong hint". A hint is not enforcement, so we validate what came
back rather than trusting what was asked for.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import logging
import re
import time
from collections import deque
from typing import Any, Iterable

import config

log = logging.getLogger(__name__)

# Effort is the primary cost lever. Reasoning-heavy stages get more, mechanical
# ones get less; see agent/stages.py for the per-stage choice.
Effort = str

# Structured outputs live on the beta endpoint in anthropic 0.75.0 — `output_config`
# is not a parameter of the non-beta `messages.create` at all — and the server
# gates it behind this flag. Anthropic path only; OpenRouter uses response_format.
_STRUCTURED_OUTPUTS_BETA = "structured-outputs-2025-11-13"


@dataclasses.dataclass(frozen=True)
class ServedCall:
    """One completed model call and who served it."""

    label: str
    provider: str
    model: str
    finish_reason: str
    output_tokens: int
    seconds: float
    allowed: bool


class ModelRefusal(RuntimeError):
    """The request was declined by the model's safety classifiers."""


class SchemaViolation(RuntimeError):
    """The response did not conform to the requested schema after repair."""


class TruncatedResponse(RuntimeError):
    """The model hit its output limit before finishing the JSON."""


class ProviderNotAllowed(RuntimeError):
    """A response came back from a provider outside the configured allow-list."""


# --------------------------------------------------------------------------- #
# Which provider actually served each call
#
# OpenRouter returns the serving provider on the response body. Recording it is
# the only way to know that `order` + `allow_fallbacks: false` is being honoured
# rather than merely requested — the request is a preference, the response is the
# fact. Without this, a silent fall-through to Moonshot direct looks identical to
# a successful pinned route, at 1.5x the prompt price.
#
# The log line is the durable record. `ROUTE_LOG` is a bounded in-process tail for
# diagnostics and for the e2e script to print; it is deliberately NOT per-review
# attribution — two concurrent reviews interleave in it.
# --------------------------------------------------------------------------- #

ROUTE_LOG_LIMIT = 64
ROUTE_LOG: deque[ServedCall] = deque(maxlen=ROUTE_LOG_LIMIT)


def route_log() -> list[ServedCall]:
    """The recent tail of served calls, oldest first."""
    return list(ROUTE_LOG)


def reset_route_log() -> None:
    ROUTE_LOG.clear()


def _slug(name: str) -> str:
    """Normalise a provider name or slug for comparison.

    The request takes slugs (`sail-research`); the response returns display names
    (`Sail Research`). Comparing them raw reports a violation on every call.
    """
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _served_provider(response: Any) -> str:
    """The provider OpenRouter says served this call.

    `provider` is an OpenRouter extension, so it is absent from the OpenAI SDK's
    declared model; pydantic keeps it in `model_extra`. Returns "" when the field
    is missing rather than guessing, so an unknown route is visibly unknown.
    """
    direct = getattr(response, "provider", None)
    if isinstance(direct, str) and direct:
        return direct
    extra = getattr(response, "model_extra", None) or {}
    value = extra.get("provider")
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #

def complete_json(
    *,
    system: list[dict[str, Any]],
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    effort: Effort = "high",
    max_tokens: int = 16000,
    label: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    """Run one structured-output request and return (parsed JSON, token usage).

    `label` names the call in the route log — it identifies which of the pipeline's
    calls a log line belongs to and is otherwise unused.

    `system` blocks are passed through verbatim on the Anthropic path so callers
    can place their own `cache_control` breakpoint on the stable prefix. On the
    OpenRouter path they are flattened into one system message — Moonshot caching
    is automatic and takes no breakpoints (OpenRouter: "Prompt caching with
    Moonshot AI is automated and does not require any additional configuration").
    """
    if config.LLM_PROVIDER == "openrouter":
        parsed, usage = _openrouter_complete(
            system=system, content=content, schema=schema,
            effort=effort, max_tokens=max_tokens, label=label,
        )
    else:
        parsed, usage = _anthropic_complete(
            system=system, content=content, schema=schema,
            effort=effort, max_tokens=max_tokens,
        )

    return enforce_schema(parsed, schema), usage


def image_block(media_type: str, data_b64: str) -> dict[str, Any]:
    """The canonical internal image block. Each adapter translates it."""
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


# --------------------------------------------------------------------------- #
# Schema enforcement — the layer that actually holds
# --------------------------------------------------------------------------- #

def enforce_schema(payload: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Prune additive violations, then validate what remains, strictly.

    Two classes of deviation, treated differently on purpose:

    * **Additive** — a key the schema forbids via `additionalProperties: false`.
      Dropping it loses nothing we asked for, so it is repaired and logged rather
      than failing a review that is otherwise complete and already paid for. This
      mirrors how `agent/stages.py` already discards unrecognised `check_id`s.
    * **Substantive** — a missing required field, a wrong type, a value outside
      an enum. These change meaning, so they raise. A findings list with an
      invented status is worse than no findings list.
    """
    import jsonschema

    pruned, removed = _prune_unknown(payload, schema)
    if removed:
        log.warning(
            "Response carried %d field(s) the schema forbids; dropped: %s",
            len(removed), ", ".join(sorted(removed)[:10]),
        )

    try:
        jsonschema.validate(pruned, schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "(root)"
        raise SchemaViolation(
            f"Model response violates the requested schema at {location}: "
            f"{exc.message}"
        ) from exc

    return pruned


def _prune_unknown(
    value: Any, schema: dict[str, Any], path: str = "$"
) -> tuple[Any, set[str]]:
    """Recursively drop object keys not permitted by `additionalProperties: false`."""
    removed: set[str] = set()

    if schema.get("type") == "object" and isinstance(value, dict):
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            for key in list(value):
                if key not in properties:
                    del value[key]
                    removed.add(f"{path}.{key}")
        for key, sub_schema in properties.items():
            if key in value and isinstance(sub_schema, dict):
                value[key], child = _prune_unknown(
                    value[key], sub_schema, f"{path}.{key}"
                )
                removed |= child

    elif schema.get("type") == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                value[index], child = _prune_unknown(
                    item, item_schema, f"{path}[{index}]"
                )
                removed |= child

    return value, removed


# --------------------------------------------------------------------------- #
# OpenRouter adapter
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def _openrouter_client() -> Any:
    import openai

    return openai.OpenAI(
        api_key=config.openrouter_api_key(),
        base_url=config.OPENROUTER_BASE_URL,
        # A stalled call must fail fast rather than hang. Without this the SDK waits
        # on its default 600s per attempt, and a hung upstream looks like a slow one.
        timeout=config.OPENROUTER_TIMEOUT_SECONDS,
        # The SDK retries twice by default, and `_openrouter_create_with_retry`
        # already retries once itself. Left at the default, one stalled call could
        # burn 6 x timeout before surfacing — the multiplication is what turns a
        # 120s ceiling back into an hour. One retry, ours, deliberately placed.
        max_retries=0,
    )


def _system_text(system: list[dict[str, Any]]) -> str:
    """Flatten the internal system blocks into one message.

    `cache_control` is dropped rather than translated: Moonshot caching via
    OpenRouter is automatic and accepts no breakpoints, so there is nothing to
    map it onto. The prefix is still byte-identical between reviews, which is
    what implicit caching keys on.
    """
    return "\n\n".join(
        block["text"]
        for block in system
        if block.get("type") == "text" and block.get("text")
    )


def _user_parts(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate internal content blocks into OpenAI-compatible parts.

    Text is emitted before images regardless of the order the caller supplied,
    because OpenRouter's image guidance is explicit: "Due to how the content is
    parsed, we recommend sending the text prompt first, then the images." Doing
    the reordering here rather than at the call site leaves the Anthropic adapter
    free to keep Anthropic's opposite recommendation for a single image.
    """
    texts: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []

    for block in content:
        if block.get("type") == "text":
            texts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            source = block["source"]
            images.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{source['media_type']};base64,{source['data']}"
                    },
                }
            )
        else:  # pragma: no cover — defensive
            raise ValueError(f"Cannot translate content block: {block.get('type')!r}")

    return texts + images


def _schema_name(schema: dict[str, Any]) -> str:
    """A stable, API-legal name for the schema. Must match ^[a-zA-Z0-9_-]+$."""
    required = schema.get("required") or []
    stem = "_".join(str(key) for key in list(required)[:2]) or "response"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", stem)[:60] or "response"


def _openrouter_request(
    *,
    system: list[dict[str, Any]],
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    effort: Effort,
    max_tokens: int,
) -> dict[str, Any]:
    capped = min(max_tokens, config.OPENROUTER_MAX_COMPLETION_TOKENS)
    if capped > config.OPENROUTER_LOWEST_ENDPOINT_COMPLETION_CAP:
        # Not fatal: most endpoints serving this model advertise 262k completion
        # tokens. Logged because if routing ever does land on a small one, the
        # symptom is a truncated response and this line is the explanation.
        log.debug(
            "Requesting %d completion tokens, above the lowest known endpoint cap "
            "of %d; routing must avoid such endpoints.",
            capped, config.OPENROUTER_LOWEST_ENDPOINT_COMPLETION_CAP,
        )

    provider: dict[str, Any] = {
        # Ordered allow-list. Slugs are verified against OpenRouter's provider and
        # endpoint APIs in config.py — see the note there.
        "order": list(config.OPENROUTER_PROVIDER_ORDER),
        # Off by default: a request that none of the ordered providers can serve
        # must FAIL rather than route somewhere unlisted. See config.py.
        "allow_fallbacks": config.OPENROUTER_ALLOW_FALLBACKS,
        # MANDATORY. Structured output support is per endpoint, not per model: of
        # the 22 providers serving kimi-k2.6, at least one reports no
        # structured_outputs and another no response_format. Without this,
        # routing can silently land on one of them and the schema is ignored.
        "require_parameters": True,
    }
    if config.OPENROUTER_IGNORE_PROVIDERS:
        provider["ignore"] = list(config.OPENROUTER_IGNORE_PROVIDERS)

    return {
        "model": config.MODEL,
        "messages": [
            {"role": "system", "content": _system_text(system)},
            {"role": "user", "content": _user_parts(content)},
        ],
        "max_tokens": capped,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name(schema),
                "strict": True,
                "schema": schema,
            },
        },
        "extra_body": {
            "provider": provider,
            # The replacement for Anthropic's per-stage `output_config.effort`.
            # OpenRouter's unified `reasoning` parameter takes the same effort
            # vocabulary and maps it to the nearest level the endpoint supports,
            # so the "evaluate thinks harder than classify" intent survives even
            # though kimi-k2.6 exposes no `reasoning_effort` of its own.
            # `exclude` keeps reasoning text out of the response: we never read
            # it, and reasoning tokens bill as output tokens.
            "reasoning": {"effort": effort, "exclude": True},
        },
    }


def _record_route(response: Any, label: str, seconds: float) -> ServedCall:
    """Log, record, and — by default — HARD-FAIL on a provider outside the lock.

    This used to log at ERROR and carry on, on the reasoning that a paid response is
    still a usable response. That reasoning was wrong in practice: a run was served
    by Phala, which is not in the configured order, the ERROR line went unread, and
    the review completed looking exactly like a correctly pinned one. A route that
    ignored the lock invalidates every cost and output-ceiling assumption built on
    it, so it now raises.

    An UNREPORTED provider raises too. The point of the lock is provability, and a
    response that does not say who served it cannot be shown to have honoured it —
    "no evidence of a violation" is not the same as "evidence of compliance". A run
    has already come back with no provider recorded at all.

    `OPENROUTER_ENFORCE_PROVIDER_LOCK=0` downgrades both back to a log line, for the
    case where a provider stops reporting the field and shipping matters more than
    proving the route.
    """
    provider = _served_provider(response)
    allow_list = {_slug(name) for name in config.OPENROUTER_PROVIDER_ORDER}
    # No allow-list, or fallbacks deliberately on, means anything is permitted.
    allowed = (
        True
        if not allow_list or config.OPENROUTER_ALLOW_FALLBACKS
        else _slug(provider) in allow_list
    )

    choice = response.choices[0] if response.choices else None
    usage = getattr(response, "usage", None)
    served = ServedCall(
        label=label or "unlabelled",
        provider=provider or "unreported",
        model=getattr(response, "model", "") or "",
        finish_reason=getattr(choice, "finish_reason", "") or "",
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        seconds=seconds,
        allowed=allowed,
    )
    ROUTE_LOG.append(served)

    log.info(
        "route call=%s provider=%s model=%s finish=%s out_tokens=%d %.1fs",
        served.label, served.provider, served.model,
        served.finish_reason, served.output_tokens, served.seconds,
    )
    if allowed:
        return served

    # The request id is the only handle on OpenRouter's activity log, so it goes in
    # the message rather than only in a debug line — diagnosing this after the fact
    # without it is guesswork.
    request_id = getattr(response, "id", "") or "unreported"
    detail = (
        f"reported no serving provider (request {request_id})"
        if served.provider == "unreported"
        else f"was served by {served.provider!r} (request {request_id})"
    )
    message = (
        f"Provider lock violated: call {served.label!r} {detail}, which is not in "
        f"the configured order {config.OPENROUTER_PROVIDER_ORDER} sent with "
        f"allow_fallbacks={config.OPENROUTER_ALLOW_FALLBACKS}. The routing directive "
        f"was not honoured, so cost and output-ceiling assumptions no longer hold."
    )
    log.error("route VIOLATION %s", message)
    if config.OPENROUTER_ENFORCE_PROVIDER_LOCK:
        raise ProviderNotAllowed(message)
    return served


def _openrouter_complete(
    *,
    system: list[dict[str, Any]],
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    effort: Effort,
    max_tokens: int,
    label: str = "",
) -> tuple[dict[str, Any], dict[str, int]]:
    request = _openrouter_request(
        system=system, content=content, schema=schema,
        effort=effort, max_tokens=max_tokens,
    )
    started = time.monotonic()
    response = _openrouter_create_with_retry(request)
    # Recorded before the checks below, so a refusal or a truncation is still
    # attributed to the provider that produced it — those are exactly the cases
    # where knowing the endpoint matters most.
    _record_route(response, label, time.monotonic() - started)
    choice = response.choices[0]

    if getattr(choice.message, "refusal", None):
        raise ModelRefusal(f"Model declined the request. {choice.message.refusal}")
    if choice.finish_reason == "content_filter":
        raise ModelRefusal("Model response was blocked by a content filter.")
    if choice.finish_reason == "length":
        raise TruncatedResponse(
            f"Model hit the {request['max_tokens']}-token output limit before "
            "completing the JSON output; raise max_tokens for this stage or "
            "split it into smaller requests."
        )

    text = choice.message.content or ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaViolation(
            f"Model returned content that is not JSON ({exc}); "
            f"first 200 characters: {text[:200]!r}"
        ) from exc

    return parsed, _openrouter_usage(response)


def _openrouter_usage(response: Any) -> dict[str, int]:
    """Map OpenAI-shaped usage onto the keys the rest of the app already uses."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}

    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details else 0
    return {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "cache_read_input_tokens": cached or 0,
        "cache_creation_input_tokens": 0,
    }


def _openrouter_create(request: dict[str, Any]) -> Any:
    """The single point where this codebase calls the OpenAI-compatible SDK."""
    return _openrouter_client().chat.completions.create(**request)


def _openrouter_create_with_retry(request: dict[str, Any]) -> Any:
    """One retry, ours, on a transient failure. Never more.

    Timeouts and connection errors are retried here as well as HTTP 5xx, because
    setting the SDK's `max_retries=0` — which was necessary to stop it multiplying
    the 120s deadline by three — otherwise leaves a single dropped connection fatal.
    The bound is deliberate and arithmetic: at most two attempts, so at most
    2 x OPENROUTER_TIMEOUT_SECONDS of wall clock per call, and nothing can turn a
    120s ceiling back into an hour.
    """
    import openai

    try:
        return _openrouter_create(request)
    except (openai.APITimeoutError, openai.APIConnectionError) as exc:
        log.warning(
            "OpenRouter call %s after %.0fs; retrying once.",
            type(exc).__name__, config.OPENROUTER_TIMEOUT_SECONDS,
        )
        return _openrouter_create(request)
    except openai.APIStatusError as exc:
        if not _is_transient(exc.status_code):
            raise
        log.warning("Transient OpenRouter error %s; retrying once.", exc.status_code)
        return _openrouter_create(request)


# --------------------------------------------------------------------------- #
# Anthropic adapter — intact, no longer the default
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def _client() -> Any:
    import anthropic

    # Same deadline as the OpenRouter path: this one is the fallback provider, and a
    # fallback that can hang for an hour is not a fallback.
    return anthropic.Anthropic(
        api_key=config.anthropic_api_key(),
        timeout=config.OPENROUTER_TIMEOUT_SECONDS,
        max_retries=0,
    )


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


def _anthropic_complete(
    *,
    system: list[dict[str, Any]],
    content: list[dict[str, Any]],
    schema: dict[str, Any],
    effort: Effort,
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    request: dict[str, Any] = {
        "model": config.ANTHROPIC_MODEL,
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
        raise TruncatedResponse(
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
    import anthropic

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


def _is_transient(status_code: int | None) -> bool:
    """Statuses where the identical request might succeed on a second attempt."""
    if status_code is None:
        return False
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
