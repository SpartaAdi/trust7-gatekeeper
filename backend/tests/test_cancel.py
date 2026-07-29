"""Cancellation: the signal, the pipeline's response to it, and the route.

The three things worth proving, in the order they matter:

1. A cancelled review STOPS STARTING STAGES. This is the whole point — the call in
   flight may well complete and bill, but the stages that would have followed it
   are where the money is.
2. A cancelled review is never stored or served as a result. Cancelled is not a
   truncated success.
3. Cancellation is not an error. The two have to stay distinguishable all the way
   to the screen.

`llm.complete_json` is stubbed throughout, so nothing here needs an API key.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import cancel
import config
import llm
import main
import storage
from agent import pipeline
from schema import ReviewStatus

DEMO_TOKEN = "cancel-demo-token"

DRAWIO = (
    b"""<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>"""
    b"""<mxCell id="api" value="API Gateway" style="shape=mxgraph.aws4.resourceIcon;"""
    b"""resIcon=mxgraph.aws4.api_gateway" vertex="1" parent="0"/>"""
    b"""</root></mxGraphModel></diagram></mxfile>"""
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """No test may inherit another's cancellation."""
    cancel.reset()
    yield
    cancel.reset()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    return tmp_path


@pytest.fixture
def client(env):
    return TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

def test_is_cancelled_is_false_until_requested() -> None:
    assert cancel.is_cancelled("rev-1") is False
    cancel.request("rev-1")
    assert cancel.is_cancelled("rev-1") is True


def test_requesting_twice_is_idempotent() -> None:
    cancel.request("rev-1")
    cancel.request("rev-1")
    assert cancel.is_cancelled("rev-1") is True
    cancel.clear("rev-1")
    assert cancel.is_cancelled("rev-1") is False


def test_cancelling_one_review_does_not_cancel_another() -> None:
    cancel.request("rev-1")
    assert cancel.is_cancelled("rev-2") is False


def test_check_raises_only_when_cancelled() -> None:
    cancel.check("rev-1")  # no exception
    cancel.request("rev-1")
    with pytest.raises(cancel.Cancelled):
        cancel.check("rev-1")


# --------------------------------------------------------------------------- #
# (a) Cancelling mid-stage stops further stages from starting
# --------------------------------------------------------------------------- #

def _stub_llm(monkeypatch, on_call) -> list[str]:
    """Record every stage that reaches the model, and let a hook interpose.

    This replaces `complete_json` wholesale, so the in-flight guard inside the
    adapters is not exercised by these tests — a stubbed call always "succeeds".
    That is deliberate: the stage-gating guarantee and the in-flight guarantee are
    different mechanisms, and the second is covered directly further down.
    """
    seen: list[str] = []

    def fake(*, system, content, schema, effort, max_tokens, label=""):
        seen.append(label)
        on_call(label)
        required = set(schema.get("required", []))
        if "design_summary" in required:
            return {"design_summary": "x", "components": []}, {}
        if "findings" in required:
            return {"findings": []}, {}
        if "ranking" in required:
            return {"summary": "x", "ranking": []}, {}
        return {"executive_summary": "x", "remediations": []}, {}

    monkeypatch.setattr(llm, "complete_json", fake)
    return seen


def test_cancelling_during_a_stage_stops_the_stages_after_it(env, monkeypatch) -> None:
    """The headline guarantee.

    Cancellation is requested from inside the classify call — the closest a test can
    get to the reviewer clicking Stop while a request is on the wire. Classify's own
    response still arrives, which is the accepted cost; what must not happen is
    evaluate, prioritize and remediate each placing their own call afterwards.
    """
    review_id = "11111111-2222-3333-4444-555555555555"
    storage.put_status(ReviewStatus.initial(review_id))

    def interpose(label: str) -> None:
        if label.startswith("classify"):
            cancel.request(review_id)

    seen = _stub_llm(monkeypatch, interpose)

    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    # Classify ran and was paid for. Nothing after it did.
    assert any(label.startswith("classify") for label in seen)
    for later in ("evaluate", "prioritize", "remediate"):
        assert not any(label.startswith(later) for label in seen), (
            f"{later} started after cancellation; calls seen: {seen}"
        )


def test_cancelling_between_stages_stops_the_next_one(env, monkeypatch) -> None:
    review_id = "22222222-3333-4444-5555-666666666666"
    storage.put_status(ReviewStatus.initial(review_id))
    # Cancelled before the pipeline starts: not one call should be placed.
    cancel.request(review_id)

    seen = _stub_llm(monkeypatch, lambda label: None)

    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    assert seen == [], f"a cancelled review placed calls: {seen}"


