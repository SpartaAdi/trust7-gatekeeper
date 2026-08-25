"""The zero-component gate: stop after classify when there is nothing to score.

The screen gate refuses things that are not solution designs. This one refuses a
solution design that yielded no inventory — no components from classify AND none
from the diagram graph. Evaluate would otherwise score an empty set and return a
number, and a number produced from nothing is indistinguishable, on the results
page, from a number produced from evidence.

Where the emptiness came from decides how much corroboration it needs. An analyzed
diagram — drawio parsed deterministically, or an image the vision model reported on
— that yields zero components is a real finding, and gates on its own. Classify
returning zero over document text is ambiguous, so it additionally requires the text
to be thin enough for that answer to be plausible; see
`pipeline.MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY`.

The tests that matter here are the ones proving the gate does NOT fire:

* `test_twenty_components_do_not_trigger_the_gate` uses 20, the count classify
  actually returned on the real RMBL-Control-Tower SoW from its prose alone.
* `test_a_substantial_document_only_upload_is_not_rejected` is the conflict with
  `stages.classify` resolved: that docstring is explicit that a doubly-empty
  classify is a provider quality dip rather than an empty design, and must not
  discard an otherwise sound review.
* `test_diagram_components_alone_do_not_trigger_the_gate` covers the asymmetric
  case: classify came back empty but the diagram gave us an inventory.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import config
import llm
import rubric
from agent import pipeline

DEMO_TOKEN = "zero-component-gate-token"

# The real RMBL-Control-Tower SoW was 24,215 characters. Padding to exactly that
# makes the boundary case in this file the length that actually ran tonight rather
# than an arbitrary "long document".
RMBL_CHARACTERS = 24_215

# Prose that reads as a design — the screen gate must pass it — but which the stub
# classify returns nothing for. Mirrors the shape of the real failure: a document
# describing intent and outcomes, with the architecture living in a diagram that was
# never uploaded. 387 characters, deliberately under the floor.
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

# A structurally valid drawio file that parses to zero components — the diagram was
# analyzed deterministically and genuinely contains no architecture.
EMPTY_DRAWIO = b"""<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>
<mxCell id="1" parent="0"/>
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


