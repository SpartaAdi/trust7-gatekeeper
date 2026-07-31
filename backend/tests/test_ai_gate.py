"""The one-way AI-applicability gate: that it fires, that it stays one-way.

## The failure this covers

Design B of the ground-truth set is a payments API whose document states it "does not
utilize any foundation models, neural networks, or generative capabilities". Its human
labels mark eighteen TRUST-7 checks `not_applicable`. In one of three otherwise
identical harness runs, evaluate returned all eighteen as `pass` — 89.3 instead of 42.9
overall, 92.9 instead of 0.0 on TRUST-7.

There was no gate. Applicability was an unconstrained per-check model judgement: the
deterministic `AiDetection` record was computed before evaluate and never passed to it,
and the evaluate prompt said only "use `not_applicable` sparingly", which pushes the
wrong way. So there was nothing to regress — which is why this file is new rather than
amended.

## Why the one-way property gets its own tests

A gate that can force a check to `not_applicable` is one line away from a gate that
can force one back to evaluated, and the second is far more dangerous than the failure
it was built to fix: it would silence real AI-governance findings on designs that do
have AI. `test_the_gate_is_one_way_*` exist to make that mutation fail loudly, and were
checked against a bidirectional mutation of `ai_gate.apply` rather than assumed to
work.

Guard 4 — never overwrite an evidence-bearing verdict — is likewise pinned here rather
than left to the module docstring, because it is the guard that bounds what the gate is
allowed to fix, and it is the one most tempting to relax the next time a run scores
badly.
"""

from __future__ import annotations

import importlib
import pathlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

import rubric
from agent import ai_gate
from schema import AiDetection, AiSignal, Finding

# ­A design that says outright it has no AI — the Design B shape, shortened. Fenced as
# untrusted input by the pipeline; here it is only ever read by the detector.
NO_AI_SOW = (
    b"# Checkout payments API\n\n"
    b"Orders are stored in DynamoDB behind API Gateway. This system does not "
    b"utilize any foundation models, neural networks, or generative capabilities.\n"
)
DRAWIO = b"""<mxfile><diagram><mxGraphModel><root><mxCell id="0"/>
<mxCell id="api" value="API Gateway" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.api_gateway" vertex="1" parent="0"/>
<mxCell id="db" value="Orders DynamoDB" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.dynamodb" vertex="1" parent="0"/>
<mxCell id="e1" value="HTTPS" edge="1" parent="0" source="api" target="db"/>
</root></mxGraphModel></diagram></mxfile>"""

DEMO_TOKEN = "ai-gate-demo-token"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _detection(verdict: str) -> AiDetection:
    """An `AiDetection` whose COMPUTED verdict is `verdict`.

    `verdict` is a `@computed_field`, so it cannot be set — the signals have to be
    arranged to produce it. That is deliberate in the schema (a stored record can
    never contradict its own evidence) and it means these fixtures exercise the same
    derivation the pipeline does, not a shortcut past it.
    """
    bedrock = AiSignal(
        tier="named_service", signal="Amazon Bedrock",
        source="diagram component", excerpt="Bedrock Runtime",
    )
    denial = AiSignal(
        tier="denial", signal="does not utilize any foundation models",
        source="solution document",
        excerpt="This system does not utilize any foundation models.",
    )
    signals = {
        "present": [bedrock],
        "likely": [AiSignal(tier="implicit_function", signal="recommendation engine",
                            source="solution document",
                            excerpt="a recommendation engine ranks the catalogue")],
        "contradicted": [bedrock, denial],
        "denied": [denial],
        "absent": [],
    }[verdict]

    record = AiDetection(
        signals=signals,
        patterns_checked=80,
        components_seen=["API Gateway", "Orders DynamoDB"],
    )
    assert record.verdict == verdict, (
        f"fixture builds {record.verdict!r}, not {verdict!r} — the test below would "
        f"be asserting against the wrong verdict"
    )
    return record


