"""The shared-token demo gate.

Not an auth system — one static token so the public Render URL cannot be used by
anyone who finds it to read past reviews or spend our tokens.

The tests worth having are the ones covering the ways a gate looks present and
is not: an unset token failing open, a preflight being blocked so the browser
never sends the real request, and a 401 arriving without CORS headers so the user
sees an opaque network error instead of the reason.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

TOKEN = "demo-token-for-tests"
VERCEL = "https://trust7-gatekeeper.vercel.app"

# Every route the gate must cover. /health is deliberately absent.
GATED_REQUESTS = [
    ("GET", "/reviews"),
    ("GET", "/reviews/11111111-2222-3333-4444-555555555555"),
    ("GET", "/reviews/11111111-2222-3333-4444-555555555555/status"),
    ("GET", "/reviews/11111111-2222-3333-4444-555555555555/report.pdf"),
    ("POST", "/reviews"),
    ("POST", "/reviews/11111111-2222-3333-4444-555555555555/reanalyze"),
    ("POST", "/uploads"),
]


def _app(monkeypatch, token: str | None, origin: str = VERCEL):
    """Rebuild config and main as if the process had started with this environment."""
    if token is None:
        monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("DEMO_ACCESS_TOKEN", token)
    monkeypatch.setenv("CORS_ALLOWED_ORIGIN", origin)

    import config

    importlib.reload(config)
    import main

    importlib.reload(main)
    return main


@pytest.fixture(autouse=True)
def _restore_modules():
    """Leave config/main as the rest of the suite found them."""
    yield
    import config
    import main

    importlib.reload(config)
    importlib.reload(main)


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    # A scratch data directory, so an accepted request reads an empty history
    # rather than whatever happens to be on this machine.
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    return TestClient(_app(monkeypatch, TOKEN).app)


# --------------------------------------------------------------------------- #
# Rejection — the core claim
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("method", "path"), GATED_REQUESTS)
def test_a_request_without_the_token_is_rejected(client, method, path) -> None:
    response = client.request(method, path)

    assert response.status_code == 401, f"{method} {path} was not gated"


@pytest.mark.parametrize(("method", "path"), GATED_REQUESTS)
def test_a_request_with_the_wrong_token_is_rejected(client, method, path) -> None:
    response = client.request(method, path, headers={"X-Demo-Token": "not-the-token"})

    assert response.status_code == 401


def test_the_rejection_explains_itself(client) -> None:
    """The message is rendered verbatim by the frontend, so it has to be readable."""
    detail = client.get("/reviews").json()["detail"]

    assert "X-Demo-Token" in detail
    assert "demo access token" in detail.lower()


def test_no_data_leaks_in_a_rejected_response(client) -> None:
    """A 401 must carry the reason and nothing else."""
    body = client.get("/reviews").json()

    assert set(body) == {"detail"}


def test_an_empty_token_header_is_not_treated_as_valid(client) -> None:
    assert client.get("/reviews", headers={"X-Demo-Token": ""}).status_code == 401


def test_a_token_that_is_a_prefix_of_the_real_one_is_rejected(client) -> None:
    assert client.get(
        "/reviews", headers={"X-Demo-Token": TOKEN[:-1]}
    ).status_code == 401


def test_the_header_name_is_case_insensitive(client) -> None:
    """HTTP header names are case-insensitive; a client lowercasing must still work."""
    assert client.get("/reviews", headers={"x-demo-token": TOKEN}).status_code == 200


# --------------------------------------------------------------------------- #
# Acceptance
# --------------------------------------------------------------------------- #

def test_the_correct_token_is_accepted(client) -> None:
    response = client.get("/reviews", headers={"X-Demo-Token": TOKEN})

    assert response.status_code == 200
    assert response.json() == []


def test_health_is_reachable_without_a_token(client) -> None:
    """Render polls this to decide the service is alive and sends no headers."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --------------------------------------------------------------------------- #
# Fails closed
# --------------------------------------------------------------------------- #

def test_an_unset_token_closes_the_api_rather_than_opening_it(monkeypatch) -> None:
    """The failure that would make the gate pointless: forgetting the variable and
    silently having no gate at all."""
    client = TestClient(_app(monkeypatch, None).app)

    response = client.get("/reviews")

    assert response.status_code == 401
    assert "DEMO_ACCESS_TOKEN is not set" in response.json()["detail"]


def test_an_unset_token_still_leaves_health_up(monkeypatch) -> None:
    """Otherwise Render marks the service unhealthy and restarts it forever."""
    client = TestClient(_app(monkeypatch, None).app)

    assert client.get("/health").status_code == 200


def test_a_blank_configured_token_does_not_accept_a_blank_header(monkeypatch) -> None:
    client = TestClient(_app(monkeypatch, "   ").app)

    assert client.get("/reviews", headers={"X-Demo-Token": ""}).status_code == 401
    assert client.get("/reviews", headers={"X-Demo-Token": "   "}).status_code == 401


# --------------------------------------------------------------------------- #
# Interaction with CORS — where a gate quietly breaks the whole app
# --------------------------------------------------------------------------- #

def test_a_preflight_is_not_gated(client) -> None:
    """Browsers attach no custom headers to OPTIONS, so gating preflight would
    block every cross-origin request before the real one was ever sent."""
    response = client.options(
        "/reviews",
        headers={
            "Origin": VERCEL,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-demo-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == VERCEL


def test_options_is_skipped_by_the_gate_itself(monkeypatch) -> None:
    """Exercises the gate's OPTIONS branch directly.

    Through the app this is unobservable: CORSMiddleware is registered last, so it
    is outermost and answers preflight before the gate runs. Calling the
    middleware function itself is the only way to prove the branch is real rather
    than decorative.
    """
    import asyncio

    main = _app(monkeypatch, TOKEN)
    reached = []

    async def call_next(_request):
        reached.append(True)
        return "passed-through"

    request = Request(
        {
            "type": "http",
            "method": "OPTIONS",
            "path": "/reviews",
            "headers": [],  # no token, deliberately
        }
    )

    result = asyncio.run(main.require_demo_token(request, call_next))

    assert result == "passed-through"
    assert reached == [True]


def test_a_401_still_carries_the_cors_header(client) -> None:
    """Middleware order: CORS must wrap the gate. Without this the browser reports
    an opaque CORS failure and the user never sees why they were refused."""
    response = client.get("/reviews", headers={"Origin": VERCEL})

    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == VERCEL


def test_the_token_header_is_allowed_by_cors(client) -> None:
    response = client.options(
        "/reviews",
        headers={
            "Origin": VERCEL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-demo-token",
        },
    )

    allowed = response.headers.get("access-control-allow-headers", "")
    assert "x-demo-token" in allowed.lower() or allowed == "*"
