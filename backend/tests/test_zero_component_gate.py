"""The zero-component gate: stop after classify when there is nothing to score.

The screen gate refuses things that are not solution designs. This one refuses a
solution design that yielded no inventory — no components from classify AND none
from the diagram graph. Evaluate would otherwise score an empty set and return a
number, and a number produced from nothing is indistinguishable, on the results
page, from a number produced from evidence.

Both conditions are required, and the tests that matter here are the ones proving
the gate does NOT fire:

* `test_twenty_components_do_not_trigger_the_gate` uses 20, the count classify
  actually returned on the real RMBL-Control-Tower SoW from its prose alone. That
  run is the reason the gate has an inventory floor of "any" rather than "enough".
* `test_diagram_components_alone_do_not_trigger_the_gate` covers the asymmetric
  case: classify came back empty but the diagram gave us an inventory, which is a
  provider quality dip, not an empty design. `stages.classify`'s own docstring is
  explicit that a doubly-empty classify must not discard an otherwise sound review,
  and the graph half of the AND is what honours that.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import config
import llm
import rubric

DEMO_TOKEN = "zero-component-gate-token"

# Prose that reads as a design — the screen gate must pass it — but which the stub
# classify returns nothing for. Mirrors the shape of the real failure: a document
# describing intent and outcomes, with the architecture living in a diagram that was
# never uploaded.
SOW_WITHOUT_ARCHITECTURE = (
    b"# Control Tower Engagement\n\n"
    b"## Objectives\nEstablish a governed multi-account landing zone that meets the "
    b"client's audit obligations and shortens onboarding for new workloads.\n\n"
    b"## Scope\nDiscovery, design, and a phased rollout across three business units. "
    b"Success is measured by audit findings closed and time-to-onboard.\n\n"
    b"## Target Architecture\nSee the accompanying architecture diagram.\n"
)

DRAWIO = b"""<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>
<mxCell id="api" value="API Gateway" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.api_gateway" vertex="1" parent="0"/>
<mxCell id="db" value="Orders DynamoDB" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.dynamodb" vertex="1" parent="0"/>
</root></mxGraphModel></diagram></mxfile>"""


def _components(count: int) -> list[dict]:
    """`count` schema-valid classify components."""
    return [
        {
            "id": f"c{i}",
            "label": f"Component {i}",
            "kind": "compute",
            "provider": "aws",
            "service": "Amazon EC2",
            "attributes": [],
        }
        for i in range(count)
    ]


def _stub(component_count: int, calls: list[str]):
    """A whole-pipeline stub whose classify inventory size is dictated per test."""
    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        calls.append(label)
        required = set(schema.get("required", []))

        if "verdict" in required:
            return {
                "verdict": "reviewable",
                "subject": "a statement of work",
                "reason": "It describes a system to be built.",
                "confidence": "high",
            }, {}
        if "design_summary" in required:
            return {
                "design_summary": "A governed landing zone.",
                "components": _components(component_count),
                "data_flows": [], "observations": [], "absent": [],
            }, {}
        if "findings" in required:
            return {"findings": [
                {
                    "check_id": c.check_id, "status": "fail", "severity": c.severity,
                    "severity_rationale": "s", "title": c.description, "evidence": "e",
                    "affected_components": [],
                }
                for c in rubric.all_checks()
            ]}, {}
        if "ranking" in required:
            return {"summary": "- s", "ranking": []}, {}
        return {"executive_summary": "s", "remediations": [], "use_case_notes": []}, {}

    return fake


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main
    import storage

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    return TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})


def _submit(client: TestClient, *, diagram: bytes | None = None) -> str:
    body = {}
    body["document_key"] = client.post(
        "/uploads",
        files={"file": ("sow.md", SOW_WITHOUT_ARCHITECTURE, "text/markdown")},
    ).json()["key"]
    if diagram is not None:
        body["diagram_key"] = client.post(
            "/uploads", files={"file": ("design.drawio", diagram, "application/xml")}
        ).json()["key"]
    accepted = client.post("/reviews", json=body)
    assert accepted.status_code == 202
    return accepted.json()["review_id"]


# --------------------------------------------------------------------------- #
# The gate fires
# --------------------------------------------------------------------------- #

def test_an_empty_inventory_from_both_sources_is_rejected(client, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(client)
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] == "rejected"
    assert (
        "Upload an SOW that includes an architecture diagram, or upload an "
        "architecture diagram." in status["rejection"]
    )
    # `error` stays EMPTY, as for the screen gate: the UI renders `error` under a
    # "Pipeline error" heading, and a refusal is not a fault.
    assert status["error"] == ""


def test_the_rejected_stage_is_classify_and_says_why(client, monkeypatch) -> None:
    """The stage line must not claim the screen gate's finding.

    `progress.rejected` defaults to "Not a solution design — not reviewed". That is
    false here — screen already accepted this as a design — and it contradicts the
    rejection message shown next to it.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(client)
    status = client.get(f"/reviews/{review_id}/status").json()

    classify = next(s for s in status["stages"] if s["name"] == "classify")
    assert classify["state"] == "rejected"
    assert classify["detail"] == "No components identified — not reviewed"
    assert "Not a solution design" not in classify["detail"]


def test_the_gate_stops_before_the_four_expensive_calls(client, monkeypatch) -> None:
    """What the gate is for: not spending evaluate x2, prioritize and remediate.

    Note the call count is three, not two. `stages.classify` retries once at the same
    effort when it returns an empty inventory against a design that has content, so a
    text-bearing document costs `classify` + `classify:retry-empty` before the gate
    can see a settled answer. The saving is the four calls that follow, which include
    both evaluate calls at 64,000 output tokens each.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    _submit(client)

    assert calls == ["screen", "classify", "classify:retry-empty"], (
        f"expected the pipeline to stop at the gate, got {calls}"
    )


# --------------------------------------------------------------------------- #
# The gate does NOT fire — the half that protects real reviews
# --------------------------------------------------------------------------- #

def test_twenty_components_do_not_trigger_the_gate(client, monkeypatch) -> None:
    """20 is the real number.

    On RMBL-Control-Tower-SOW-v1.0 (Signed).pdf, with no diagram uploaded and so zero
    graph components, classify returned 20 components from the document's prose alone
    in 44.2s. That review must run: the gate's AND is what lets it, and a gate keyed
    on "no diagram" rather than "no inventory" would have killed it.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(20, calls))

    review_id = _submit(client)
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] != "rejected", status.get("rejection")
    assert status["rejection"] == ""
    classify = next(s for s in status["stages"] if s["name"] == "classify")
    assert classify["state"] == "done"
    assert classify["detail"] == "20 components classified"
    # The stage the gate exists to protect actually ran.
    assert "evaluate" in " ".join(calls)


@pytest.mark.parametrize("count", [1, 20])
def test_any_inventory_at_all_is_enough(client, monkeypatch, count) -> None:
    """The floor is "any", not "enough". A one-component design is a thin review,
    not an unreviewable one, and deciding otherwise is the rubric's job."""
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(count, calls))

    review_id = _submit(client)
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] != "rejected", status.get("rejection")


def test_diagram_components_alone_do_not_trigger_the_gate(client, monkeypatch) -> None:
    """Classify empty, graph not — a provider quality dip, not an empty design.

    `stages.classify` is explicit that a doubly-empty inventory must not discard an
    otherwise sound review, because evaluate reads the normalized design text rather
    than this inventory. The graph half of the AND is what keeps that true.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(client, diagram=DRAWIO)
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] != "rejected", status.get("rejection")
    assert "evaluate" in " ".join(calls)