def _not_run() -> AiDetection:
    """The stored-before-detection-existed case: no patterns ran at all."""
    record = AiDetection(patterns_checked=0)
    assert record.verdict == "not_run"
    return record


def _conditional_checks() -> list[Any]:
    return [c for c in rubric.all_checks() if c.ai_conditional]


def _finding(check: Any, status: str, evidence: str = "") -> Finding:
    return Finding(
        framework=check.framework,
        pillar_id=check.pillar_id,
        check_id=check.check_id,
        status=status,
        severity=check.severity,
        title=check.description[:60],
        evidence=evidence,
    )


def _all_checks_as(status: str, evidence: str = "") -> list[Finding]:
    """One finding per rubric check, every one at `status`.

    Both halves matter: the AI-conditional checks are what the gate may touch, and the
    other twenty-seven are the control group proving it touches nothing else.
    """
    return [_finding(c, status, evidence) for c in rubric.all_checks()]


# --------------------------------------------------------------------------- #
# The rubric declaration the gate reads
# --------------------------------------------------------------------------- #

def test_the_rubric_declares_exactly_the_eighteen_ai_conditional_checks() -> None:
    """A literal list, on purpose.

    The count alone would pass if a check were swapped for another, and which checks
    are gated is the whole substance of the change — eighteen of forty-five statuses
    can be set by code now, so the set belongs somewhere a reviewer reads.

    This is also the list that was drafted from the descriptions and then found to
    match the human labeller's eighteen not-applicables on Design B exactly.
    """
    assert {c.check_id for c in _conditional_checks()} == {
        "tf_explainability",
        "tf_fairness",
        "tf_hallucination_control",
        "tf_privacy",
        "rr_ai_threat_model",
        "rr_incident_response_ai",
        "rr_validation_before_prod",
        "ue_cost_per_unit",
        "ue_model_routing",
        "ue_caching_ai",
        "ss_provider_dependency",
        "ss_abstraction",
        "ta_human_in_loop",
        "ta_user_training",
        "sai_inference_efficiency",
        "gov_model_inventory",
        "gov_audit_trail",
        "gov_accountability",
    }


def test_no_waf_check_is_ai_conditional() -> None:
    """Encryption at rest and multi-AZ apply whether or not there is a model.

    Flagging a WAF check would let a `denied` verdict zero out part of the AWS score,
    which is a much bigger claim than this change is making.
    """
    assert not [c for c in _conditional_checks() if c.framework != "trust7"]


def test_data_residency_is_not_gated() -> None:
    """The deliberate exclusion, pinned so re-adding it has to be argued for.

    Residency applies to any design holding regulated data. It is also the one label
    the tester corrected from `not_applicable` to `fail` on the no-AI design — so
    gating it would make the tool disagree with the ground truth by construction.
    """
    ids = {c.check_id for c in _conditional_checks()}
    assert "ss_data_residency" not in ids
    # And the near-miss a keyword matcher got wrong in the other direction.
    assert "cost_provisioning_model" not in ids


# --------------------------------------------------------------------------- #
# It fires — on absent and on denied, and only on the conditional checks
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("verdict", sorted(ai_gate.GATING_VERDICTS))
def test_an_unevidenced_pass_on_a_gating_verdict_becomes_not_applicable(
    verdict: str,
) -> None:
    """The 46-point shape: every AI-conditional check returned `pass`."""
    findings = _all_checks_as("pass")

    overridden = ai_gate.apply(findings, _detection(verdict))

    conditional = {c.check_id for c in _conditional_checks()}
    assert set(overridden) == conditional
    by_id = {f.check_id: f for f in findings}
    assert all(by_id[cid].status == "not_applicable" for cid in conditional)


