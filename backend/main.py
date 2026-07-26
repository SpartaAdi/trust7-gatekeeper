"""Trust7 Gatekeeper — Lambda entrypoint.

FastAPI application wrapped by Mangum so the same app runs locally
(`uvicorn main:app`) and behind API Gateway HTTP API on Lambda.

No business logic lives here. Routers from `api/` are mounted as they
are implemented.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

app = FastAPI(
    title="Trust7 Gatekeeper",
    description="AI governance agent reviewing solution designs against the "
    "AWS Well-Architected Framework and Minfy TRUST-7.",
    version="0.1.0",
)

# Comma-separated list of allowed origins; defaults to the local Vite dev server.
_allowed_origins = os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": "trust7-gatekeeper"}


# Routers are registered here as they land, e.g.:
# from api import reviews
# app.include_router(reviews.router)

handler = Mangum(app)
