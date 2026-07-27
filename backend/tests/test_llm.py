"""Guards on the one module that calls the Anthropic SDK.

The `fallbacks` incident: a parameter that does not exist in anthropic 0.75.0
was passed to every model call, so the whole pipeline raised `TypeError` on
first contact with the API. Nothing caught it, because every test stubbed the
SDK out. `test_request_binds_to_the_installed_sdk_signature` closes that gap by
checking the request we build against the real installed signature.
"""

from __future__ import annotations

import inspect
from typing import Any

import anthropic
import httpx
import pytest

import llm


class _FakeBlock:
    type = "text"
    text = "{}"


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _FakeMessage:
    stop_reason = "end_turn"
    content = [_FakeBlock()]
    usage = _FakeUsage()


def _sent_kwargs(monkeypatch) -> dict[str, Any]:
    """The exact kwargs that reach the SDK, captured from a full complete_json run.

    Capturing at the client boundary rather than inspecting the request dict is
    the point: the `fallbacks` bug lived in `_create`, which adds kwargs of its
    own, so a test that only checked the request dict would have missed it.
    """
    sent: dict[str, Any] = {}

    class FakeMessages:
        def create(self, **kwargs: Any) -> _FakeMessage:
            sent.update(kwargs)
            return _FakeMessage()

    fake_client = type(
        "FakeClient", (), {"beta": type("Beta", (), {"messages": FakeMessages()})()}
    )()
    monkeypatch.setattr(llm, "_client", lambda: fake_client)

    llm.complete_json(
        system=[{"type": "text", "text": "system"}],
        content=[{"type": "text", "text": "user"}],
        schema={"type": "object", "properties": {}, "required": []},
        effort="high",
        max_tokens=16000,
    )
    return sent


def test_request_binds_to_the_installed_sdk_signature(monkeypatch) -> None:
    """Every kwarg we send must exist on the SDK method we send it to.

    This is the check that catches `fallbacks`: binding raises TypeError for an
    unexpected keyword, exactly as the real call did.
    """
    client = anthropic.Anthropic(api_key="test-key-not-used")
    signature = inspect.signature(client.beta.messages.create)

    signature.bind(**_sent_kwargs(monkeypatch))


def test_no_fallbacks_parameter_is_sent(monkeypatch) -> None:
    assert "fallbacks" not in _sent_kwargs(monkeypatch)


def test_structured_output_requests_carry_the_beta_flag(monkeypatch) -> None:
    """`output_config` is beta-gated in this SDK version."""
    assert _sent_kwargs(monkeypatch)["betas"] == ["structured-outputs-2025-11-13"]


# --------------------------------------------------------------------------- #
# Retry behaviour
# --------------------------------------------------------------------------- #


def _bad_request() -> anthropic.BadRequestError:
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    return anthropic.BadRequestError("bad", response=response, body=None)


def _overloaded() -> anthropic.InternalServerError:
    response = httpx.Response(
        529, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    return anthropic.InternalServerError("overloaded", response=response, body=None)


FULL_REQUEST: dict[str, Any] = {
    "model": "claude-opus-5",
    "max_tokens": 16000,
    "messages": [],
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": {}}},
}


def test_bad_request_retries_once_without_tuning_parameters(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create(request: dict[str, Any]) -> str:
        calls.append(request)
        if len(calls) == 1:
            raise _bad_request()
        return "second-attempt-result"

    monkeypatch.setattr(llm, "_create", fake_create)
    result = llm._create_with_retry(dict(FULL_REQUEST))

    assert result == "second-attempt-result"
    assert len(calls) == 2
    # The retry drops tuning, keeps the schema — without it the caller's
    # json.loads would fail on prose.
    assert "thinking" not in calls[1]
    assert "effort" not in calls[1]["output_config"]
    assert calls[1]["output_config"]["format"] == {"type": "json_schema", "schema": {}}


def test_bad_request_propagates_when_there_is_nothing_left_to_strip(monkeypatch) -> None:
    """No silent second failure: a minimal request that still 400s raises."""
    calls: list[dict[str, Any]] = []

    def fake_create(request: dict[str, Any]) -> Any:
        calls.append(request)
        raise _bad_request()

    monkeypatch.setattr(llm, "_create", fake_create)
    with pytest.raises(anthropic.BadRequestError):
        llm._create_with_retry({"model": "m", "max_tokens": 1, "messages": []})

    assert len(calls) == 1


def test_server_error_retries_once_with_the_same_request(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create(request: dict[str, Any]) -> str:
        calls.append(request)
        if len(calls) == 1:
            raise _overloaded()
        return "recovered"

    monkeypatch.setattr(llm, "_create", fake_create)
    assert llm._create_with_retry(dict(FULL_REQUEST)) == "recovered"
    assert len(calls) == 2
    assert calls[1] == FULL_REQUEST


def test_a_persistent_server_error_is_not_retried_forever(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_create(request: dict[str, Any]) -> Any:
        calls.append(request)
        raise _overloaded()

    monkeypatch.setattr(llm, "_create", fake_create)
    with pytest.raises(anthropic.InternalServerError):
        llm._create_with_retry(dict(FULL_REQUEST))

    assert len(calls) == 2, "one retry, then give up"


def test_authentication_errors_are_not_retried(monkeypatch) -> None:
    """A bad key fails identically on a retry — repeating it only adds latency."""
    calls: list[dict[str, Any]] = []
    response = httpx.Response(
        401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )

    def fake_create(request: dict[str, Any]) -> Any:
        calls.append(request)
        raise anthropic.AuthenticationError("bad key", response=response, body=None)

    monkeypatch.setattr(llm, "_create", fake_create)
    with pytest.raises(anthropic.AuthenticationError):
        llm._create_with_retry(dict(FULL_REQUEST))

    assert len(calls) == 1, "no retry on an unauthenticated request"