def test_the_gate_lives_in_progress_start_so_no_stage_can_omit_it() -> None:
    """Structural, and deliberately so.

    Six stages each remembering to check a flag is six chances to forget, and a
    seventh stage added later would inherit none of them. The check is inside
    `_Progress.start`, which every stage calls, so this asserts the design rather
    than the behaviour — behaviour is covered above.
    """
    import inspect

    source = inspect.getsource(pipeline._Progress.start)
    assert "cancel.check" in source
    # Before the status write, or a cancelled review records a stage as running.
    assert source.index("cancel.check") < source.index('state = "running"')


def test_an_llm_call_refuses_to_open_once_cancelled(monkeypatch) -> None:
    """The in-flight half of the mechanism, at the seam it actually guards.

    `_check_cancelled` sits at the single SDK call point rather than in the retry
    wrapper, which is what stops the existing one-shot retry from answering an
    aborted call with a fresh full-price one.
    """
    calls: list[int] = []
    monkeypatch.setattr(
        llm, "_openrouter_client", lambda: calls.append(1) or object()
    )

    with llm.cancellation(lambda: True):
        with pytest.raises(cancel.Cancelled):
            llm._openrouter_create({"model": "x"})

    assert calls == [], "a cancelled review still built a client and sent a request"


def test_the_cancellation_predicate_does_not_leak_out_of_its_scope() -> None:
    with llm.cancellation(lambda: True):
        with pytest.raises(cancel.Cancelled):
            llm._check_cancelled()
    # Outside the block there is no predicate, so nothing is cancelled.
    llm._check_cancelled()


def test_abort_in_flight_reuses_the_deadline_mechanism(monkeypatch) -> None:
    """No second cancellation path: the public entry point IS the deadline's close."""
    called: list[str] = []
    monkeypatch.setattr(llm, "_abort_transport", lambda: called.append("closed"))
    llm.abort_in_flight()
    assert called == ["closed"]


# --------------------------------------------------------------------------- #
# (c) A cancelled review is never stored or served as complete
# --------------------------------------------------------------------------- #

def test_a_cancelled_review_stores_no_result(env, monkeypatch) -> None:
    review_id = "33333333-4444-5555-6666-777777777777"
    storage.put_status(ReviewStatus.initial(review_id))

    def interpose(label: str) -> None:
        if label.startswith("classify"):
            cancel.request(review_id)

    _stub_llm(monkeypatch, interpose)
    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    assert storage.get_review(review_id) is None, "a cancelled review was persisted"


def test_a_cancel_during_the_last_stage_still_stores_nothing(env, monkeypatch) -> None:
    """The gap `progress.start` cannot cover.

    Nothing calls `start` between the final stage finishing and the result being
    written, so without an explicit check there the review would be stored and
    served as a finished one.
    """
    review_id = "44444444-5555-6666-7777-888888888888"
    storage.put_status(ReviewStatus.initial(review_id))

    def interpose(label: str) -> None:
        if label.startswith("remediate"):
            cancel.request(review_id)

    seen = _stub_llm(monkeypatch, interpose)
    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    assert any(label.startswith("remediate") for label in seen), "remediate did not run"
    assert storage.get_review(review_id) is None
    assert storage.get_status(review_id).state == "cancelled"


def test_the_status_says_cancelled_not_error(env, monkeypatch) -> None:
    review_id = "55555555-6666-7777-8888-999999999999"
    storage.put_status(ReviewStatus.initial(review_id))

    def interpose(label: str) -> None:
        if label.startswith("classify"):
            cancel.request(review_id)

    _stub_llm(monkeypatch, interpose)
    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    status = storage.get_status(review_id)
    assert status.state == "cancelled"
    # Not an error, and carrying no error text to render.
    assert status.error == ""

    # The stage marked is the one the pipeline STOPPED AT, which is `evaluate` and
    # not `classify`: classify's own response had already been paid for and arrived,
    # so it is recorded as done. `evaluate` is where the gate fired, and marking it
    # is what tells the reviewer how far the run got.
    assert [s.state for s in status.stages if s.name == "classify"] == ["done"]
    interrupted = [s for s in status.stages if s.state == "cancelled"]
    assert [s.name for s in interrupted] == ["evaluate"]
    assert interrupted[0].detail == "Stopped by the reviewer"
    # No stage is left mid-flight on a stopped review — a spinner on a stopped
    # screen would read as still working.
    assert not any(s.state == "running" for s in status.stages)
    # And the stages beyond the stopping point stay pending rather than being
    # dressed up as anything.
    assert [s.state for s in status.stages if s.name in ("prioritize", "remediate")] == [
        "pending",
        "pending",
    ]