def test_checks_that_are_not_ai_conditional_are_left_exactly_as_evaluated() -> None:
    """The control group. Without this, "the gate fired" and "the gate flattened the
    review" look identical."""
    findings = _all_checks_as("pass", evidence="")
    others = [c.check_id for c in rubric.all_checks() if not c.ai_conditional]

    ai_gate.apply(findings, _detection("denied"))

    by_id = {f.check_id: f for f in findings}
    assert len(others) == 27, "expected 45 - 18; the control group must be non-empty"
    assert all(by_id[cid].status == "pass" for cid in others)
    assert all(by_id[cid].evidence == "" for cid in others)


def test_the_replacement_evidence_says_why_and_can_be_contested() -> None:
    """A `not_applicable` a reviewer cannot interrogate is worse than a wrong one: it
    reads as a fact. The detection rationale carries the component list, so the
    finding itself is enough to overrule the gate on sight."""
    findings = _all_checks_as("pass")

    ai_gate.apply(findings, _detection("denied"))

    evidence = next(
        f.evidence for f in findings if f.check_id == "gov_model_inventory"
    )
    assert "no AI/ML component was detected" in evidence
    assert "only applies to designs that have one" in evidence
    # The audit half, from AiDetection.rationale rather than restated here.
    assert "explicitly states it has none" in evidence
    assert "API Gateway" in evidence


def test_an_unevidenced_partial_is_gated_too() -> None:
    """`partial` on a check that structurally cannot apply is the same guess `pass`
    is, and it scores points. Half of nothing is still nothing."""
    findings = _all_checks_as("partial")

    overridden = ai_gate.apply(findings, _detection("absent"))

    assert set(overridden) == {c.check_id for c in _conditional_checks()}


def _design_b_shape(gated_status: str) -> list[Finding]:
    """The real run's shape: eighteen free `pass`es on top of a design that mostly fails.

    `_all_checks_as("pass")` cannot show this — with every check passing, dropping
    eighteen of them from the denominator leaves 100.0 either way. The eighteen only
    cost points when there is something for them to average against, which on Design B
    there was: it fails most of what does apply to it.
    """
    findings = []
    for check in rubric.all_checks():
        if check.ai_conditional:
            # Unevidenced, so the gate is licensed to act.
            findings.append(_finding(check, gated_status))
        else:
            findings.append(_finding(check, "fail", evidence="Not addressed."))
    return findings


def test_the_gate_moves_the_score_the_way_the_ground_truth_says_it_should() -> None:
    """The point of the whole change, in the one number a reviewer reads first.

    Not an assertion on a literal 42.9: the figures come from the rubric's own weights,
    and pinning them here would break on any rubric edit for no reason. What must hold
    is the direction and the mechanism — eighteen inapplicable checks stop being
    credited as satisfied, TRUST-7 collapses to what actually applies, and the AWS side
    does not move at all.
    """
    import scoring

    ungated = _design_b_shape("pass")
    gated = _design_b_shape("pass")
    ai_gate.apply(gated, _detection("denied"))

    before, before_frameworks = scoring.score(ungated)
    after, after_frameworks = scoring.score(gated)

    assert after < before, (
        f"{before} -> {after}: the inflated score survived the gate"
    )

    def framework(frameworks: list[Any], key: str) -> Any:
        return next(f for f in frameworks if f.framework == key)

    # TRUST-7 is where all eighteen live. Everything left in it fails, so it goes to
    # the floor rather than being carried by checks that never applied — which is the
    # 0.0-instead-of-92.9 half of the failure.
    assert framework(before_frameworks, "trust7").score > 0
    assert framework(after_frameworks, "trust7").score == 0.0

    # And the AWS framework does not move at all: 18 of 18 gated checks are TRUST-7.
    assert (
        framework(after_frameworks, "aws_waf").score
        == framework(before_frameworks, "aws_waf").score
    )


