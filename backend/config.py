"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
import pathlib

# Claude API direct — not Bedrock. Pay-per-token, no provisioned throughput.
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

# Everything persistent lives here: uploads, reviews, and progress records.
DATA_DIR = pathlib.Path(os.environ.get("LOCAL_DATA_DIR", "./local-data")).resolve()

# Exact origin of the deployed frontend. No wildcard — the browser sends the
# origin, and a wildcard would let any site call this API on a user's behalf.
CORS_ALLOWED_ORIGIN = os.environ.get("CORS_ALLOWED_ORIGIN", "http://localhost:5173")

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
