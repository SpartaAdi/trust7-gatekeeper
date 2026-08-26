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
        # Anything left over is forwarded rather than dropped. `temperature` is the
        # first optional parameter to arrive, and a helper that silently swallowed
        # it would make a test asserting it was sent pass while sending nothing.
        **kwargs,
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
# 3b. Sampling temperature — greedy on evaluate, absent everywhere else
#
# Evaluate's 45 statuses are the sole input to `scoring.score`, so sampling
# variance there moves the score and corrupts the re-review delta, which is
# supposed to mean the design changed. These tests pin three separate things,
# because each could break without the others noticing:
#
#   * the value evaluate asks for is the floor of the parameter's range;
#   * it reaches the wire as a top-level `temperature`, not buried in extra_body
#     where the provider would never read it;
#   * every other stage still sends NO temperature key at all — an unasked-for
#     default appearing here would change four request bodies that are meant to
#     stay byte-identical for the implicit prompt cache.
# --------------------------------------------------------------------------- #

def test_no_temperature_is_sent_unless_the_caller_asks(monkeypatch) -> None:
    """Absent, not defaulted: the four other stages send the body they always sent."""
    assert "temperature" not in sent_request(monkeypatch)


def test_a_requested_temperature_is_sent_as_a_top_level_parameter(monkeypatch) -> None:
    request = sent_request(monkeypatch, temperature=llm.GREEDY_TEMPERATURE)

    assert request["temperature"] == 0.0
    assert "temperature" not in request["extra_body"], (
        "temperature is an OpenAI-compatible top-level parameter; inside extra_body "
        "it would be forwarded as an unknown provider option"
    )


def test_greedy_is_the_floor_of_the_documented_range() -> None:
    """OpenRouter documents temperature as 0.0-2.0, so 0.0 is as close to
    deterministic as this parameter goes. Anything above it is a regression."""
    assert llm.GREEDY_TEMPERATURE == 0.0


def test_the_greedy_temperature_binds_to_the_installed_openai_signature(
    monkeypatch,
) -> None:
    """`temperature` must be a real parameter of the method we send it to."""
    client = openai.OpenAI(api_key="test-key-not-used", base_url=config.OPENROUTER_BASE_URL)

    inspect.signature(client.chat.completions.create).bind(
        **sent_request(monkeypatch, temperature=llm.GREEDY_TEMPERATURE)
    )


def test_only_the_two_score_bearing_stages_ask_for_a_temperature() -> None:
    """Read from source, across every model call site in the pipeline.

    THE ASSERTION WIDENED, on instruction. It read `== {"evaluate"}`, implementing an
    earlier round that was explicit classify should keep its sampling untouched. That
    was reversed deliberately: classify's output is rendered into the evaluate prompt
    by `_render_classification`, so classify IS part of evaluate's input, and greedy
    decoding on a varying input still varies.

    It was not hypothetical. Design B run 2 returned `pass` on all 18 AI-conditional
    checks where two otherwise-identical runs returned `not_applicable` — 89.3 against
    42.9 overall — and this call was the only thing that differed between them.

    `prioritize`, `remediate` and the vision path still sample at the provider
    default, and still must: they produce prose and an ordering, where wording
    varying between runs is not a correctness problem, and paying to suppress it buys
    nothing. This test exists to keep that line where it is, not to freeze a count.
    """
    import inspect as inspect_module

    from agent import stages
    from ingestion import vision

    asking = {
        name
        for module, name in (
            (stages, "classify"),
            (stages, "_classify_once"),
            (stages, "evaluate"),
            (stages, "prioritize"),
            (stages, "remediate"),
            (vision, "parse_image"),
        )
        if (function := getattr(module, name, None)) is not None
        and "temperature=" in inspect_module.getsource(function)
    }

    assert asking == {"evaluate", "_classify_once"}, (
        f"temperature is set at {sorted(asking)}; only evaluate and the classify call "
        f"may set it — prioritize, remediate and ingest/vision sample at the provider "
        f"default, because their output is prose and an ordering rather than "
        f"arithmetic input"
    )


