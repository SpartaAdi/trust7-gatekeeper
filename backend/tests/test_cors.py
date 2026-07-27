"""CORS configuration: exactly one origin, from the environment, never a wildcard.

These tests reload `config` and `main` under a controlled environment, because
both read the environment at import time.
"""

from __future__ import annotations

import importlib
import importlib.util
import pathlib

import pytest
from fastapi.testclient import TestClient

VERCEL = "https://trust7-gatekeeper.vercel.app"


def _app_with(monkeypatch: pytest.MonkeyPatch, origin: str | None):
    """Rebuild the app as if the process had just started with this environment."""
    if origin is None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGIN", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOWED_ORIGIN", origin)

    import config

    importlib.reload(config)
    import main

    importlib.reload(main)
    return config, main


@pytest.fixture(autouse=True)
def _restore_modules():
    """Leave config/main as the rest of the suite found them."""
    yield
    import config
    import main

    importlib.reload(config)
    importlib.reload(main)


def test_environment_variable_sets_the_allowed_origin(monkeypatch) -> None:
    config, _ = _app_with(monkeypatch, VERCEL)

    assert config.CORS_ALLOWED_ORIGIN == VERCEL
    assert config.CORS_ORIGIN_FROM_ENV is True


def _config_beside_dotenv(tmp_path, dotenv_body: str):
    """Import a copy of config.py from a temp dir holding a real .env file.

    `config` locates .env relative to its own `__file__`, and importlib.reload
    restores the true path — so monkeypatching `__file__` proves nothing. Loading
    a copy from disk is the only way to exercise the .env branch for real.
    """
    import config as real_config

    (tmp_path / "config.py").write_text(pathlib.Path(real_config.__file__).read_text())
    (tmp_path / ".env").write_text(dotenv_body)

    spec = importlib.util.spec_from_file_location(
        f"config_under_test_{tmp_path.name}", tmp_path / "config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STALE = "https://stale-from-dotenv.example"


def test_a_dotenv_file_is_actually_read_when_the_variable_is_unset(
    monkeypatch, tmp_path
) -> None:
    """The control for the test below: without this, that one passes trivially."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGIN", raising=False)

    module = _config_beside_dotenv(tmp_path, f"CORS_ALLOWED_ORIGIN={STALE}\n")

    assert module.CORS_ALLOWED_ORIGIN == STALE


def test_environment_variable_beats_a_dotenv_file(monkeypatch, tmp_path) -> None:
    """A real environment variable is how Render supplies this; it must win."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGIN", VERCEL)

    module = _config_beside_dotenv(tmp_path, f"CORS_ALLOWED_ORIGIN={STALE}\n")

    assert module.CORS_ALLOWED_ORIGIN == VERCEL, (
        "a .env file overrode the real environment variable — load_dotenv must "
        "not be called with override=True"
    )


def test_unset_falls_back_to_the_dev_default_and_flags_it(monkeypatch) -> None:
    config, _ = _app_with(monkeypatch, None)

    assert config.CORS_ALLOWED_ORIGIN == "http://localhost:5173"
    assert config.CORS_ORIGIN_FROM_ENV is False


def test_an_empty_variable_does_not_become_an_origin_of_empty_string(
    monkeypatch,
) -> None:
    """Render will store a blank value; `allow_origins=['']` blocks everything."""
    config, _ = _app_with(monkeypatch, "   ")

    assert config.CORS_ALLOWED_ORIGIN == "http://localhost:5173"
    assert config.CORS_ORIGIN_FROM_ENV is False


def test_a_trailing_slash_is_normalised_away(monkeypatch) -> None:
    """A browser Origin header never has a trailing slash, so neither may this."""
    config, _ = _app_with(monkeypatch, f"{VERCEL}/")

    assert config.CORS_ALLOWED_ORIGIN == VERCEL


def test_the_configured_origin_is_echoed_for_that_origin_only(monkeypatch) -> None:
    _, main = _app_with(monkeypatch, VERCEL)
    client = TestClient(main.app)

    allowed = client.get("/health", headers={"Origin": VERCEL})
    assert allowed.headers.get("access-control-allow-origin") == VERCEL

    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert other.headers.get("access-control-allow-origin") is None


def test_no_wildcard_is_ever_allowed(monkeypatch) -> None:
    _, main = _app_with(monkeypatch, VERCEL)
    client = TestClient(main.app)

    response = client.get("/health", headers={"Origin": "https://evil.example"})

    assert response.headers.get("access-control-allow-origin") != "*"


def test_preflight_from_the_configured_origin_is_accepted(monkeypatch) -> None:
    _, main = _app_with(monkeypatch, VERCEL)
    client = TestClient(main.app)

    response = client.options(
        "/reviews",
        headers={
            "Origin": VERCEL,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == VERCEL


def test_the_middleware_receives_exactly_one_origin(monkeypatch) -> None:
    """Guards against a list of origins or a wildcard creeping into main.py."""
    _, main = _app_with(monkeypatch, VERCEL)

    cors = [
        m
        for m in main.app.user_middleware
        if m.cls.__name__ == "CORSMiddleware"
    ]
    assert len(cors) == 1
    origins = cors[0].kwargs["allow_origins"]
    assert origins == [VERCEL]
    assert "*" not in origins
    assert cors[0].kwargs["allow_credentials"] is False
