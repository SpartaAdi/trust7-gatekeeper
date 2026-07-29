"""Trust7 Gatekeeper — FastAPI application.

Run with `uvicorn main:app`. Deployed as a Render web service; there is no
serverless target and no Lambda adapter.
"""

import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
from api.routes import router as api_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(
    title="Trust7 Gatekeeper",
    description="AI governance agent reviewing solution designs against the "
    "AWS Well-Architected Framework and Minfy TRUST-7.",
    version="0.1.0",
)


# --------------------------------------------------------------------------- #
# Demo access gate
#
# Registered BEFORE the CORS middleware on purpose. Starlette runs the most
# recently added middleware outermost, so adding CORS second puts it outside this
# one — which means a 401 still comes back with the Access-Control-Allow-Origin
# header. Without that the browser reports an opaque CORS failure and the user
# never sees the "token is wrong" message.
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def require_demo_token(request: Request, call_next):
    """Reject anything without the shared token, except the health check.

    OPTIONS is also skipped, because browsers attach no custom headers to a
    preflight and gating it would block every cross-origin request before the
    real one was ever sent. In practice CORSMiddleware sits outside this and
    answers preflight itself, so the check here is defence in depth against a
    future reordering rather than the thing making preflight work today — what
    makes it work is CORS being registered last. `test_a_preflight_is_not_gated`
    covers the behaviour; `test_options_is_skipped_by_the_gate_itself` covers
    this branch directly, since the outer CORS layer otherwise hides it.
    """
    if request.method == "OPTIONS" or request.url.path in config.UNGATED_PATHS:
        return await call_next(request)

    # Share links carry their own per-review HMAC instead of the shared token —
    # see config.UNGATED_PREFIXES and share.py. The route itself refuses a bad
    # or missing one, so skipping the gate here does not make it readable.
    if request.url.path.startswith(config.UNGATED_PREFIXES):
        return await call_next(request)

    if not config.DEMO_ACCESS_TOKEN:
        return JSONResponse(
            status_code=401,
            content={
                "detail": "This API is closed: DEMO_ACCESS_TOKEN is not set on the "
                "server. Set it in the Render dashboard (Environment tab)."
            },
        )

    supplied = request.headers.get(config.DEMO_TOKEN_HEADER, "")
    # compare_digest rather than == so the comparison does not leak the token's
    # length or prefix through timing. Cheap, so there is no reason not to.
    if not supplied or not secrets.compare_digest(supplied, config.DEMO_ACCESS_TOKEN):
        return JSONResponse(
            status_code=401,
            content={
                "detail": f"Missing or invalid {config.DEMO_TOKEN_HEADER} header. "
                "Enter the demo access token to continue."
            },
        )

    return await call_next(request)


# One exact origin, never a wildcard: the browser sends the user's origin, and a
# wildcard would let any site call this API on their behalf. The origin itself is
# never hardcoded here — it comes from config, which reads the environment first.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.CORS_ALLOWED_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Log which origin actually won, and where it came from. Without this a missing
# or mistyped dashboard variable shows up only as an opaque CORS failure in the
# browser, with nothing on the server to explain it.
if config.CORS_ORIGIN_FROM_ENV:
    log.info(
        "CORS: allowing exactly %s (from the CORS_ALLOWED_ORIGIN environment "
        "variable)",
        config.CORS_ALLOWED_ORIGIN,
    )
else:
    log.warning(
        "CORS: CORS_ALLOWED_ORIGIN is not set; falling back to the development "
        "default %s. A deployed frontend will be blocked until this is set in "
        "the Render dashboard (Environment tab).",
        config.CORS_ALLOWED_ORIGIN,
    )

# Same reasoning as the CORS line above: a gate nobody can see the state of is a
# gate nobody notices is misconfigured.
if config.DEMO_ACCESS_TOKEN:
    log.info(
        "Demo gate: active, %s required on every route except %s.",
        config.DEMO_TOKEN_HEADER,
        ", ".join(sorted(config.UNGATED_PATHS)),
    )
else:
    log.warning(
        "Demo gate: DEMO_ACCESS_TOKEN is not set, so every gated route will "
        "return 401. Set it in the Render dashboard (Environment tab)."
    )


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict[str, str]:
    """Liveness probe — also Render's health check path. Never gated.

    HEAD as well as GET because free uptime pingers, used to keep Render's free
    tier from spinning down, often send HEAD by default. A GET-only route answers
    those with 405, which still resets the idle timer but shows the service as
    down on the monitor's dashboard.
    """
    return {"status": "ok", "service": "trust7-gatekeeper"}


app.include_router(api_router)