def test_classify_asks_for_the_greedy_temperature() -> None:
    """The literal at the call site, so a future edit to a non-zero value fails.

    Classify feeds the evaluate prompt, so a non-zero here reintroduces variance into
    a stage that was made greedy precisely to remove it.
    """
    import inspect as inspect_module
    import re

    from agent import stages

    source = inspect_module.getsource(stages._classify_once)
    match = re.search(r"temperature=([\w.]+)", source)
    assert match, "classify no longer sets a temperature"
    assert match.group(1) == "llm.GREEDY_TEMPERATURE"
    assert llm.GREEDY_TEMPERATURE == 0.0


def test_evaluate_asks_for_the_greedy_temperature() -> None:
    """The literal at the call site, so a future edit to a non-zero value fails."""
    import inspect as inspect_module
    import re

    from agent import stages

    source = inspect_module.getsource(stages.evaluate)
    found = re.search(r"temperature=([\w.]+)", source)

    assert found, "evaluate no longer passes a temperature"
    assert found.group(1) == "llm.GREEDY_TEMPERATURE", found.group(1)


def test_a_lower_effort_retry_keeps_the_same_temperature(monkeypatch) -> None:
    """Effort is what a deadline retry trades away — sampling is not.

    Evaluate's retry carries the same label prefix, so a retry that sampled at the
    provider default would return a differently-sampled set of verdicts under a
    name that claims to be the same call.
    """
    sent: list[dict[str, Any]] = []

    class FakeCompletions:
        def create(self, **request: Any) -> _Response:
            sent.append(request)
            if len(sent) == 1:
                raise openai.APITimeoutError(request=httpx.Request("POST", "http://x"))
            return _Response('{"ok": true}')

    fake = type(
        "FakeClient", (), {"chat": type("Chat", (), {"completions": FakeCompletions()})()}
    )()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)

    llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=MINIMAL_SCHEMA,
        effort="high",
        max_tokens=16000,
        label="evaluate:aws_waf",
        temperature=llm.GREEDY_TEMPERATURE,
    )

    assert len(sent) == 2, "expected the transient retry to have fired"
    assert [request["temperature"] for request in sent] == [0.0, 0.0]


def test_the_pinned_providers_all_advertise_temperature() -> None:
    """`require_parameters: True` means an unsupported parameter drops an endpoint.

    Sending temperature narrows the routable set to endpoints advertising it. The
    three in OPENROUTER_PROVIDER_ORDER do — verified against
    /api/v1/models/moonshotai/kimi-k2.6/endpoints — and this test states that
    dependency so it is a visible assumption rather than a silent one. It reads the
    configured order, not the network: a slug added without checking the endpoint
    metadata should be a deliberate edit here too.
    """
    verified_to_support_temperature = {"coreweave", "decart", "inceptron"}

    unverified = set(config.OPENROUTER_PROVIDER_ORDER) - verified_to_support_temperature
    assert not unverified, (
        f"provider(s) {sorted(unverified)} are in the routing order but are not "
        f"among those verified to advertise `temperature`. With "
        f"require_parameters: True, an endpoint that does not support it cannot "
        f"serve the evaluate stage at all — check "
        f"/api/v1/models/{config.OPENROUTER_MODEL}/endpoints before adding it."
    )


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


def test_prioritize_asks_for_more_than_the_ceiling_that_killed_a_review() -> None:
    """16000 on prioritize failed a real review; it must not drift back.

    Run 3 of 3 on the AI-bearing design: prioritize blew the 120s deadline,
    `_openrouter_complete` retried one step down at `low` effort as designed, and
    that attempt hit 16000 before closing its JSON. `TruncatedResponse` at `low` has
    nowhere left to step down, so it propagated and the review failed — after
    evaluate had already been paid for twice.

    Asserted as a floor rather than an equality: a future raise to evaluate's 64000
    is a legitimate next step if it recurs, and this should not stand in the way of
    it. What must never happen is a return to a value already observed to fail.

    The measured shape of the stage is why the number was wrong. Its JSON is the
    SMALLEST in the pipeline — ~600 tokens for 10 open findings, ~2,300 for all 45 —
    so 16000 left roughly 13,700 for reasoning and reasoning still overran it.
    Prioritize is the only stage producing a total order, weighing every finding
    against every other; every other stage is per-item and independent. Highest
    reasoning demand, lowest ceiling.
    """
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    source = (backend / "agent" / "stages.py").read_text()

    # The call site is identified by its label, so this cannot pass by reading some
    # other stage's ceiling.
    block = source[: source.index('label="prioritize"')]
    asked = [int(n) for n in re.findall(r"max_tokens=(\d+),", block)]
    assert asked, "no max_tokens found before the prioritize label — has it moved?"
    prioritize_tokens = asked[-1]

    assert prioritize_tokens > 16000, (
        f"prioritize requests {prioritize_tokens}; 16000 truncated in a real run "
        f"and failed the whole review"
    )
    # And it must still clear both ceilings, or the raise is silently clamped away.
    assert prioritize_tokens <= config.OPENROUTER_MAX_COMPLETION_TOKENS
    assert prioritize_tokens <= config.OPENROUTER_ROUTING_SAFE_COMPLETION_TOKENS