def test_a_wholly_inapplicable_pillar_is_excluded_rather_than_scored_zero() -> None:
    """The distinction the framework average turns on.

    A pillar whose every check is `not_applicable` has nothing to score. Averaging it
    in as 0.0 would punish a design for lacking AI, and averaging it in as 100 would
    credit it for the same thing; the rubric's rule is to leave it out. The gate
    creates far more wholly-N/A pillars than the model ever did, so this rule now
    carries much more weight than when it was written.
    """
    import scoring

    findings = _design_b_shape("pass")
    ai_gate.apply(findings, _detection("denied"))
    _, frameworks = scoring.score(findings)

    trust7 = next(f for f in frameworks if f.framework == "trust7")
    wholly_na = [p for p in trust7.pillars if p.checks_evaluated == 0]

    assert wholly_na, "the gate did not empty a single pillar; this proves nothing"
    # Excluded from the average: with every remaining check failing, TRUST-7 is 0.0
    # either way — so the observable statement is that the pillar reports nothing
    # evaluated rather than a score it cannot support.
    assert all(p.score == 0.0 and p.checks_total > 0 for p in wholly_na)


# --------------------------------------------------------------------------- #
# It does NOT fire — every other verdict
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("verdict", ["present", "likely", "contradicted"])
def test_a_non_gating_verdict_changes_nothing(verdict: str) -> None:
    """`present` and `contradicted` are obvious. `likely` is the interesting one: it
    fires on a suggestive phrase — a "personalisation service" that might be a rules
    engine — and a phrase is nowhere near enough to mark eighteen governance checks
    inapplicable."""
    findings = _all_checks_as("pass")

    overridden = ai_gate.apply(findings, _detection(verdict))

    assert overridden == []
    assert all(f.status == "pass" for f in findings)


def test_not_run_changes_nothing() -> None:
    """`not_run` is not `absent`: one says nobody looked, the other says we looked and
    found nothing. Gating on the first would turn a silence into a claim about the
    design that nothing in the system ever established."""
    findings = _all_checks_as("pass")

    assert ai_gate.apply(findings, _not_run()) == []
    assert all(f.status == "pass" for f in findings)


def test_every_verdict_is_covered_by_one_of_these_tests() -> None:
    """So a new verdict in the schema cannot land defaulting silently into either
    behaviour. Read off `AiDetection.verdict`'s own annotation, not a hand-kept list.
    """
    import typing

    annotation = typing.get_type_hints(AiDetection.verdict.fget)["return"]  # type: ignore[attr-defined]
    declared = set(typing.get_args(annotation))

    gating = set(ai_gate.GATING_VERDICTS)
    non_gating = {"present", "likely", "contradicted", "not_run"}

    assert declared == gating | non_gating, (
        f"verdicts with no gate decision: {declared - (gating | non_gating)}"
    )


# --------------------------------------------------------------------------- #
# Guard 4 — an evidence-bearing verdict is deferred to, and logged
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("status", ["pass", "fail", "partial"])
def test_a_verdict_with_evidence_behind_it_is_never_overwritten(status: str) -> None:
    """The line between a gate and an override. The model looked at the design and
    cited something; whether or not we agree, that is a judgement about the design and
    not the guess this gate exists to replace."""
    findings = _all_checks_as(status, evidence="The design states model outputs are "
                                               "reviewed by a human before release.")

    overridden = ai_gate.apply(findings, _detection("denied"))

    assert overridden == []
    assert all(f.status == status for f in findings)


def test_whitespace_only_evidence_does_not_count_as_evidence() -> None:
    """Otherwise a single space defeats the gate, and "  " is not a citation."""
    findings = _all_checks_as("pass", evidence="   \n ")

    overridden = ai_gate.apply(findings, _detection("denied"))

    assert set(overridden) == {c.check_id for c in _conditional_checks()}


def test_an_evidenced_pass_against_a_denial_is_logged_as_a_contradiction(
    caplog: Any,
) -> None:
    """The residual guard 4 leaves behind, made observable.

    "This design has no AI" and "its AI controls are satisfied" cannot both be true.
    The gate defers, because it is one-way — but silence here is exactly how the
    46-point run came to be undiagnosable after the fact, since the accuracy harness
    retains statuses and not evidence.
    """
    findings = _all_checks_as("pass", evidence="Model cards are maintained.")

    with caplog.at_level("WARNING"):
        ai_gate.apply(findings, _detection("denied"))

    assert "gov_model_inventory" in caplog.text
    assert "one-way" in caplog.text
    assert "verdict=denied" in caplog.text


