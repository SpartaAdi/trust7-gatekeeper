"""Read-only share links for a single completed review.

## The token

Derived, not stored: `HMAC(gate token, review id)`. That choice is the whole
design, and it is what makes a link survive a restart of the service — there is
no share registry to lose. A stored random token on Render's free tier would be
gone with the disk it was written to, and every link ever issued would break.

The HMAC key is `DEMO_ACCESS_TOKEN` rather than a new secret. Reusing it:

* adds no configuration, and fails closed in exactly the same circumstance the
  rest of the API already does — no token set, no sharing;
* grants nobody anything new. Forging a share token requires the gate token, and
  anyone holding that can already read every review directly. So the reuse costs
  no privilege separation that exists today;
* means rotating the gate token invalidates every outstanding share link. For a
  demo that is a feature — one rotation revokes everything handed out.

What a link deliberately does NOT carry is the gate token itself, so sharing a
review with an outsider never hands them access to the rest of the API.

## What a link does not survive

The token is durable; **the review it points at is not**. Reviews live in
`local-data/`, which on Render's free tier is ephemeral — it is wiped when the
service restarts or spins down from idle. So a share link keeps working exactly
as long as the review file behind it exists, and after a restart it answers 404.

That is stated rather than engineered around: making it durable means a database
or an object store, and both are ruled out by the project's constraints. The UI
says so on the page, and `SharedReview.expires_note` carries the same sentence to
any client that renders one.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import config

# Long enough that guessing is hopeless, short enough to paste in a chat message.
# 32 hex characters is 128 bits.
_TOKEN_CHARS = 32

SHARING_DISABLED_REASON = (
    "Sharing is unavailable: DEMO_ACCESS_TOKEN is not set on the server, and the "
    "share token is derived from it."
)

EPHEMERAL_NOTE = (
    "This link stops working when the server restarts. Reviews are stored on "
    "Render's free-tier disk, which is wiped on restart and after a period of "
    "idleness — the link itself stays valid, but the review behind it is gone."
)


def sharing_enabled() -> bool:
    return bool(config.DEMO_ACCESS_TOKEN)


def token_for(review_id: str) -> str:
    """The share token for a review. Same input, same token, for the life of the
    gate token — which is what lets a link outlive the process that issued it."""
    if not sharing_enabled():
        raise RuntimeError(SHARING_DISABLED_REASON)
    digest = hmac.new(
        config.DEMO_ACCESS_TOKEN.encode(),
        review_id.encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:_TOKEN_CHARS]


def is_valid(review_id: str, token: str) -> bool:
    """Whether `token` is the share token for `review_id`.

    `compare_digest` rather than `==`: the comparison must not leak how much of a
    guessed token was right through how long the comparison took.
    """
    if not sharing_enabled() or not token:
        return False
    return secrets.compare_digest(token, token_for(review_id))