def test_classify_asks_for_more_than_the_ceiling_that_truncated_a_real_review() -> None:
    """16000 on classify failed a real review once it could see an embedded diagram.

    On the real RMBL SoW, ingest found 29 components in the page-8 diagram plus
    24,215 characters of document text, and classify hit 16000 before closing its
    JSON. The pipeline stopped at t+235.3s having never reached evaluate.

    The number was not wrong for the reason it looks. Measured, classify's JSON is
    ~900 output tokens for 9 components, ~4,100 for 29 with rich attributes and
    ~6,600 for 60 — it fits at every size, so the OUTPUT never overran. Reasoning
    did, drawn from the same budget on OpenRouter, because Segment 3 gave this stage
    a SECOND description of one design to reconcile against the first. That is the
    same reasoning-heavy, output-light shape that forced prioritize's raise.

    Asserted as a floor rather than an equality: 64000 is a legitimate next step if
    it recurs. What must never happen is a return to a value already observed to
    fail on a real file.
    """
    import re

    backend = pathlib.Path(__file__).resolve().parent.parent
    source = (backend / "agent" / "stages.py").read_text()

    # Identified by the call site's own body, so this cannot pass by reading some
    # other stage's ceiling. `_classify_once` takes its label as a parameter, so
    # there is no literal label string to anchor on the way prioritize has.
    block = source[source.index("def _classify_once("):]
    block = block[: block.index("def design_has_content(")]
    asked = [int(n) for n in re.findall(r"max_tokens=(\d+),", block)]
    assert asked, "no max_tokens found inside _classify_once — has it moved?"
    classify_tokens = asked[-1]

    assert classify_tokens > 16000, (
        f"classify requests {classify_tokens}; 16000 truncated on the real RMBL SoW "
        f"and failed the whole review before evaluate ran"
    )
    # And it must still clear both ceilings, or the raise is silently clamped away.
    assert classify_tokens <= config.OPENROUTER_MAX_COMPLETION_TOKENS
    assert classify_tokens <= config.OPENROUTER_ROUTING_SAFE_COMPLETION_TOKENS


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

    # A stage whose request was extracted into a helper (classify, so its empty-
    # response retry can reuse it) holds no max_tokens of its own, so follow the
    # indirection rather than reporting the stage as unbounded.
    for name in (stage, f"_{stage}_once"):
        function = getattr(stages, name, None)
        if function is None:
            continue
        found = re.search(r"max_tokens=(\d+)", inspect.getsource(function))
        if found:
            return int(found.group(1))
    raise AssertionError(f"no max_tokens found for {stage}()")


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
    #
    # Six, not five: `remediate` gained a completion retry ("remediate-missing")
    # that fires only when the first answer covers fewer findings than were asked
    # for. It is a conditional sixth call, not a sixth stage — the pipeline is
    # still ingest -> classify -> evaluate -> prioritize -> remediate. Raising this
    # number is a decision about cost, so it stays an exact assertion.
    assert calls == 6, f"expected 6 call sites, found {calls}"
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


# --------------------------------------------------------------------------- #
# 10. A real wall-clock deadline, and a bounded retry when it fires
#
# The 120s transport timeout did not stop a 603s call. httpx's `timeout` bounds each
# socket operation — the gap between reads — so a response that trickles bytes
# forever resets it forever. These tests pin the difference.
# --------------------------------------------------------------------------- #

