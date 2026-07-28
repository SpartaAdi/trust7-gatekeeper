"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

# Load `backend/.env` then the repo-root `.env`, before anything below reads the
# environment. Neither overrides a variable already set, so a real environment
# variable (how Render supplies the key) always wins over a stray local file.
_HERE = pathlib.Path(__file__).resolve().parent
for _env_file in (_HERE / ".env", _HERE.parent / ".env"):
    if _env_file.is_file():
        load_dotenv(_env_file)

# --------------------------------------------------------------------------- #
# LLM provider
#
# One switch, so a same-day revert is an environment change and a restart rather
# than a code change. The Anthropic path stays fully intact and tested; it is
# simply not the default any more.
# --------------------------------------------------------------------------- #

PROVIDERS = ("openrouter", "anthropic")

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openrouter").strip().lower() or "openrouter"
if LLM_PROVIDER not in PROVIDERS:
    raise RuntimeError(
        f"LLM_PROVIDER={LLM_PROVIDER!r} is not recognised. "
        f"Set one of: {', '.join(PROVIDERS)}."
    )

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# Verified against https://openrouter.ai/api/v1/models: kimi-k2.6 is the current
# general Moonshot model that has BOTH image input and structured outputs, at
# 262k context. kimi-k2 — the obvious-looking slug — is text-only with no
# structured output support, so it must not be used here.
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "moonshotai/kimi-k2.6")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)

# The lowest max_completion_tokens any endpoint currently serving kimi-k2.6
# advertises (DeepInfra). Documented here because the evaluate stage asks for
# more than this — see the note in llm.py.
OPENROUTER_LOWEST_ENDPOINT_COMPLETION_CAP = 16_384

# Hard ceiling on what any stage may request from OpenRouter. Defaults to the
# largest a stage actually asks for; lower it to force every request under the
# floor above if routing turns out to reach a low-capability endpoint.
OPENROUTER_MAX_COMPLETION_TOKENS = int(
    os.environ.get("OPENROUTER_MAX_COMPLETION_TOKENS", "32000")
)

# Operational escape hatch: comma-separated OpenRouter provider names to exclude
# from routing, e.g. "DeepInfra,Sail Research". Empty by default.
OPENROUTER_IGNORE_PROVIDERS = [
    name.strip()
    for name in os.environ.get("OPENROUTER_IGNORE_PROVIDERS", "").split(",")
    if name.strip()
]

# The model string in force for this process, whichever provider is selected.
MODEL = OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else ANTHROPIC_MODEL

# Everything persistent lives here: uploads, reviews, and progress records.
DATA_DIR = pathlib.Path(os.environ.get("LOCAL_DATA_DIR", "./local-data")).resolve()

# Exact origin of the deployed frontend. No wildcard — the browser sends the
# origin, and a wildcard would let any site call this API on a user's behalf.
#
# Precedence, highest first:
#   1. the CORS_ALLOWED_ORIGIN environment variable (how Render supplies it)
#   2. CORS_ALLOWED_ORIGIN in backend/.env or the repo-root .env
#   3. the development default below
# `load_dotenv` above is called without `override=True`, so a real environment
# variable always beats a .env file. Nothing is hardcoded at the middleware.
#
# The value is stripped and falsiness-checked rather than read straight out of
# the environment: Render's dashboard will happily store an empty string, and
# `allow_origins=[""]` blocks every request while looking configured.
_CORS_ORIGIN_ENV = os.environ.get("CORS_ALLOWED_ORIGIN", "").strip().rstrip("/")
CORS_ALLOWED_ORIGIN = _CORS_ORIGIN_ENV or "http://localhost:5173"

# Whether the origin came from the environment or fell back to the dev default.
# Logged at startup so a missing dashboard variable is visible in Render's logs
# rather than surfacing only as an opaque CORS error in someone's browser.
CORS_ORIGIN_FROM_ENV = bool(_CORS_ORIGIN_ENV)

# Uploads are read fully into memory before being written, so cap them.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))


def _required_key(name: str) -> str:
    """Read a credential from the environment, or explain how to supply it.

    Set as a dashboard environment variable in the hosting provider — never
    committed, never written to `local-data/`.
    """
    key = os.environ.get(name, "").strip()
    if not key:
        raise RuntimeError(
            f"{name} is not set. Set it in the Render dashboard (Environment "
            f"tab), or in a local .env file for development."
        )
    return key


def anthropic_api_key() -> str:
    return _required_key("ANTHROPIC_API_KEY")


def openrouter_api_key() -> str:
    return _required_key("OPENROUTER_API_KEY")


def llm_api_key() -> str:
    """The credential for whichever provider is selected."""
    return (
        openrouter_api_key() if LLM_PROVIDER == "openrouter" else anthropic_api_key()
    )
