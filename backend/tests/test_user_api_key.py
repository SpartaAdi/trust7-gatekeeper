"""A reviewer spending their own OpenRouter key instead of the server's.

The feature is three lines of routing; the risk is entirely in where the
credential can come to rest. So most of what is asserted here is absence — the
key must not reach disk, a response body, a log line, or the next review on the
same thread — and each of those is checked by scanning for the secret rather
than by trusting the code path that was supposed to avoid it.

The fallback matters as much as the override: with no key supplied, behaviour
must be byte-for-byte what it was before this existed.
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

import config
import llm

# Recognisable, and long enough that a substring scan cannot match it by luck.
USER_KEY = "sk-or-v1-user-supplied-key-a1b2c3d4e5f6a7b8c9d0"
SERVER_KEY = "sk-or-v1-server-key-0000000000000000000000"


@pytest.fixture(autouse=True)
def _clean_client_state(monkeypatch):
    """Drop the cached client and transport around every test.

    `_openrouter_client` is lru_cached and `_transport` is a module global, so a
    test that inspects either would otherwise see one an earlier test built.
    """
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    llm._openrouter_client.cache_clear()
    monkeypatch.setattr(llm, "_transport", None)
    yield
    llm._openrouter_client.cache_clear()


# --------------------------------------------------------------------------- #
# Which credential a call goes out on
# --------------------------------------------------------------------------- #


def test_no_user_key_uses_the_servers_own_client(monkeypatch) -> None:
    """The unchanged default. Same object, so the pool is still reused."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    assert llm._client_for_call() is llm._openrouter_client()