def test_an_evidenced_fail_is_not_logged_as_a_contradiction(caplog: Any) -> None:
    """"No model governance is documented" is a coherent thing to say about a design
    with no model, and it costs points rather than awarding them. Logging it would
    bury the flattering case in noise from the harmless one."""
    findings = _all_checks_as("fail", evidence="No model governance is documented.")

    with caplog.at_level("WARNING"):
        ai_gate.apply(findings, _detection("denied"))

    assert "gov_model_inventory" not in caplog.text


def test_a_check_the_model_already_marked_not_applicable_is_not_counted() -> None:
    """Reporting it as an override would overstate what the gate did — and the count
    is what the progress line shows the user."""
    findings = _all_checks_as("not_applicable")

    assert ai_gate.apply(findings, _detection("absent")) == []
    assert all(f.status == "not_applicable" for f in findings)


def test_the_gate_is_idempotent() -> None:
    """It runs once per review today, but a re-review path that ran it twice must not
    produce a different second answer."""
    findings = _all_checks_as("pass")
    detection = _detection("denied")

    first = ai_gate.apply(findings, detection)
    second = ai_gate.apply(findings, detection)

    assert len(first) == 18
    assert second == [], "the second pass must find nothing left to do"


# --------------------------------------------------------------------------- #
# ONE-WAY — the property that must survive a mutation
#
# These are the tests that fail when `apply` is made bidirectional. Verified against
# a real mutation of ai_gate.apply, not assumed.
# --------------------------------------------------------------------------- #

def test_the_gate_is_one_way_it_cannot_force_a_check_to_be_evaluated() -> None:
    """The mirror-image failure, and the worse one.

    A gate that can turn `not_applicable` back into an evaluated status would silence
    real AI-governance findings on designs that DO have AI — the same eighteen checks,
    failing in the other direction, on the designs where they matter most.
    """
    findings = _all_checks_as("not_applicable")

    for verdict in ("present", "likely", "contradicted", "denied", "absent"):
        overridden = ai_gate.apply(findings, _detection(verdict))
        assert overridden == [], f"{verdict} moved a not_applicable finding"
        assert all(f.status == "not_applicable" for f in findings), (
            f"{verdict} forced a check to be evaluated — the gate is not one-way"
        )


def test_the_gate_is_one_way_present_never_reaches_for_a_finding_at_all() -> None:
    """Stronger than "the statuses are unchanged": on an AI-bearing design the gate
    must be a no-op it costs nothing to run, whatever the findings look like.

    A tripwire object rather than an equality check, so a mutation that reads a
    finding and happens to write back the same value is still caught.
    """
    class Tripwire(Finding):
        def __setattr__(self, name: str, value: Any) -> None:
            raise AssertionError(
                f"the gate wrote {name!r} on a design detection says has AI"
            )

    check = _conditional_checks()[0]
    tripwire = Tripwire(
        framework=check.framework, pillar_id=check.pillar_id,
        check_id=check.check_id, status="pass", severity=check.severity, title="t",
    )

    assert ai_gate.apply([tripwire], _detection("present")) == []