def _long_sow(characters: int) -> bytes:
    """A document-only SoW of a given length, in prose that names no service.

    Padded with scope/governance sentences rather than lorem ipsum, so the screen
    gate still reads it as a solution document — a long string of filler would be
    refused a stage earlier and test nothing.
    """
    filler = (
        "The delivery team will agree acceptance criteria with the business owner "
        "before each phase begins, and record the outcome in the engagement log. "
    )
    body = SOW_WITHOUT_ARCHITECTURE + (filler.encode() * (characters // len(filler) + 1))
    return body[:characters]


def _submit(
    client: TestClient,
    *,
    diagram: bytes | None = None,
    document: bytes = SOW_WITHOUT_ARCHITECTURE,
) -> str:
    body = {}
    body["document_key"] = client.post(
        "/uploads",
        files={"file": ("sow.md", document, "text/markdown")},
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

def test_a_thin_document_with_an_empty_inventory_is_rejected(client, monkeypatch) -> None:
    """The document-only half of the gate, and note what makes it fire.

    SOW_WITHOUT_ARCHITECTURE is 387 characters — under
    `pipeline.MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY`. An empty classify over that
    little text is believable, so the refusal is safe. Over a real SoW it would not
    be, which is what `test_a_substantial_document_only_upload_is_not_rejected` pins.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    assert len(SOW_WITHOUT_ARCHITECTURE) < pipeline.MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY

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


def test_an_analyzed_diagram_with_no_components_rejects_whatever_the_text_says(
    client, monkeypatch
) -> None:
    """The diagram half keeps no character floor, deliberately.

    drawio parsing is deterministic and vision reports what it saw, so zero
    components from an analyzed diagram is a real finding rather than a possible
    miss. The document text alongside it does not soften that — here the text is
    RMBL-length and the review is still refused.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(
        client, diagram=EMPTY_DRAWIO, document=_long_sow(RMBL_CHARACTERS)
    )
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] == "rejected", status
    # Pinned to the classify stage specifically. `relevance.rejection_message` also
    # says "an architecture diagram", so a looser assertion here would pass just as
    # happily on a screen-gate refusal and prove nothing about this gate.
    rejected_stages = [s["name"] for s in status["stages"] if s["state"] == "rejected"]
    assert rejected_stages == ["classify"], rejected_stages
    assert "no architecture components could be identified" in status["rejection"]
    # And the text was well above the floor, so only the diagram branch can explain
    # the refusal.
    assert RMBL_CHARACTERS > pipeline.MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY


# --------------------------------------------------------------------------- #
# The gate does NOT fire — the half that protects real reviews
# --------------------------------------------------------------------------- #

def test_a_substantial_document_only_upload_is_not_rejected(client, monkeypatch) -> None:
    """The conflict this gate had with `stages.classify`, resolved.

    That docstring is explicit: a doubly-empty classify is evidence of a provider
    quality dip, not of an empty design, and discarding an otherwise sound review
    over it is the wrong trade — evaluate reads the normalized design text, not this
    inventory. A document-only upload has no diagram, so gating on "classify returned
    zero" alone would reject a real SoW on a bad provider day. Above the character
    floor the review continues, exactly as it did before the gate existed.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(client, document=_long_sow(RMBL_CHARACTERS))
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] != "rejected", status.get("rejection")
    assert status["rejection"] == ""
    # It did not merely survive the gate — the expensive stages actually ran.
    assert "evaluate" in " ".join(calls)
    classify = next(s for s in status["stages"] if s["name"] == "classify")
    assert classify["detail"] == (
        "0 components classified from the document text — the review continues "
        "from the design text"
    )


@pytest.mark.parametrize(
    ("characters", "rejects"),
    [
        (200, True),               # unambiguously thin
        (999, True),               # one character under the floor
        (1000, False),             # exactly at it — the floor is inclusive-continue
        (2361, False),             # the suite's smallest realistic SoW fixture
        (RMBL_CHARACTERS, False),  # the real RMBL length
    ],
)
def test_the_character_floor_decides_a_document_only_empty_classify(
    client, monkeypatch, characters, rejects
) -> None:
    """The floor's behaviour at its own boundary, including off-by-one.

    1,000 is not a guess: `ingestion/quality.py` puts a real page of a solution
    document at 1,500-3,000 characters, and the suite's own fixtures cluster at
    32-387 then jump to 2,361, so the threshold sits in an empty band.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(client, document=_long_sow(characters))
    status = client.get(f"/reviews/{review_id}/status").json()

    assert (status["state"] == "rejected") is rejects, (
        f"{characters} chars: state={status['state']!r} {status.get('rejection', '')}"
    )


def test_twenty_components_do_not_trigger_the_gate(client, monkeypatch) -> None:
    """20 is the real number.

    On RMBL-Control-Tower-SOW-v1.0 (Signed).pdf, with no diagram uploaded and so zero
    graph components, classify returned 20 components from the document's prose alone
    in 44.2s. That review must run: the gate's conditions are what let it, and a gate
    keyed on "no diagram" rather than "no inventory" would have killed it.
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
    """The floor on the inventory is "any", not "enough". A one-component design is a
    thin review, not an unreviewable one, and deciding otherwise is the rubric's
    job."""
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(count, calls))

    review_id = _submit(client)
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] != "rejected", status.get("rejection")


def test_diagram_components_alone_do_not_trigger_the_gate(client, monkeypatch) -> None:
    """Classify empty, graph not — a provider quality dip, not an empty design.

    `stages.classify` is explicit that a doubly-empty inventory must not discard an
    otherwise sound review, because evaluate reads the normalized design text rather
    than this inventory.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(0, calls))

    review_id = _submit(client, diagram=DRAWIO)
    status = client.get(f"/reviews/{review_id}/status").json()

    assert status["state"] != "rejected", status.get("rejection")
    assert "evaluate" in " ".join(calls)