def test_a_trickling_response_is_aborted_by_the_wall_clock(monkeypatch) -> None:
    """The regression. Data keeps arriving, so no socket read ever times out, and
    the old transport-level timeout never fired."""
    import time as _time

    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT_SECONDS", 0.3)

    class TricklingCompletions:
        def create(self, **_request: Any) -> _Response:
            # Never idle: a byte every 10ms, well inside any read timeout.
            for _ in range(200):
                _time.sleep(0.01)
            return _Response('{"ok": true}')

    fake = type("FakeClient", (), {
        "chat": type("Chat", (), {"completions": TricklingCompletions()})()
    })()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)
    monkeypatch.setattr(llm, "_abort_transport", lambda: None)

    started = _time.monotonic()
    with pytest.raises(llm.CallDeadlineExceeded) as caught:
        llm._openrouter_create({"model": "m"})
    elapsed = _time.monotonic() - started

    assert elapsed < 1.5, f"deadline did not fire promptly ({elapsed:.1f}s)"
    assert "wall-clock deadline" in str(caught.value)


def test_the_deadline_aborts_the_transport_rather_than_abandoning_it(monkeypatch) -> None:
    """Cancelling the future is not enough: a thread blocked in a read is not
    interruptible, so it would keep receiving — and keep billing — until the
    provider finished on its own. Closing the socket is what actually stops it."""
    import time as _time

    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT_SECONDS", 0.2)
    aborted: list[bool] = []

    class SlowCompletions:
        def create(self, **_request: Any) -> _Response:
            _time.sleep(2)
            return _Response('{"ok": true}')

    fake = type("FakeClient", (), {
        "chat": type("Chat", (), {"completions": SlowCompletions()})()
    })()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)
    monkeypatch.setattr(llm, "_abort_transport", lambda: aborted.append(True))

    with pytest.raises(llm.CallDeadlineExceeded):
        llm._openrouter_create({"model": "m"})

    assert aborted == [True]


def test_a_deadline_abort_retries_once_at_lower_effort(monkeypatch) -> None:
    """Bounding runaway generation rather than eliminating it: reasoning tokens come
    out of the same budget, so turning the reasoning down is the lever."""
    efforts: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        efforts.append(kwargs["effort"])
        if len(efforts) == 1:
            raise llm.CallDeadlineExceeded("too slow")
        return {"ok": True}, {}

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=MINIMAL_SCHEMA,
        effort="high",
        label="evaluate:aws_waf",
    )

    assert efforts == ["high", "medium"]


def test_a_truncation_also_retries_once_at_lower_effort(monkeypatch) -> None:
    efforts: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        efforts.append(kwargs["effort"])
        if len(efforts) == 1:
            raise llm.TruncatedResponse("hit the ceiling")
        return {"ok": True}, {}

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=MINIMAL_SCHEMA,
        effort="medium",
    )

    assert efforts == ["medium", "low"]


def test_the_retry_happens_at_most_once(monkeypatch) -> None:
    """Worst case is deadline + one lower-effort call. Not unbounded."""
    efforts: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        efforts.append(kwargs["effort"])
        raise llm.CallDeadlineExceeded("still too slow")

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    with pytest.raises(llm.CallDeadlineExceeded):
        llm.complete_json(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA,
            effort="high",
        )

    assert efforts == ["high", "medium"], "retried more than once"


def test_a_failure_at_the_lowest_effort_is_not_retried(monkeypatch) -> None:
    """`low` has nowhere to step down to, so it is a real failure."""
    calls: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        calls.append(kwargs["effort"])
        raise llm.CallDeadlineExceeded("too slow")

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    with pytest.raises(llm.CallDeadlineExceeded):
        llm.complete_json(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA,
            effort="low",
        )

    assert calls == ["low"]


def test_the_retry_is_labelled_so_the_route_log_shows_it(monkeypatch) -> None:
    """Otherwise the log shows one call that mysteriously took twice as long."""
    labels: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        labels.append(kwargs["label"])
        if len(labels) == 1:
            raise llm.TruncatedResponse("hit the ceiling")
        return {"ok": True}, {}

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=MINIMAL_SCHEMA,
        effort="high",
        label="evaluate:aws_waf",
    )

    assert labels == ["evaluate:aws_waf", "evaluate:aws_waf:retry@medium"]