def test_the_one_way_property_holds_across_every_status_and_verdict_pair() -> None:
    """Exhaustive over the 4x6 grid, asserting the invariant rather than the cases.

    Two things must hold for every pair: a status only ever moves TO `not_applicable`,
    and it only moves at all on a gating verdict. Writing this as one property test
    rather than twenty-four examples is what makes it hard to satisfy with a special
    case.
    """
    statuses = ("pass", "fail", "partial", "not_applicable")
    verdicts = ("present", "likely", "contradicted", "denied", "absent", "not_run")

    for status in statuses:
        for verdict in verdicts:
            for evidence in ("", "The design says so."):
                findings = _all_checks_as(status, evidence)
                detection = (
                    _not_run() if verdict == "not_run" else _detection(verdict)
                )
                ai_gate.apply(findings, detection)

                for f in findings:
                    if f.status == status:
                        continue
                    where = f"{status}+{verdict}+evidence={bool(evidence)}"
                    assert f.status == "not_applicable", (
                        f"{where}: {f.check_id} moved to {f.status}"
                    )
                    assert verdict in ai_gate.GATING_VERDICTS, (
                        f"{where}: {f.check_id} moved on a non-gating verdict"
                    )


def test_scoring_cannot_reach_the_gate() -> None:
    """The narrowed version of the old "scoring never reads AI detection" rule.

    The arithmetic still reads statuses and nothing else. What changed is that one of
    those statuses can now be SET by this module, between evaluate and scoring — which
    is a real crossing of the old rule, not a technicality. Asserted from both sides;
    tests/test_ai_detection.py holds the other half.
    """
    import scoring

    source = pathlib.Path(scoring.__file__).read_text()
    assert "ai_gate" not in source
    assert "AiDetection" not in source
    assert "not_applicable" in source, (
        "the status the gate sets is what scoring reads; if scoring stopped reading "
        "it, the gate would have no effect and this file would be proving nothing"
    )


# --------------------------------------------------------------------------- #
# Through the real pipeline
#
# The coverage whose absence let the failure ship: every test above calls `apply`
# directly, and `apply` was correct on the day the 46 points were lost — because
# nothing called it.
# --------------------------------------------------------------------------- #

def _stub_complete_json(seen: list[str]):
    """Answers every stage, and returns `pass` with no evidence on all 45 checks.

    The unevidenced `pass` is the point: it is the shape of a model guessing, and the
    only shape the gate is licensed to overwrite.
    """

    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        seen.append(label)
        required = set(schema.get("required", []))

        if "verdict" in required:
            return {
                "verdict": "reviewable",
                "subject": "a payments API design",
                "reason": "It describes an API, a datastore and the flow between them.",
                "confidence": "high",
            }, {}

        if "design_summary" in required:
            return {
                "design_summary": "A payments API on AWS, with no AI/ML component.",
                "components": [
                    {"id": "api", "label": "API Gateway", "kind": "gateway",
                     "provider": "aws", "service": "api gateway", "attributes": []},
                    {"id": "db", "label": "Orders DynamoDB", "kind": "database",
                     "provider": "aws", "service": "dynamodb", "attributes": []},
                ],
                "data_flows": [],
                "observations": [],
                "absent": [],
            }, {}

        if "findings" in required:
            return {
                "findings": [
                    {
                        "check_id": check.check_id,
                        "status": "pass",
                        "severity": check.severity,
                        "severity_rationale": "default",
                        "title": check.description[:60],
                        "evidence": "",
                        "affected_components": [],
                        "confidence": "low",
                    }
                    for check in rubric.all_checks()
                    if check.framework in system[1]["text"]
                ]
            }, {}

        if "ranking" in required:
            return {"summary": "Nothing outstanding.", "ranking": []}, {}

        return {
            "executive_summary": (
                "A conventional payments API with no AI/ML component; the AI "
                "governance checks do not apply to it."
            ),
            "remediations": [],
        }, {}

    return fake


