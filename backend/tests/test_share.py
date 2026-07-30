"""Read-only share links.

Two things are being protected. First, that a link authorises exactly one review
and nothing else — it bypasses the demo gate, so a flaw here is an open API.
Second, that the link says what it cannot do: the token outlives a restart, the
review it points at does not, and the response has to admit that rather than
imply permanence.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

import config
import llm
import share

DEMO_TOKEN = "share-suite-demo-token"
OTHER_TOKEN = "a-different-gate-token"


def _stub_complete_json():
    usage = {"input_tokens": 900, "output_tokens": 200}

    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        required = set(schema.get("required", []))
        if "design_summary" in required:
            return {
                "design_summary": "A small API on AWS.",
                "components": [
                    {
                        "id": "api",
                        "label": "API Gateway",
                        "kind": "gateway",
                        "provider": "aws",
                        "service": "api gateway",
                        "attributes": [],
                    }
                ],
                "data_flows": [],
                "assumptions": [],
            }, usage
        if "findings" in required:
            import rubric

            return {
                "findings": [
                    {
                        "check_id": check.check_id,
                        "status": "fail",
                        "severity": "high",
                        "evidence": "SECRET-EVIDENCE-STRING not stated in the design.",
                        "rationale": "SECRET-RATIONALE-STRING no encryption described.",
                        "confidence": "high",
                    }
                    for check in rubric.all_checks()[:2]
                ]
            }, usage
        if "ranking" in required:
            return {"summary": "Gaps to close.", "ranking": []}, usage
        if "remediations" in required:
            return {
                "executive_summary": "Below band.",
                "remediations": [],
            }, usage
        raise AssertionError(f"unexpected schema: {sorted(required)}")

    return fake


@pytest.fixture
def app(monkeypatch, tmp_path):
    """The real app with a completed review already stored."""
    import main
    import storage

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    importlib.reload(storage)
    importlib.reload(main)
    monkeypatch.setattr(llm, "complete_json", _stub_complete_json())

    client = TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})
    upload = client.post(
        "/uploads", files={"file": ("sow.md", b"# Design\n", "text/markdown")}
    )
    accepted = client.post(
        "/reviews",
        json={"document_key": upload.json()["key"], "title": "Payments platform"},
    )
    review_id = accepted.json()["review_id"]
    state = client.get(f"/reviews/{review_id}/status").json()["state"]
    assert state == "complete", f"fixture review did not finish: {state}"

    yield client, review_id

    importlib.reload(storage)
    importlib.reload(main)


def _share(client: TestClient, review_id: str) -> dict[str, Any]:
    response = client.get(f"/reviews/{review_id}/share")
    assert response.status_code == 200, response.text
    return response.json()


def _ungated(client: TestClient) -> TestClient:
    """A client with no demo token at all — what a recipient of a link has."""
    client.headers.pop(config.DEMO_TOKEN_HEADER, None)
    return client


# --------------------------------------------------------------------------- #
# The token
# --------------------------------------------------------------------------- #


def test_the_same_review_always_mints_the_same_token(app) -> None:
    """Derived, not stored — which is the only reason a link can outlive the
    process that issued it on a disk that does not persist."""
    client, review_id = app

    assert _share(client, review_id)["token"] == _share(client, review_id)["token"]


def test_the_token_survives_a_restart_because_nothing_stored_it(app, monkeypatch) -> None:
    """Simulates the restart: wipe the data directory and rebuild the module
    state. The token must still be computable from the review id alone."""
    client, review_id = app
    before = _share(client, review_id)["token"]

    importlib.reload(share)

    assert share.token_for(review_id) == before


def test_different_reviews_get_different_tokens(app) -> None:
    client, review_id = app

    assert share.token_for(review_id) != share.token_for("00000000-0000-0000-0000-000000000000")


def test_rotating_the_gate_token_revokes_every_link(app, monkeypatch) -> None:
    """The documented consequence of deriving from DEMO_ACCESS_TOKEN, asserted so
    it is a known property rather than a surprise."""
    client, review_id = app
    issued = share.token_for(review_id)

    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", OTHER_TOKEN)

    assert share.token_for(review_id) != issued
    assert not share.is_valid(review_id, issued)


def test_sharing_is_off_when_the_gate_token_is_unset(app, monkeypatch) -> None:
    """Fails closed, like every other credential in this codebase."""
    client, review_id = app
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "")

    assert not share.sharing_enabled()
    assert not share.is_valid(review_id, "anything")
    with pytest.raises(RuntimeError):
        share.token_for(review_id)

    # 401, not 409: with no token configured the gate refuses every request
    # before the route is reached. The 409 branch in `create_share_link` is
    # therefore unreachable through the app and only fires if the gate is ever
    # relaxed — which is exactly when it would matter.
    assert client.get(f"/reviews/{review_id}/share").status_code == 401


# --------------------------------------------------------------------------- #
# The link, used by someone who has no demo token
# --------------------------------------------------------------------------- #


def test_a_valid_link_works_without_the_demo_token(app) -> None:
    client, review_id = app
    token = _share(client, review_id)["token"]

    response = _ungated(client).get(f"/shared/{review_id}?t={token}")

    assert response.status_code == 200, response.text
    assert response.json()["review_id"] == review_id
    assert response.json()["overall_score"] >= 0


def test_the_link_carries_scores_and_their_movement(app) -> None:
    client, review_id = app
    token = _share(client, review_id)["token"]

    body = _ungated(client).get(f"/shared/{review_id}?t={token}").json()

    assert body["pillars"], "a scoreboard with no pillars is not a scoreboard"
    assert body["frameworks"], "the frameworks being scored are part of the trend"
    assert body["open_findings"] >= 1
    assert body["component_count"] >= 1
    assert "delta" in body


def test_the_link_does_not_leak_finding_text(app) -> None:
    """An ungated URL is a public URL. Evidence, rationale and remediation are the
    parts of a review most likely to quote a customer's design back."""
    client, review_id = app
    token = _share(client, review_id)["token"]

    body = _ungated(client).get(f"/shared/{review_id}?t={token}").text

    assert "SECRET-EVIDENCE-STRING" not in body
    assert "SECRET-RATIONALE-STRING" not in body
    assert "findings" not in body.lower().replace("open_findings", "")


