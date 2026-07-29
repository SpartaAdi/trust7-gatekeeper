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
import pathlib
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
                 refusal: str | None = None, cached: int = 0,
                 provider: str | None = "CoreWeave", model: str = "moonshotai/kimi-k2.6",
                 response_id: str = "gen-test") -> None:
        self.choices = [_Choice(content, finish_reason, refusal)]
        self.usage = _Usage(cached)
        self.model = model
        self.id = response_id
        # OpenRouter reports the serving provider here. `None` models the field
        # being absent, which is how a non-OpenRouter-shaped response looks.
        if provider is not None:
            self.provider = provider


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


def test_the_configured_ceiling_is_not_below_what_any_stage_requests() -> None:
    """The trap this guards: llm.py clamps every request to
    OPENROUTER_MAX_COMPLETION_TOKENS, so a stage raised above the ceiling is
    silently reduced instead of erroring. Raising evaluate to 64000 while the
    ceiling sat at 32000 would have looked applied and changed nothing.
    """
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    # vision.py is included because it now asks for 64000 too — a guard that only
    # read stages.py would have let the vision stage cross the ceiling unnoticed.
    requested: list[int] = []
    for source in (backend / "agent" / "stages.py", backend / "ingestion" / "vision.py"):
        requested += [int(n) for n in re.findall(r"max_tokens=(\d+),", source.read_text())]

    assert requested, "no max_tokens call sites found — have the stages moved?"
    assert config.OPENROUTER_MAX_COMPLETION_TOKENS >= max(requested), (
        f"ceiling {config.OPENROUTER_MAX_COMPLETION_TOKENS} is below the largest "
        f"stage request {max(requested)}; that stage would be silently clamped"
    )


def test_no_stage_requests_more_than_the_routing_safe_ceiling() -> None:
    """The guard the ceiling used to provide implicitly.

    While OPENROUTER_MAX_COMPLETION_TOKENS was 64000 it could not pass a request
    big enough to narrow routing. At 128000 it can, so this asserts the thing that
    actually matters: no stage asks for more than 65,536, above which only 13 of
    the 22 providers serving kimi-k2.6 remain routable instead of 15.
    """
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    # vision.py is included because it now asks for 64000 too — a guard that only
    # read stages.py would have let the vision stage cross the ceiling unnoticed.
    requested: list[int] = []
    for source in (backend / "agent" / "stages.py", backend / "ingestion" / "vision.py"):
        requested += [int(n) for n in re.findall(r"max_tokens=(\d+),", source.read_text())]

    assert requested, "no max_tokens call sites found — have the stages moved?"
    over = [n for n in requested if n > config.OPENROUTER_ROUTING_SAFE_COMPLETION_TOKENS]
    assert not over, (
        f"stage(s) requesting {over} exceed the routing-safe "
        f"{config.OPENROUTER_ROUTING_SAFE_COMPLETION_TOKENS}, which drops Venice "
        f"and StreamLake from the routable set"
    )


def test_raising_the_ceiling_did_not_change_what_evaluate_actually_requests(
    monkeypatch,
) -> None:
    """Headroom must not become a bigger request by accident: the clamp takes the
    minimum, so a 128000 ceiling still sends evaluate's own 64000."""
    monkeypatch.setattr(config, "OPENROUTER_MAX_COMPLETION_TOKENS", 128_000)

    assert sent_request(monkeypatch, max_tokens=64_000)["max_tokens"] == 64_000


def test_evaluate_asks_for_more_output_than_the_other_stages() -> None:
    """Evaluate emits a finding per check with evidence, and truncated at 32000 in
    a real run. It should be the stage with the most headroom."""
    from agent import stages

    evaluate_max = _stage_max_tokens("evaluate")
    assert evaluate_max == 64_000, evaluate_max
    for name in ("classify", "prioritize", "remediate"):
        assert _stage_max_tokens(name) < evaluate_max, (
            f"{name} should not have been raised alongside evaluate"
        )


def _stage_max_tokens(stage: str) -> int:
    """The max_tokens literal inside one stage function, read from source.

    Read rather than executed because calling the stage would need a live client;
    the point is to pin the per-stage numbers against accidental drift.
    """
    import inspect
    import re

    from agent import stages

    source = inspect.getsource(getattr(stages, stage))
    found = re.search(r"max_tokens=(\d+)", source)
    assert found, f"no max_tokens in {stage}()"
    return int(found.group(1))


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


# --------------------------------------------------------------------------- #
# 8. Provider routing — an ordered allow-list, and proof it was honoured
# --------------------------------------------------------------------------- #

def _respond(monkeypatch, response: _Response, *, label: str = "call") -> None:
    """Run one call against a canned response."""
    class FakeCompletions:
        def create(self, **request: Any) -> _Response:
            return response

    fake = type(
        "FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()}
    )()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)
    llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=MINIMAL_SCHEMA,
        label=label,
    )


@pytest.fixture(autouse=True)
def _clean_route_log() -> None:
    """The route log is module state; a leaked entry would cross tests."""
    llm.reset_route_log()