@pytest.fixture(scope="module")
def gated_review(tmp_path_factory) -> Any:
    """One real run through the routes, on a design that denies having AI."""
    data_dir = tmp_path_factory.mktemp("ai-gate-data")

    import config
    import llm
    import main
    import storage

    seen: list[str] = []
    patch = pytest.MonkeyPatch()
    patch.setattr(config, "DATA_DIR", data_dir)
    patch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    patch.setattr(llm, "complete_json", _stub_complete_json(seen))

    client = TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})
    diagram_key = client.post(
        "/uploads", files={"file": ("design.drawio", DRAWIO, "application/xml")}
    ).json()["key"]
    document_key = client.post(
        "/uploads", files={"file": ("sow.md", NO_AI_SOW, "text/markdown")}
    ).json()["key"]

    accepted = client.post(
        "/reviews",
        json={"document_key": document_key, "diagram_key": diagram_key,
              "title": "Checkout payments API"},
    )
    review_id = accepted.json()["review_id"]

    out = {
        "status": client.get(f"/reviews/{review_id}/status").json(),
        "result": client.get(f"/reviews/{review_id}").json(),
        "labels": seen,
    }
    yield out

    patch.undo()
    importlib.reload(storage)
    importlib.reload(main)


def test_the_pipeline_gates_the_ai_checks_on_a_design_that_denies_having_ai(
    gated_review,
) -> None:
    """Through the route, not through `apply`: this is what the previous round's
    investigation found missing, and it is the assertion that would have failed on the
    day the gate did not exist."""
    findings = {f["check_id"]: f for f in gated_review["result"]["findings"]}
    conditional = [c.check_id for c in _conditional_checks()]

    still_passing = [c for c in conditional if findings[c]["status"] != "not_applicable"]

    assert not still_passing, (
        f"the model returned `pass` on these and the gate did not fire: {still_passing}"
    )
    # Detection reached the gating verdict from the document's own sentence.
    assert gated_review["result"]["ai_detection"]["verdict"] == "denied"


def test_the_pipeline_leaves_the_other_checks_passing(gated_review) -> None:
    """The same control group, at the level that matters: a gate that marked all 45
    not applicable would pass the test above."""
    findings = {f["check_id"]: f for f in gated_review["result"]["findings"]}
    others = [c.check_id for c in rubric.all_checks() if not c.ai_conditional]

    assert all(findings[c]["status"] == "pass" for c in others)


def test_the_stored_pillars_report_nothing_evaluated_where_the_gate_emptied_them(
    gated_review,
) -> None:
    """What the heatmap draws from, on a review the real pipeline produced.

    The stub passes everything, so this fixture's TRUST-7 score is not the interesting
    number — `test_the_gate_moves_the_score_*` covers that against the Design B shape.
    What this asserts is the pillar bookkeeping the results page renders: a pillar whose
    every check the gate marked not-applicable must report `checks_evaluated == 0`,
    which is how the UI can say "not applicable to this design" instead of drawing a
    cell that looks like a score.
    """
    result = gated_review["result"]
    trust7 = next(f for f in result["frameworks"] if f["framework"] == "trust7")

    emptied = [p for p in trust7["pillars"] if p["checks_evaluated"] == 0]

    assert emptied, "no pillar came out wholly not-applicable; the gate barely fired"
    assert all(p["checks_total"] > 0 for p in emptied), (
        "the pillar still HAS checks — they were gated, not deleted, and the count is "
        "what makes that visible"
    )
    assert all(p["score"] == 0.0 for p in emptied), (
        "a wholly not-applicable pillar must not be credited with a score"
    )


def test_the_user_is_told_the_checks_were_gated_rather_than_passed(
    gated_review,
) -> None:
    """A silent status change is the thing this project keeps deciding against. The
    evaluate stage detail is where the run says it happened."""
    evaluate = next(
        s for s in gated_review["status"]["stages"] if s["name"] == "evaluate"
    )
    assert "not applicable" in evaluate["detail"]
    assert "no AI/ML component detected" in evaluate["detail"]


def test_every_finding_the_gate_set_carries_its_reasoning_in_the_stored_review(
    gated_review,
) -> None:
    """Auditability has to survive persistence and serialization, not just exist in
    memory — a judge reads the stored JSON."""
    findings = {f["check_id"]: f for f in gated_review["result"]["findings"]}

    for check in _conditional_checks():
        evidence = findings[check.check_id]["evidence"]
        assert "no AI/ML component was detected" in evidence, check.check_id
        assert "API Gateway" in evidence, check.check_id
