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

# Hard ceiling on what any stage may request from OpenRouter, applied as a clamp
# in llm.py. It must stay >= the largest per-stage request or that stage is
# silently reduced: raising evaluate to 64000 while this sat at 32000 would have
# looked applied and changed nothing.
#
# 128000 is headroom, not a target. It changes no request today — evaluate asks
# 64000 and min(64000, 128000) is 64000 — it just means a future stage needing
# more is not silently clipped by this constant.
#
# The trade-off it introduces: at 64000 this constant doubled as a routing
# guard, because it could not pass a request large enough to exclude providers.
# It no longer can, so ROUTING_SAFE_COMPLETION_TOKENS below carries that job and
# a test asserts no stage exceeds it. Lower this to force every request under the
# floor if routing ever reaches a low-capability endpoint.
OPENROUTER_MAX_COMPLETION_TOKENS = int(
    os.environ.get("OPENROUTER_MAX_COMPLETION_TOKENS", "128000")
)

# The largest request that still reaches a wide set of providers. Of the 22
# endpoints serving kimi-k2.6, only DeepInfra (16,384) and Venice (65,536)
# declare a cap below 256,000, so a request at or under 65,536 reaches 15 of
# them; above it, 13. No stage needs more, and a stage that crosses this should
# be a deliberate decision rather than a number nudged upward.
OPENROUTER_ROUTING_SAFE_COMPLETION_TOKENS = 65_536

# Operational escape hatch: comma-separated OpenRouter provider names to exclude
# from routing, e.g. "DeepInfra,Sail Research". Empty by default.
OPENROUTER_IGNORE_PROVIDERS = [
    name.strip()
    for name in os.environ.get("OPENROUTER_IGNORE_PROVIDERS", "").split(",")
    if name.strip()
]

# --------------------------------------------------------------------------- #
# Provider routing
#
# An explicit, ordered allow-list instead of OpenRouter's default routing.
#
# Every slug below was read from https://openrouter.ai/api/v1/providers and
# cross-checked against .../models/moonshotai/kimi-k2.6/endpoints — not inferred
# from a display name. As verified there, all three serve kimi-k2.6, all three
# advertise `response_format` AND `structured_outputs`, and all three advertise
# 262,144 max completion tokens, which clears the evaluate stage's 64,000 with
# room to spare.
#
# The lowercase slug is the routing token; the endpoint's `tag` adds the
# quantization it serves at (coreweave/fp4, decart/fp4, inceptron/int4). Order
# here is preference order.
# --------------------------------------------------------------------------- #
OPENROUTER_PROVIDER_ORDER = [
    name.strip()
    for name in os.environ.get(
        "OPENROUTER_PROVIDER_ORDER", "coreweave,decart,inceptron"
    ).split(",")
    if name.strip()
]

# --------------------------------------------------------------------------- #
# List pricing for the locked providers, USD per token
#
# Read on 2026-08-25 from
# https://openrouter.ai/api/v1/models/moonshotai/kimi-k2.6/endpoints — the same
# source, and the same way, the provider slugs above were verified. Not a blended
# headline figure from a marketing page. As read there, per million tokens:
#
#   tag              prompt    completion    cache read
#   decart/fp4       0.5493        2.3128        0.0925
#   inceptron/int4   0.5700        3.3900        0.2000
#   coreweave/fp4    0.6500        3.4100        0.1500
#
# These constants are the MAXIMUM of the three, not an average, so the figure is an
# upper bound rather than a guess. Which provider serves a given call is decided by
# OpenRouter at request time, and the running total is written before the answer is
# known — so a blend would be wrong in an unknown direction, while a ceiling is
# wrong in one known direction only. A cost display that can understate is worse
# than one that cannot, and the UI says "at most" rather than implying precision.
#
# Cached input is deliberately charged at the full prompt rate. It is cheaper in
# reality (see the column above), but `usage.prompt_tokens` already includes the
# cached tokens, so charging the difference would mean subtracting — which is the
# one direction this figure must not move. Re-read the endpoint above if prices
# drift; nothing here updates itself.
OPENROUTER_PRICE_PROMPT_USD = 0.65 / 1_000_000
OPENROUTER_PRICE_COMPLETION_USD = 3.41 / 1_000_000