def test_a_deadline_abort_is_not_treated_as_a_transient_error(monkeypatch) -> None:
    """Retrying a runaway at the SAME effort would just spend the deadline twice on
    the way to the same place."""
    calls = {"n": 0}

    def create(_request: dict) -> Any:
        calls["n"] += 1
        raise llm.CallDeadlineExceeded("too slow")

    monkeypatch.setattr(llm, "_openrouter_create", create)

    with pytest.raises(llm.CallDeadlineExceeded):
        llm._openrouter_create_with_retry({"model": "m"})

    assert calls["n"] == 1, "the deadline error was caught by the transient retry"


def test_confidence_is_not_required_by_the_evaluate_schema() -> None:
    """One omission on finding 44 of 45 discarded a whole paid evaluate call."""
    from agent import stages

    item = stages._EVALUATE_SCHEMA["properties"]["findings"]["items"]

    assert "confidence" not in item["required"]
    assert "confidence" in item["properties"], "still asked for, just not demanded"


def test_a_finding_without_confidence_survives_enforcement() -> None:
    from agent import stages

    payload = {"findings": [{
        "check_id": "sec_a", "status": "fail", "severity": "high",
        "severity_rationale": "r", "title": "t", "evidence": "e",
        "affected_components": [],
    }]}

    enforced = llm.enforce_schema(payload, stages._EVALUATE_SCHEMA)

    assert enforced["findings"][0].get("confidence", "") == ""
    assert stages._confidence_of(enforced["findings"][0]) == ""


# --------------------------------------------------------------------------- #
# 11. finish_reason "error" — an endpoint fault, not a budget fault
#
# Run 3 died on classify with finish_reason "error" at 203 output tokens: not a
# ceiling, not a timeout, content cut mid-string. It was diagnosable only as "cut
# off", because none of the detail OpenRouter attaches was being read.
# --------------------------------------------------------------------------- #

class _ErrorChoice(_Choice):
    """A choice as OpenRouter shapes it when a provider faults mid-stream."""

    def __init__(self, content: str = '{"partial": "cut off mid-str',
                 native: str | None = None, error: dict | None = None) -> None:
        super().__init__(content, finish_reason="error")
        if native is not None:
            self.native_finish_reason = native
        if error is not None:
            self.error = error


class _ErrorResponse(_Response):
    def __init__(self, choice: _ErrorChoice, response_error: dict | None = None,
                 completion_tokens: int = 203) -> None:
        super().__init__("")
        self.choices = [choice]
        self.usage.completion_tokens = completion_tokens
        if response_error is not None:
            self.error = response_error


def _once(monkeypatch, response: Any) -> None:
    class Completions:
        def create(self, **_request: Any) -> Any:
            return response

    fake = type("FakeClient", (), {
        "chat": type("Chat", (), {"completions": Completions()})()
    })()
    monkeypatch.setattr(llm, "_openrouter_client", lambda: fake)


def test_a_stream_error_is_not_reported_as_a_truncation(monkeypatch) -> None:
    """Retrying at lower effort would be the wrong lever: 203 tokens is not a
    budget problem."""
    _once(monkeypatch, _ErrorResponse(_ErrorChoice()))

    with pytest.raises(llm.ProviderStreamError):
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )


def test_the_exception_reports_the_token_count_it_stopped_at(monkeypatch) -> None:
    _once(monkeypatch, _ErrorResponse(_ErrorChoice(), completion_tokens=203))

    with pytest.raises(llm.ProviderStreamError) as caught:
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )

    assert "203 output tokens" in str(caught.value)


def test_the_providers_own_finish_reason_is_surfaced(monkeypatch) -> None:
    """`native_finish_reason` is the raw one, next to the normalised "error"."""
    _once(monkeypatch, _ErrorResponse(_ErrorChoice(native="stream_error")))

    with pytest.raises(llm.ProviderStreamError) as caught:
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )

    assert "native_finish_reason='stream_error'" in str(caught.value)