def test_the_link_does_not_leak_the_gate_token(app) -> None:
    """The token is derived from DEMO_ACCESS_TOKEN; a response that echoed it
    would hand the recipient full API access."""
    client, review_id = app
    token = _share(client, review_id)["token"]

    body = _ungated(client).get(f"/shared/{review_id}?t={token}").text

    assert DEMO_TOKEN not in body


def test_a_wrong_token_is_refused(app) -> None:
    client, review_id = app

    response = _ungated(client).get(f"/shared/{review_id}?t={'0' * 32}")

    assert response.status_code == 404


def test_a_missing_token_is_refused(app) -> None:
    client, review_id = app

    assert _ungated(client).get(f"/shared/{review_id}").status_code == 404


def test_one_reviews_token_does_not_open_another(app) -> None:
    """The failure that would turn one shared link into a key to every review."""
    client, review_id = app
    token = _share(client, review_id)["token"]
    other_id = "11111111-1111-1111-1111-111111111111"

    assert _ungated(client).get(f"/shared/{other_id}?t={token}").status_code == 404


def test_every_refusal_looks_identical(app) -> None:
    """Distinguishing "wrong token" from "no such review" lets a link holder
    enumerate which review ids exist.

    The fourth case is the one that matters and the one a first draft of this
    test missed: a *valid* token for a review that is not on disk. The first
    three all stop at the token check, so on their own they cannot tell whether
    the second 404 branch says something different — a mutant that gave it its
    own message survived until this case was added.
    """
    client, review_id = app
    ungated = _ungated(client)
    absent_id = "44444444-4444-4444-4444-444444444444"

    wrong_token = ungated.get(f"/shared/{review_id}?t={'0' * 32}")
    unknown_id = ungated.get(f"/shared/22222222-2222-2222-2222-222222222222?t={'0' * 32}")
    malformed_id = ungated.get(f"/shared/not-a-uuid-at-all!!?t={'0' * 32}")
    valid_token_no_review = ungated.get(
        f"/shared/{absent_id}?t={share.token_for(absent_id)}"
    )

    responses = [wrong_token, unknown_id, malformed_id, valid_token_no_review]
    assert {r.status_code for r in responses} == {404}
    details = {r.json()["detail"] for r in responses}
    assert len(details) == 1, f"refusals are distinguishable: {details}"


def test_the_share_route_itself_is_still_gated(app) -> None:
    """Reading a shared review needs no demo token; minting a link does. Without
    this, anyone could mint a link for any review id they guessed."""
    client, review_id = app

    assert _ungated(client).get(f"/reviews/{review_id}/share").status_code == 401


def test_an_unfinished_review_cannot_be_shared(app) -> None:
    client, review_id = app
    import storage

    upload = client.post(
        "/uploads", files={"file": ("sow.md", b"# Design\n", "text/markdown")}
    )
    from schema import ReviewStatus

    pending_id = "33333333-3333-3333-3333-333333333333"
    storage.put_status(ReviewStatus.initial(pending_id))

    assert client.get(f"/reviews/{pending_id}/share").status_code == 409
    # And the derived token opens nothing, because there is no stored result.
    token = share.token_for(pending_id)
    assert _ungated(client).get(f"/shared/{pending_id}?t={token}").status_code == 404


# --------------------------------------------------------------------------- #
# Honesty about the ephemeral disk
# --------------------------------------------------------------------------- #


def test_both_responses_state_that_the_link_does_not_outlive_a_restart(app) -> None:
    """The project's constraint is a local filesystem on a free tier that wipes
    it. Saying nothing would be an implied promise of permanence."""
    client, review_id = app
    token = _share(client, review_id)["token"]

    minted = _share(client, review_id)
    fetched = _ungated(client).get(f"/shared/{review_id}?t={token}").json()

    for note in (minted["expires_note"], fetched["expires_note"]):
        assert "restart" in note.lower()
        assert note == share.EPHEMERAL_NOTE


def test_a_valid_token_answers_404_once_the_disk_is_wiped(app, tmp_path) -> None:
    """What a restart actually looks like: the token still verifies, the review
    file is gone, and the link answers 404 rather than an error nobody expects."""
    client, review_id = app
    token = _share(client, review_id)["token"]

    for path in (tmp_path / "reviews").glob("*.json"):
        path.unlink()

    assert share.is_valid(review_id, token), "the token itself is unaffected"
    assert _ungated(client).get(f"/shared/{review_id}?t={token}").status_code == 404
