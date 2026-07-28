"""Guards on the OpenRouter adapter — the default provider.

Everything the verification step turned up has a test here, because each finding
was a way the swap could look fine and be silently wrong:

* `provider.require_parameters` missing → routing lands on one of the endpoints
  that ignores the JSON schema, and findings come back unvalidated;
* the beta header surviving → a parameter Anthropic understands and OpenRouter
  does not;
* per-stage effort quietly dropped → every stage costs the same and evaluates
  worse;
* images sent before text → against OpenRouter's documented parsing guidance;
* schema treated as a guarantee → a provider that treats it as "a strong hint"
  returns something else and nothing notices.
"""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import openai
import pytest

import config
import llm

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pass", "fail"]},
                },
                "required": ["check_id", "status"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

MINIMAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class _Message:
    def __init__(self, content: str, refusal: str | None = None) -> None:
        self.content = content
        self.refusal = refusal


class _Choice:
    def __init__(self, content: str, finish_reason: str = "stop",
                 refusal: str | None = None) -> None:
        self.message = _Message(content, refusal)
        self.finish_reason = finish_reason


class _PromptDetails:
    def __init__(self, cached: int) -> None:
        self.cached_tokens = cached


class _Usage:
    def __init__(self, cached: int = 0) -> None:
        self.prompt_tokens = 1200
        self.completion_tokens = 340
        self.prompt_tokens_details = _PromptDetails(cached)


class _Response:
    def __init__(self, content: str, finish_reason: str = "stop",
                 refusal: str | None = None, cached: int = 0) -> None:
        self.choices = [_Choice(content, finish_reason, refusal)]
        self.usage = _Usage(cached)


@pytest.fixture(autouse=True)
def _openrouter_selected(monkeypatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(config, "MODEL", "moonshotai/kimi-k2.6")


def sent_request(monkeypatch, *, content: str = '{"ok": true}',
                 schema: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Capture the exact kwargs handed to the OpenAI-compatible client."""
    captured: dict[str, Any] = {}

    class FakeCompletions:
        def create(self, **request: Any) -> _Response:
            captured.update(request)
            return _Response(content)

    fake = type(
        "FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()}
    )()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)

    llm.complete_json(
        system=kwargs.pop("system", [{"type": "text", "text": "system prompt"}]),
        content=kwargs.pop("content_blocks", [{"type": "text", "text": "user prompt"}]),
        schema=schema or MINIMAL_SCHEMA,
        effort=kwargs.pop("effort", "high"),
        max_tokens=kwargs.pop("max_tokens", 16000),
    )
    return captured


# --------------------------------------------------------------------------- #
# 1. require_parameters — mandatory, not optional
# --------------------------------------------------------------------------- #

def test_every_request_requires_parameter_support_from_the_endpoint(monkeypatch) -> None:
    """Without this, routing can pick an endpoint that ignores the schema."""
    request = sent_request(monkeypatch)

    assert request["extra_body"]["provider"]["require_parameters"] is True


def test_ignored_providers_are_forwarded_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENROUTER_IGNORE_PROVIDERS", ["DeepInfra", "Sail Research"])

    request = sent_request(monkeypatch)

    assert request["extra_body"]["provider"]["ignore"] == ["DeepInfra", "Sail Research"]


def test_no_ignore_key_when_nothing_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENROUTER_IGNORE_PROVIDERS", [])

    assert "ignore" not in sent_request(monkeypatch)["extra_body"]["provider"]


# --------------------------------------------------------------------------- #
# 2. response_format replaces the beta header
# --------------------------------------------------------------------------- #

def test_structured_output_uses_response_format_json_schema(monkeypatch) -> None:
    # The stubbed body must satisfy SCHEMA, or enforcement rejects it first.
    request = sent_request(monkeypatch, schema=SCHEMA, content='{"findings": []}')

    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == SCHEMA
    assert response_format["json_schema"]["name"]


def test_the_schema_name_is_api_legal(monkeypatch) -> None:
    """OpenRouter requires the name to match ^[a-zA-Z0-9_-]+$."""
    import re

    request = sent_request(monkeypatch, schema=SCHEMA, content='{"findings": []}')
    name = request["response_format"]["json_schema"]["name"]

    assert re.fullmatch(r"[a-zA-Z0-9_-]+", name), name


def test_the_anthropic_beta_header_is_not_sent(monkeypatch) -> None:
    """`structured-outputs-2025-11-13` means nothing to OpenRouter."""
    request = sent_request(monkeypatch)

    assert "betas" not in request
    assert llm._STRUCTURED_OUTPUTS_BETA not in str(request)


def test_anthropic_only_parameters_are_not_sent(monkeypatch) -> None:
    request = sent_request(monkeypatch)

    assert "output_config" not in request
    assert "thinking" not in request
    assert "system" not in request, "system must be a message, not a top-level field"


def test_the_configured_kimi_model_is_used(monkeypatch) -> None:
    assert sent_request(monkeypatch)["model"] == "moonshotai/kimi-k2.6"


def test_the_request_binds_to_the_installed_openai_signature(monkeypatch) -> None:
    """Catches the `fallbacks` class of bug on this adapter too: every kwarg we
    send must exist on the method we send it to."""
    client = openai.OpenAI(api_key="test-key-not-used", base_url=config.OPENROUTER_BASE_URL)
    signature = inspect.signature(client.chat.completions.create)

    signature.bind(**sent_request(monkeypatch))


# --------------------------------------------------------------------------- #
# 3. Per-stage effort survives the move
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("effort", ["high", "medium", "low"])
def test_effort_is_carried_as_the_unified_reasoning_parameter(monkeypatch, effort) -> None:
    """kimi-k2.6 exposes no `reasoning_effort`; OpenRouter's `reasoning.effort`
    takes the same vocabulary and maps it to the nearest supported level."""
    request = sent_request(monkeypatch, effort=effort)

    assert request["extra_body"]["reasoning"]["effort"] == effort


def test_reasoning_text_is_excluded_from_the_response(monkeypatch) -> None:
    """We never read it, and reasoning tokens bill as output tokens."""
    assert sent_request(monkeypatch)["extra_body"]["reasoning"]["exclude"] is True


def test_reasoning_effort_is_not_sent_as_a_top_level_parameter(monkeypatch) -> None:
    """The parameter kimi-k2.6 does not support must not be the one we send."""
    assert "reasoning_effort" not in sent_request(monkeypatch)


# --------------------------------------------------------------------------- #
# 4. Vision: data URI, and text before image
# --------------------------------------------------------------------------- #

def test_an_image_becomes_a_base64_data_uri_image_url_part(monkeypatch) -> None:
    request = sent_request(
        monkeypatch,
        content_blocks=[
            {"type": "text", "text": "Extract this diagram."},
            llm.image_block("image/png", "QUJD"),
        ],
    )

    parts = request["messages"][1]["content"]
    image = next(part for part in parts if part["type"] == "image_url")
    assert image["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_text_is_sent_before_the_image_even_when_supplied_after(monkeypatch) -> None:
    """OpenRouter: "we recommend sending the text prompt first, then the images."
    The adapter reorders so no caller has to remember."""
    request = sent_request(
        monkeypatch,
        content_blocks=[
            llm.image_block("image/jpeg", "QUJD"),
            {"type": "text", "text": "Extract this diagram."},
        ],
    )

    kinds = [part["type"] for part in request["messages"][1]["content"]]
    assert kinds == ["text", "image_url"]


@pytest.mark.parametrize(
    "media_type", ["image/png", "image/jpeg", "image/webp", "image/gif"]
)
def test_every_media_type_the_uploader_accepts_is_translatable(monkeypatch, media_type) -> None:
    request = sent_request(
        monkeypatch,
        content_blocks=[
            {"type": "text", "text": "go"},
            llm.image_block(media_type, "QUJD"),
        ],
    )

    parts = request["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith(f"data:{media_type};base64,")


def test_system_blocks_are_flattened_into_one_system_message(monkeypatch) -> None:
    """cache_control has nothing to map onto — Moonshot caching is automatic."""
    request = sent_request(
        monkeypatch,
        system=[
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second", "cache_control": {"type": "ephemeral"}},
        ],
    )

    system = request["messages"][0]
    assert system["role"] == "system"
    assert system["content"] == "first\n\nsecond"
    assert "cache_control" not in str(request)


# --------------------------------------------------------------------------- #
# 5. max_tokens ceiling
# --------------------------------------------------------------------------- #

def test_max_tokens_is_capped_by_configuration(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENROUTER_MAX_COMPLETION_TOKENS", 20_000)

    assert sent_request(monkeypatch, max_tokens=32_000)["max_tokens"] == 20_000


def test_a_request_below_the_ceiling_is_left_alone(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENROUTER_MAX_COMPLETION_TOKENS", 32_000)

    assert sent_request(monkeypatch, max_tokens=16_000)["max_tokens"] == 16_000


def test_hitting_the_output_limit_raises_rather_than_returning_half_a_json(
    monkeypatch,
) -> None:
    """finish_reason=length is the only signal that a 16,384-cap endpoint truncated
    us; without this the failure would surface as a JSON decode error."""
    class FakeCompletions:
        def create(self, **request: Any) -> _Response:
            return _Response('{"findings": [{"check_id": "sec', finish_reason="length")

    fake = type(
        "FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()}
    )()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)

    with pytest.raises(llm.TruncatedResponse) as caught:
        llm.complete_json(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=SCHEMA,
            effort="high",
            max_tokens=16_000,
        )

    assert "split" in str(caught.value)


# --------------------------------------------------------------------------- #
# Refusals, usage, retries
# --------------------------------------------------------------------------- #

def _client_returning(response: _Response, monkeypatch) -> None:
    class FakeCompletions:
        def create(self, **request: Any) -> _Response:
            return response

    fake = type(
        "FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()}
    )()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)


def _run(schema: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    return llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=schema or MINIMAL_SCHEMA,
        effort="medium",
        max_tokens=16_000,
    )


def test_a_refusal_is_surfaced_as_a_refusal(monkeypatch) -> None:
    _client_returning(_Response("", refusal="I cannot help with that."), monkeypatch)

    with pytest.raises(llm.ModelRefusal):
        _run()


def test_a_content_filter_stop_is_surfaced_as_a_refusal(monkeypatch) -> None:
    _client_returning(_Response("{}", finish_reason="content_filter"), monkeypatch)

    with pytest.raises(llm.ModelRefusal):
        _run()


def test_non_json_content_raises_a_schema_violation_with_a_sample(monkeypatch) -> None:
    _client_returning(_Response("Certainly! Here is the JSON you asked for:"), monkeypatch)

    with pytest.raises(llm.SchemaViolation) as caught:
        _run()

    assert "Certainly" in str(caught.value)


def test_usage_is_mapped_onto_the_keys_the_app_already_uses(monkeypatch) -> None:
    _client_returning(_Response('{"ok": true}', cached=800), monkeypatch)

    _, usage = _run()

    assert usage == {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 0,
    }


def _status_error(status: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return openai.APIStatusError(
        "boom", response=httpx.Response(status, request=request), body=None
    )


def test_a_transient_error_is_retried_once(monkeypatch) -> None:
    calls: list[int] = []

    def fake_create(request: dict[str, Any]) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise _status_error(503)
        return "recovered"

    monkeypatch.setattr(llm, "_openrouter_create", fake_create)

    assert llm._openrouter_create_with_retry({}) == "recovered"
    assert len(calls) == 2


def test_an_authentication_error_is_not_retried(monkeypatch) -> None:
    calls: list[int] = []

    def fake_create(request: dict[str, Any]) -> Any:
        calls.append(1)
        raise _status_error(401)

    monkeypatch.setattr(llm, "_openrouter_create", fake_create)

    with pytest.raises(openai.APIStatusError):
        llm._openrouter_create_with_retry({})
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Schema enforcement — applies to whichever provider is selected
# --------------------------------------------------------------------------- #

def test_unknown_fields_are_pruned_rather_than_failing_a_paid_review(monkeypatch) -> None:
    """Additive deviation loses nothing we asked for, so it is repaired."""
    _client_returning(
        _Response(
            '{"findings": [{"check_id": "sec_a", "status": "fail", '
            '"confidence": 0.9}], "extra_top_level": 1}'
        ),
        monkeypatch,
    )

    parsed, _ = _run(SCHEMA)

    assert parsed == {"findings": [{"check_id": "sec_a", "status": "fail"}]}


def test_a_missing_required_field_raises(monkeypatch) -> None:
    _client_returning(_Response('{"findings": [{"check_id": "sec_a"}]}'), monkeypatch)

    with pytest.raises(llm.SchemaViolation) as caught:
        _run(SCHEMA)

    assert "status" in str(caught.value)


def test_a_value_outside_the_enum_raises(monkeypatch) -> None:
    """The defence that matters if a provider treats the schema as a hint."""
    _client_returning(
        _Response('{"findings": [{"check_id": "sec_a", "status": "probably_fine"}]}'),
        monkeypatch,
    )

    with pytest.raises(llm.SchemaViolation) as caught:
        _run(SCHEMA)

    assert "probably_fine" in str(caught.value)


def test_a_wrong_type_raises(monkeypatch) -> None:
    _client_returning(_Response('{"findings": "not an array"}'), monkeypatch)

    with pytest.raises(llm.SchemaViolation):
        _run(SCHEMA)


def test_enforcement_reports_where_the_violation_was(monkeypatch) -> None:
    _client_returning(
        _Response('{"findings": [{"check_id": 7, "status": "pass"}]}'), monkeypatch
    )

    with pytest.raises(llm.SchemaViolation) as caught:
        _run(SCHEMA)

    assert "findings/0/check_id" in str(caught.value)


def test_enforce_schema_is_applied_on_the_anthropic_path_too(monkeypatch) -> None:
    """The guarantee is ours, not the provider's — so it cannot be provider-specific."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        llm,
        "_anthropic_complete",
        lambda **kwargs: ({"findings": [], "sneaky": True}, {}),
    )

    parsed, _ = _run(SCHEMA)

    assert parsed == {"findings": []}