# With fallbacks off, a request that cannot be served by one of the providers
# above FAILS rather than quietly routing elsewhere. That is the point: it is the
# only way to know the allow-list is actually being honoured, and it means a
# review never silently costs Moonshot-direct prices or lands on a 16k-output
# endpoint. The accepted trade-off is that all three being unavailable is an
# outage for us. Set OPENROUTER_ALLOW_FALLBACKS=1 to trade that back.
OPENROUTER_ALLOW_FALLBACKS = os.environ.get(
    "OPENROUTER_ALLOW_FALLBACKS", ""
).strip().lower() in ("1", "true", "yes")

# Wall-clock ceiling on a single model call, in seconds.
#
# There is no server-side deadline on a chat completion, so without this a stalled
# upstream is indistinguishable from a slow one and the pipeline waits forever. It
# has already cost a 94-minute hang that produced malformed JSON and no usable
# diagnostics — a fast, loud failure would have been strictly better.
#
# 300s is the deliberate value, raised from 120s after that ceiling stopped being
# "comfortably above every stage's observed latency" and started being the thing
# failing real reviews. The run that forced it: a signed 12-page SoW, classify
# finding 20 components from prose alone, then evaluate (Well-Architected, call 1
# of 2) hitting 120s, retrying a step down in effort, and hitting it again — the
# whole review dead at t+302.5s having produced nothing.
#
# Note what that number is NOT: it is not a per-stage budget. `_openrouter_attempt`
# retries a deadline once at a lower effort, so a stage that trips this twice costs
# 2 x this value — 600s at 300s — before it surfaces. Both attempts are billed.
#
# Raise this rather than removing it if a real design starts tripping it, and read
# the 94-minute hang above before considering the removal.
OPENROUTER_TIMEOUT_SECONDS = float(
    os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "300")
)

# Hard-fail a call whose response came from a provider outside
# OPENROUTER_PROVIDER_ORDER. On by default: a route that silently ignored the lock
# is exactly the failure this configuration exists to prevent, and a warning in a
# log nobody reads is how it went unnoticed once already.
OPENROUTER_ENFORCE_PROVIDER_LOCK = os.environ.get(
    "OPENROUTER_ENFORCE_PROVIDER_LOCK", "1"
).strip().lower() in ("1", "true", "yes")

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

# --------------------------------------------------------------------------- #
# Demo access gate
#
# A single shared token, deliberately not an auth system: it exists so the public
# Render URL cannot be used by anyone who finds it to read past reviews or spend
# our tokens. One day's worth of gate, no users, no sessions, no roles.
#
# It FAILS CLOSED. With no token configured every gated route returns 401, which
# is loud and fixable in seconds. Failing open would mean a forgotten dashboard
# variable silently leaves the API wide open — the exact failure this gate exists
# to prevent, and the same silent-misconfiguration trap as an unset CORS origin.
# --------------------------------------------------------------------------- #
DEMO_ACCESS_TOKEN = os.environ.get("DEMO_ACCESS_TOKEN", "").strip()
DEMO_TOKEN_HEADER = "X-Demo-Token"

# Never gated: Render polls this to decide whether the service is alive, and it
# has no credentials to send.
UNGATED_PATHS = frozenset({"/health"})

# Prefixes the demo gate does not cover. Exactly one, and it carries its own
# credential: a share link is handed to someone who deliberately does NOT have
# the demo token, so gating it would defeat the point. The route behind this
# prefix validates an HMAC share token per review and answers 404 without one —
# ungated here means "the shared secret is not the thing that authorises it",
# not "anyone may read it".
#
# A prefix rather than another exact path because the review id is in the URL.
# Kept as a tuple of one so adding a second is a visible decision rather than a
# widening nobody notices.
UNGATED_PREFIXES: tuple[str, ...] = ("/shared/",)


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