def test_a_user_key_is_the_one_actually_sent(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    with llm.api_key_override(USER_KEY):
        client = llm._client_for_call()

    assert client.api_key == USER_KEY
    # The header is what the provider actually reads; asserting only `api_key`
    # would pass even if the credential never reached the request.
    assert client.auth_headers["Authorization"] == f"Bearer {USER_KEY}"


def test_a_user_key_does_not_contaminate_the_shared_client(monkeypatch) -> None:
    """The cached client is process-wide. If an override mutated it rather than
    building its own, the next reviewer would silently spend this reviewer's key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    with llm.api_key_override(USER_KEY):
        llm._client_for_call()

    assert llm._openrouter_client().api_key == SERVER_KEY
    assert llm._client_for_call().api_key == SERVER_KEY


def test_an_empty_or_blank_override_falls_back_rather_than_sending_nothing(
    monkeypatch,
) -> None:
    """"No key" must mean the server's key, not an empty Authorization header."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    with llm.api_key_override(""):
        assert llm._client_for_call().api_key == SERVER_KEY


def test_a_user_key_works_when_the_server_has_no_key_at_all(monkeypatch) -> None:
    """The case the feature exists for. `config.openrouter_api_key()` raises when
    the server has none, so routing the override through the cached client would
    make a reviewer's own key unusable on exactly the deployment that needs it."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        llm._openrouter_client()

    with llm.api_key_override(USER_KEY):
        assert llm._client_for_call().api_key == USER_KEY


def test_a_user_key_call_still_shares_the_abortable_transport(monkeypatch) -> None:
    """The cancel button and the wall-clock deadline both work by closing one
    httpx transport. A per-key client with its own pool would be immune to both,
    so a user-key review could not be stopped and would bill to the end."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    server_client = llm._openrouter_client()
    with llm.api_key_override(USER_KEY):
        user_client = llm._client_for_call()

    assert user_client._client is server_client._client
    assert user_client._client is llm._transport


def test_the_deadline_and_retry_settings_survive_an_override(monkeypatch) -> None:
    """A client built by a different path is a client that can quietly miss the
    settings that stop one stalled call burning six times the deadline."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)
    monkeypatch.setattr(config, "OPENROUTER_TIMEOUT_SECONDS", 137.0)

    with llm.api_key_override(USER_KEY):
        client = llm._client_for_call()

    assert client.max_retries == 0
    assert client.timeout == 137.0
    assert str(client.base_url).startswith(config.OPENROUTER_BASE_URL)


# --------------------------------------------------------------------------- #
# Lifetime — where the key lives, and when it stops living there
# --------------------------------------------------------------------------- #


def test_the_key_is_cleared_when_the_block_exits(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    with llm.api_key_override(USER_KEY):
        pass

    assert llm._API_KEY.get() == ""
    assert llm._client_for_call().api_key == SERVER_KEY


def test_the_key_is_cleared_even_when_the_review_fails(monkeypatch) -> None:
    """Reset in a `finally`, or a crashed review leaves its key readable by
    whatever runs on that thread next."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)

    with pytest.raises(ZeroDivisionError):
        with llm.api_key_override(USER_KEY):
            1 / 0

    assert llm._API_KEY.get() == ""


def test_one_reviewers_key_is_invisible_to_a_concurrent_review(monkeypatch) -> None:
    """Reviews run on threadpool threads. A module global would let two of them
    spend each other's credit; a ContextVar does not."""
    monkeypatch.setenv("OPENROUTER_API_KEY", SERVER_KEY)
    seen: list[str] = []

    def other_review() -> None:
        seen.append(llm._client_for_call().api_key)

    with llm.api_key_override(USER_KEY):
        thread = threading.Thread(target=other_review)
        thread.start()
        thread.join()

    assert seen == [SERVER_KEY]


# --------------------------------------------------------------------------- #
# Leak surfaces — asserted by scanning for the secret, not by reading the code
# --------------------------------------------------------------------------- #


def _stub_complete_json(seen_keys: list[str], watch: dict[str, Any] | None = None):
    """Record which credential each stage would have called on.

    `watch` turns each model call into a disk inspection point. Scanning only
    after the run finishes is not enough: a key written to the status file and
    then overwritten by the next stage has still been on disk, and an
    end-of-run scan reports that as clean. A mutant doing exactly that survived
    until this ran mid-flight.
    """

    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        seen_keys.append(llm._API_KEY.get())
        if watch is not None:
            data_dir = watch["data_dir"]
            watch.setdefault("offenders", []).extend(
                str(path.relative_to(data_dir))
                for path in data_dir.rglob("*")
                if path.is_file() and USER_KEY.encode() in path.read_bytes()
            )
        required = set(schema.get("required", []))
        usage = {"input_tokens": 900, "output_tokens": 200}

        # `complete_json` returns (payload, usage) and the pipeline unpacks both.
        # Returning a bare dict here raises ValueError inside the background task,
        # where `_run_pipeline` logs it and moves on — so the review dies at
        # classify and every assertion below still passes, against nothing. That
        # is exactly what this file did until `_the_review_completed` was added.
        if "design_summary" in required:
            return {
                "design_summary": "A small API on AWS.",
                "components": [
                    {
                        "id": "api",
                        "label": "API Gateway",
                        "kind": "gateway",
                        "provider": "aws",
                        "service": "api gateway",
                        "attributes": [],
                    }
                ],
                "data_flows": [],
                "assumptions": [],
            }, usage
        if "findings" in required:
            return {
                "findings": [
                    {
                        "check_id": check_id,
                        "status": "fail",
                        "severity": "high",
                        "evidence": "Not stated in the design.",
                        "rationale": "No encryption is described.",
                        "confidence": "high",
                    }
                    for check_id in _first_check_ids()
                ]
            }, usage
        if "ranking" in required:
            return {"summary": "Gaps to close.", "ranking": []}, usage
        if "remediations" in required:
            return {"executive_summary": "Below band.", "remediations": []}, usage
        raise AssertionError(f"unexpected schema: {sorted(required)}")

    return fake


def _first_check_ids() -> list[str]:
    import rubric

    return [check.check_id for check in rubric.all_checks()[:2]]


@pytest.fixture
def app_with_stubbed_model(monkeypatch, tmp_path):
    """The real app on a temp data dir, with only the model stubbed."""
    import main
    import storage

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "demo-token")
    importlib.reload(storage)
    importlib.reload(main)

    seen_keys: list[str] = []
    watch: dict[str, Any] = {"data_dir": tmp_path}
    monkeypatch.setattr(llm, "complete_json", _stub_complete_json(seen_keys, watch))

    client = TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: "demo-token"})
    yield client, seen_keys, tmp_path, watch

    importlib.reload(storage)
    importlib.reload(main)