def test_the_provider_order_is_sent_as_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        config, "OPENROUTER_PROVIDER_ORDER", ["coreweave", "decart", "inceptron"]
    )

    provider = sent_request(monkeypatch)["extra_body"]["provider"]

    # Order is meaningful — it is preference order, not a set.
    assert provider["order"] == ["coreweave", "decart", "inceptron"]


def test_fallbacks_are_off_by_default(monkeypatch) -> None:
    """The whole point: an unlisted provider must never quietly serve a call."""
    monkeypatch.setattr(config, "OPENROUTER_ALLOW_FALLBACKS", False)

    assert sent_request(monkeypatch)["extra_body"]["provider"]["allow_fallbacks"] is False


def test_fallbacks_can_be_turned_back_on_without_a_code_change(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENROUTER_ALLOW_FALLBACKS", True)

    assert sent_request(monkeypatch)["extra_body"]["provider"]["allow_fallbacks"] is True


def test_the_order_does_not_displace_require_parameters(monkeypatch) -> None:
    """Both matter: `order` picks the endpoint, `require_parameters` vets it."""
    provider = sent_request(monkeypatch)["extra_body"]["provider"]

    assert provider["require_parameters"] is True
    assert set(provider) >= {"order", "allow_fallbacks", "require_parameters"}


def test_every_configured_provider_slug_is_lowercase_and_hyphenated() -> None:
    """Slugs, not display names. `CoreWeave` is not a routing token."""
    for slug in config.OPENROUTER_PROVIDER_ORDER:
        assert slug == slug.lower(), f"{slug!r} is not a slug"
        assert " " not in slug, f"{slug!r} looks like a display name"


def test_the_served_provider_is_recorded(monkeypatch) -> None:
    _respond(monkeypatch, _Response('{"ok": true}', provider="Decart"), label="evaluate:aws_waf")

    served = llm.route_log()
    assert len(served) == 1
    assert served[0].provider == "Decart"
    assert served[0].label == "evaluate:aws_waf"
    assert served[0].allowed is True
    assert served[0].output_tokens == 340


def test_a_provider_outside_the_allow_list_hard_fails(monkeypatch, caplog) -> None:
    """The regression this exists for.

    A run was served by Phala — not in the order, `allow_fallbacks: false` sent — and
    completed looking exactly like a correctly pinned one, because this only logged.
    """
    monkeypatch.setattr(config, "OPENROUTER_PROVIDER_ORDER", ["coreweave", "decart"])
    monkeypatch.setattr(config, "OPENROUTER_ALLOW_FALLBACKS", False)

    with caplog.at_level("ERROR"), pytest.raises(llm.ProviderNotAllowed) as caught:
        _respond(monkeypatch, _Response('{"ok": true}', provider="Phala"))

    assert "Phala" in str(caught.value)
    assert "VIOLATION" in caplog.text
    # Recorded as well as raised: the tail is what the e2e script prints.
    assert llm.route_log()[0].allowed is False


def test_the_violation_message_carries_the_request_id(monkeypatch) -> None:
    """The request id is the only handle on OpenRouter's activity log. Diagnosing a
    bad route after the fact without it is guesswork — which is how the first one
    went unexplained."""
    monkeypatch.setattr(config, "OPENROUTER_PROVIDER_ORDER", ["coreweave"])

    with pytest.raises(llm.ProviderNotAllowed) as caught:
        _respond(monkeypatch, _Response('{"ok": true}', provider="Phala",
                                        response_id="gen-abc123"))

    assert "gen-abc123" in str(caught.value)


def test_an_unreported_provider_also_hard_fails(monkeypatch) -> None:
    """A response that does not say who served it cannot be shown to have honoured
    the lock. "No evidence of a violation" is not "evidence of compliance", and a
    real run came back with no provider recorded at all."""
    with pytest.raises(llm.ProviderNotAllowed) as caught:
        _respond(monkeypatch, _Response('{"ok": true}', provider=None))

    assert "reported no serving provider" in str(caught.value)
    assert llm.route_log()[0].provider == "unreported"


def test_enforcement_can_be_switched_off_without_a_code_change(monkeypatch) -> None:
    """Escape hatch for the case where a provider stops reporting the field and
    shipping matters more than proving the route."""
    monkeypatch.setattr(config, "OPENROUTER_ENFORCE_PROVIDER_LOCK", False)
    monkeypatch.setattr(config, "OPENROUTER_PROVIDER_ORDER", ["coreweave"])

    _respond(monkeypatch, _Response('{"ok": true}', provider="Phala"))

    assert llm.route_log()[0].allowed is False


def test_no_violation_is_raised_when_fallbacks_are_deliberately_allowed(
    monkeypatch,
) -> None:
    """With fallbacks on, another provider serving is the configured behaviour."""
    monkeypatch.setattr(config, "OPENROUTER_ALLOW_FALLBACKS", True)

    _respond(monkeypatch, _Response('{"ok": true}', provider="Phala"))

    assert llm.route_log()[0].allowed is True


def test_display_names_are_matched_against_slugs(monkeypatch) -> None:
    """The request takes `sail-research`; the response says `Sail Research`.

    Comparing them raw would report a violation on every single call.
    """
    monkeypatch.setattr(config, "OPENROUTER_PROVIDER_ORDER", ["sail-research"])

    _respond(monkeypatch, _Response('{"ok": true}', provider="Sail Research"))

    assert llm.route_log()[0].allowed is True


def test_a_truncated_call_is_still_attributed_before_it_raises(monkeypatch) -> None:
    """Knowing which endpoint truncated is the whole point of the record."""
    with pytest.raises(llm.TruncatedResponse):
        _respond(
            monkeypatch,
            _Response('{"ok": tr', finish_reason="length", provider="Inceptron"),
        )

    assert llm.route_log()[0].provider == "Inceptron"
    assert llm.route_log()[0].finish_reason == "length"


def test_a_refused_call_is_still_attributed_before_it_raises(monkeypatch) -> None:
    with pytest.raises(llm.ModelRefusal):
        _respond(monkeypatch, _Response("", refusal="no", provider="Decart"))

    assert llm.route_log()[0].provider == "Decart"


def test_the_route_log_is_bounded(monkeypatch) -> None:
    """It is a diagnostic tail on a long-running process, not an audit trail."""
    for _ in range(llm.ROUTE_LOG_LIMIT + 5):
        _respond(monkeypatch, _Response('{"ok": true}'))

    assert len(llm.route_log()) == llm.ROUTE_LOG_LIMIT


def test_every_pipeline_call_site_passes_a_label() -> None:
    """An unlabelled call cannot be attributed to a stage in the route log."""
    sources = [
        pathlib.Path("agent/stages.py").read_text(),
        pathlib.Path("ingestion/vision.py").read_text(),
    ]
    calls = sum(source.count("llm.complete_json(") for source in sources)
    labels = sum(source.count("label=") for source in sources)

    # stages.py has one unrelated `label=` (component label), hence >= not ==.
    assert calls == 5, f"expected 5 call sites, found {calls}"
    assert labels >= calls


# --------------------------------------------------------------------------- #
# 9. A stalled call must fail fast, not hang
#
# A real run hung for 5,657 seconds — 94 minutes — and returned malformed JSON with
# no provider recorded. There is no server-side deadline on a chat completion, so
# without a client-side one a hung upstream is indistinguishable from a slow one.
# --------------------------------------------------------------------------- #

@pytest.fixture
def fresh_client(monkeypatch):
    """`_openrouter_client` is lru_cached so the connection pool is reused. That
    cache outlives a test, so any test asserting on how the client was constructed
    has to drop it first — otherwise it inspects one an earlier test built."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    llm._openrouter_client.cache_clear()
    yield
    llm._openrouter_client.cache_clear()


def test_the_client_carries_a_wall_clock_deadline(monkeypatch, fresh_client) -> None:
    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT_SECONDS", 120.0)

    client = llm._openrouter_client()

    assert client.timeout == 120.0


def test_the_sdk_does_not_multiply_the_deadline_with_its_own_retries(
    monkeypatch, fresh_client
) -> None:
    """The openai SDK retries twice by default and `_openrouter_create_with_retry`
    retries once itself. Left at the default, a single stalled call could burn
    6 x timeout before surfacing — which turns a 120s ceiling back into an hour."""
    assert llm._openrouter_client().max_retries == 0


def test_the_deadline_is_configurable_without_a_code_change(
    monkeypatch, fresh_client
) -> None:
    """Evaluate is the stage most likely to legitimately approach 120s, at 64,000
    output tokens on high effort. Raising the bound must not need a deploy."""
    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT_SECONDS", 300.0)

    assert llm._openrouter_client().timeout == 300.0


def test_the_anthropic_fallback_path_has_the_same_deadline(monkeypatch) -> None:
    """A fallback provider that can hang for an hour is not a fallback."""
    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT_SECONDS", 120.0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")

    client = llm._client()

    assert client.timeout == 120.0
    assert client.max_retries == 0


def test_a_timeout_surfaces_rather_than_being_swallowed(monkeypatch) -> None:
    """It must reach the caller as a failure. The pipeline writes the stage error to
    the status file the UI polls, so a fast loud failure is visible; a swallowed one
    would leave the review sitting at "running" forever."""
    calls = {"n": 0}

    def stall(request: dict) -> Any:
        calls["n"] += 1
        raise openai.APITimeoutError(request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(llm, "_openrouter_create", stall)

    with pytest.raises(openai.APITimeoutError):
        llm.complete_json(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA,
            label="vision",
        )

    # Our own single retry applies — a timeout is transient — and no more.
    assert calls["n"] == 2, f"expected 1 attempt + 1 retry, got {calls['n']}"


def test_the_vision_stage_asks_for_the_capacity_we_verified(monkeypatch) -> None:
    """16000 had been this stage's value since the first commit and was never a
    measured number. A run hit it mid-JSON on a synthetic diagram, because
    OpenRouter draws reasoning tokens from the SAME max_tokens budget."""
    import re

    source = (
        pathlib.Path(__file__).resolve().parent.parent / "ingestion" / "vision.py"
    ).read_text()

    assert re.search(r"max_tokens=64000,", source), "vision stage is not at 64000"