def test_a_choice_level_error_object_is_surfaced_with_its_code(monkeypatch) -> None:
    _once(monkeypatch, _ErrorResponse(_ErrorChoice(
        error={"code": 502, "message": "upstream disconnected",
               "metadata": {"provider_name": "CoreWeave", "raw": "EOF"}},
    )))

    with pytest.raises(llm.ProviderStreamError) as caught:
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )

    message = str(caught.value)
    assert "code=502" in message
    assert "upstream disconnected" in message
    assert "CoreWeave" in message


def test_a_mid_stream_error_carries_the_typed_code_and_provider_code(monkeypatch) -> None:
    """How OpenRouter documents a mid-stream fault: a top-level `error` whose
    metadata holds `error_type` and the upstream `provider_code`."""
    _once(monkeypatch, _ErrorResponse(
        _ErrorChoice(),
        response_error={"code": 429, "message": "Rate limit exceeded",
                        "metadata": {"error_type": "rate_limit_exceeded",
                                     "provider_code": "429_TOO_MANY"}},
    ))

    with pytest.raises(llm.ProviderStreamError) as caught:
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )

    message = str(caught.value)
    assert "error_type='rate_limit_exceeded'" in message
    assert "provider_code='429_TOO_MANY'" in message


def test_no_detail_says_so_rather_than_looking_complete(monkeypatch) -> None:
    """The bare case, which is what run 3 actually looked like."""
    _once(monkeypatch, _ErrorResponse(_ErrorChoice()))

    with pytest.raises(llm.ProviderStreamError) as caught:
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )

    assert "no error detail was reported" in str(caught.value)


def test_a_stream_error_retries_once_at_the_SAME_effort(monkeypatch) -> None:
    """Not the effort ladder: this is an endpoint fault, so lowering the reasoning
    would be treating a symptom the fault does not have."""
    efforts: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        efforts.append(kwargs["effort"])
        if len(efforts) == 1:
            raise llm.ProviderStreamError("mid-stream fault")
        return {"ok": True}, {}

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    llm.complete_json(
        system=[{"type": "text", "text": "s"}],
        content=[{"type": "text", "text": "u"}],
        schema=MINIMAL_SCHEMA, effort="medium", label="classify",
    )

    assert efforts == ["medium", "medium"], "effort must not be stepped down"


def test_two_stream_errors_surface_as_a_real_failure(monkeypatch) -> None:
    """Retry once, then stop. No spinning on a persistently faulting endpoint."""
    calls: list[str] = []

    def attempt(**kwargs: Any) -> Any:
        calls.append(kwargs["label"])
        raise llm.ProviderStreamError("mid-stream fault")

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    with pytest.raises(llm.ProviderStreamError):
        llm.complete_json(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", label="classify",
        )

    assert calls == ["classify", "classify:retry-transient"]


def test_the_stream_retry_does_not_chain_into_the_effort_ladder(monkeypatch) -> None:
    """Two attempts total on either branch. A stream error followed by a truncation
    must not become a third call."""
    seen: list[tuple[str, str]] = []

    def attempt(**kwargs: Any) -> Any:
        seen.append((kwargs["label"], kwargs["effort"]))
        if len(seen) == 1:
            raise llm.ProviderStreamError("mid-stream fault")
        raise llm.TruncatedResponse("and then it truncated")

    monkeypatch.setattr(llm, "_openrouter_attempt", attempt)

    with pytest.raises(llm.TruncatedResponse):
        llm.complete_json(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="high", label="classify",
        )

    assert seen == [("classify", "high"), ("classify:retry-transient", "high")]


def test_a_stream_error_is_still_attributed_to_its_provider(monkeypatch) -> None:
    """Knowing WHICH endpoint faulted is the point, and the route log records it
    before the finish_reason checks run."""
    _once(monkeypatch, _ErrorResponse(_ErrorChoice()))

    with pytest.raises(llm.ProviderStreamError):
        llm._openrouter_attempt(
            system=[{"type": "text", "text": "s"}],
            content=[{"type": "text", "text": "u"}],
            schema=MINIMAL_SCHEMA, effort="medium", max_tokens=16000, label="classify",
        )

    served = llm.route_log()[-1]
    assert served.provider == "CoreWeave"
    assert served.finish_reason == "error"