def _submit(client: TestClient, headers: dict[str, str] | None = None) -> Any:
    upload = client.post("/uploads", files={"file": ("sow.md", b"# Design\n", "text/markdown")})
    accepted = client.post(
        "/reviews",
        json={"document_key": upload.json()["key"], "title": "Test design"},
        headers=headers or {},
    )
    _assert_the_review_completed(client, accepted)
    return accepted


def _assert_the_review_completed(client: TestClient, accepted: Any) -> None:
    """Every leak assertion below is only as strong as the run it inspects.

    `_run_pipeline` catches and logs whatever the pipeline raises, so a stub that
    returns the wrong shape produces a review that fails at stage two, writes
    almost nothing, and leaves every "the key is not in X" assertion trivially
    true. This turns that into a failure instead of a false pass.
    """
    assert accepted.status_code == 202, accepted.text
    review_id = accepted.json()["review_id"]
    status = client.get(f"/reviews/{review_id}/status").json()
    assert status["state"] == "complete", (
        f"the pipeline did not finish (state={status['state']!r}, "
        f"error={status.get('error')!r}); the leak assertions would prove nothing"
    )


def test_the_header_reaches_the_pipeline(app_with_stubbed_model) -> None:
    client, seen_keys, _, _watch = app_with_stubbed_model

    _submit(client, {"X-OpenRouter-Key": USER_KEY})

    assert seen_keys, "the pipeline never called the model"
    assert set(seen_keys) == {USER_KEY}


def test_no_header_leaves_every_stage_on_the_server_key(app_with_stubbed_model) -> None:
    client, seen_keys, _, _watch = app_with_stubbed_model

    _submit(client)

    assert seen_keys, "the pipeline never called the model"
    assert set(seen_keys) == {""}


def test_the_key_is_never_written_to_disk(app_with_stubbed_model) -> None:
    """Scans every byte under the data directory: status records, review records,
    and uploads alike. An assertion on named fields would miss a key that arrived
    somewhere nobody thought to look."""
    client, _, data_dir, watch = app_with_stubbed_model

    _submit(client, {"X-OpenRouter-Key": USER_KEY})

    written = [path for path in data_dir.rglob("*") if path.is_file()]
    assert written, "the review wrote nothing, so this proves nothing"
    offenders = [
        str(path.relative_to(data_dir))
        for path in written
        if USER_KEY.encode() in path.read_bytes()
    ]
    assert not offenders, f"user key written to: {offenders}"

    # And it was not on disk at any point *during* the run either. Without this,
    # a key written to the status file and overwritten by the next stage passes
    # the scan above.
    assert watch["offenders"] == [], f"user key transiently on disk in: {watch['offenders']}"


def test_the_key_is_never_echoed_in_a_response(app_with_stubbed_model) -> None:
    client, _, _, _watch = app_with_stubbed_model

    accepted = _submit(client, {"X-OpenRouter-Key": USER_KEY})
    review_id = accepted.json()["review_id"]

    bodies = [
        accepted.text,
        client.get(f"/reviews/{review_id}").text,
        client.get(f"/reviews/{review_id}/status").text,
        client.get("/reviews").text,
    ]
    assert not [body for body in bodies if USER_KEY in body]


def test_the_key_is_never_echoed_by_a_validation_error(app_with_stubbed_model) -> None:
    """The reason the key is a header and not a body field: FastAPI's 422 handler
    echoes the submitted body back to the client. A key carried in the body would
    be returned verbatim the first time any neighbouring field was malformed."""
    client, _, _, _watch = app_with_stubbed_model

    response = client.post(
        "/reviews",
        json={"document_key": ["not", "a", "string"]},
        headers={"X-OpenRouter-Key": USER_KEY},
    )

    assert response.status_code == 422
    assert USER_KEY not in response.text


def test_the_key_is_never_logged(app_with_stubbed_model, caplog) -> None:
    client, _, _, _watch = app_with_stubbed_model

    with caplog.at_level(logging.DEBUG):
        _submit(client, {"X-OpenRouter-Key": USER_KEY})

    assert caplog.records, "nothing was logged, so this proves nothing"
    assert USER_KEY not in caplog.text
