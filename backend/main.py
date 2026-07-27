"""Trust7 Gatekeeper — FastAPI application.

Run with `uvicorn main:app`. Deployed as a Render web service; there is no
serverless target and no Lambda adapter.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api.routes import router as api_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Trust7 Gatekeeper",
    description="AI governance agent reviewing solution designs against the "
    "AWS Well-Architected Framework and Minfy TRUST-7.",
    version="0.1.0",
)

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
    logging.getLogger(__name__).info(
        "CORS: allowing exactly %s (from the CORS_ALLOWED_ORIGIN environment "
        "variable)",
        config.CORS_ALLOWED_ORIGIN,
    )
else:
    logging.getLogger(__name__).warning(
        "CORS: CORS_ALLOWED_ORIGIN is not set; falling back to the development "
        "default %s. A deployed frontend will be blocked until this is set in "
        "the Render dashboard (Environment tab).",
        config.CORS_ALLOWED_ORIGIN,
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — also Render's health check path."""
    return {"status": "ok", "service": "trust7-gatekeeper"}


app.include_router(api_router)