def test_the_result_route_refuses_a_cancelled_review(client, env, monkeypatch) -> None:
    review_id = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    storage.put_status(ReviewStatus.initial(review_id))

    def interpose(label: str) -> None:
        if label.startswith("classify"):
            cancel.request(review_id)

    _stub_llm(monkeypatch, interpose)
    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    response = client.get(f"/reviews/{review_id}")
    assert response.status_code == 409
    assert "cancelled" in response.json()["detail"]
    # And the PDF, which reads through the same guard.
    assert client.get(f"/reviews/{review_id}/report.pdf").status_code == 409


def test_a_cancelled_review_is_absent_from_history(client, env, monkeypatch) -> None:
    review_id = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
    storage.put_status(ReviewStatus.initial(review_id))

    def interpose(label: str) -> None:
        if label.startswith("classify"):
            cancel.request(review_id)

    _stub_llm(monkeypatch, interpose)
    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    listed = [item["review_id"] for item in client.get("/reviews").json()]
    assert review_id not in listed


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #

def test_cancel_marks_the_status_immediately(client, monkeypatch) -> None:
    """The reviewer should not wait for a stage boundary to see the click land."""
    review_id = "88888888-9999-aaaa-bbbb-cccccccccccc"
    status = ReviewStatus.initial(review_id)
    status.state = "running"
    storage.put_status(status)
    monkeypatch.setattr(llm, "_abort_transport", lambda: None)

    response = client.post(f"/reviews/{review_id}/cancel")

    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert storage.get_status(review_id).state == "cancelled"
    assert cancel.is_cancelled(review_id) is True


def test_cancel_aborts_the_call_on_the_wire(client, monkeypatch) -> None:
    review_id = "99999999-aaaa-bbbb-cccc-dddddddddddd"
    storage.put_status(ReviewStatus.initial(review_id))
    closed: list[str] = []
    monkeypatch.setattr(llm, "_abort_transport", lambda: closed.append("closed"))

    client.post(f"/reviews/{review_id}/cancel")

    assert closed == ["closed"], "cancel did not close the transport"


def test_cancel_is_a_conflict_on_a_finished_review(client, monkeypatch) -> None:
    review_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setattr(llm, "_abort_transport", lambda: None)

    for state in ("complete", "error", "cancelled"):
        status = ReviewStatus.initial(review_id)
        status.state = state  # type: ignore[assignment]
        storage.put_status(status)

        response = client.post(f"/reviews/{review_id}/cancel")
        assert response.status_code == 409, state
        assert state in response.json()["detail"]
        # And the stored state is untouched — a complete review keeps its result.
        assert storage.get_status(review_id).state == state


def test_cancel_on_an_unknown_review_is_404(client) -> None:
    response = client.post("/reviews/bbbbbbbb-cccc-dddd-eeee-ffffffffffff/cancel")
    assert response.status_code == 404


def test_cancel_rejects_a_malformed_review_id(client) -> None:
    assert client.post("/reviews/..%2fetc%2fpasswd/cancel").status_code in (400, 404)


def test_cancel_requires_the_demo_token(env) -> None:
    """The route is behind the same gate as everything else."""
    review_id = "cccccccc-dddd-eeee-ffff-000000000000"
    storage.put_status(ReviewStatus.initial(review_id))

    ungated = TestClient(main.app)
    assert ungated.post(f"/reviews/{review_id}/cancel").status_code == 401
    # And nothing was cancelled by the attempt.
    assert cancel.is_cancelled(review_id) is False


def test_the_registry_is_cleared_when_a_run_ends(env, monkeypatch) -> None:
    """Otherwise the set grows for the lifetime of the process."""
    review_id = "dddddddd-eeee-ffff-0000-111111111111"
    storage.put_status(ReviewStatus.initial(review_id))
    cancel.request(review_id)
    _stub_llm(monkeypatch, lambda label: None)

    with pytest.raises(cancel.Cancelled):
        pipeline.run(review_id=review_id, diagram_key=_upload(env, review_id))

    assert cancel.is_cancelled(review_id) is False


def _upload(data_dir, review_id: str) -> str:
    """Put a diagram on disk and return its key, without going through the route."""
    return storage.save_upload(f"{review_id}.drawio", DRAWIO)
