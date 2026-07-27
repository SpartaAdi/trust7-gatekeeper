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

# Claude API direct — not Bedrock. Pay-per-token, no provisioned throughput.
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

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


def anthropic_api_key() -> str:
    """The Anthropic API key, from the environment.

    Set as a dashboard environment variable in the hosting provider — never
    committed, never written to `local-data/`.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Set it in the Render dashboard "
            "(Environment tab), or in a local .env file for development."
        )
    return key
