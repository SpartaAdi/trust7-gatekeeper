"""Guards on AI/ML-component detection and its audit record.

## What is being pinned, and why it needed pinning

Nineteen of the forty-five rubric checks only mean anything if the design has an AI
or ML component in it. Whether they apply was decided entirely inside the evaluate
stage's `not_applicable` verdict — a per-check model judgement with no record of what
was looked for. A reviewer could not tell "there is genuinely no model here" apart
from "the model did not notice the model", and nineteen checks is a large fraction of
a score to rest on something unexaminable.

Measurement first, because it is the reason this module exists. The pre-existing
deterministic signal was `drawio.py`'s keyword map, which included a bare `"model"`
substring. Against 25 labels it scored:

    explicitly labelled AI  5/5 found
    implicit / unlabelled   0/15 found
    non-AI carrying "model" 5/5 WRONGLY marked ai_model

Both halves are pinned here: `test_the_bare_model_keyword_no_longer_*` covers the
false positives, and the implicit cases are covered by the parametrised detector
tests. Neither number should be allowed to regress silently.

## What is deliberately NOT tested here

That detection changes a verdict or a score — because it must not.
`test_detection_moves_no_verdict_and_no_score` asserts the opposite, at the level of
the stored record. A keyword detector is more *auditable* than the model, not more
*right*, and letting it overrule a judgement would trade a fallible reading for a
fallible regex while making the score unreproducible from the rubric.

## Known, accepted limits, pinned so they stay known

Two cases are wrong on purpose rather than by oversight, and are asserted as such so
that a change to either is a deliberate decision and not a surprise:

* "Data Model Registry" reads as `present`. "Model registry" in an architecture
  diagram overwhelmingly means an ML model registry; a registry of *data* models is
  rarer. Accepted false positive.
* A bare "Triage" or "Scoring" label with no system noun reads as `absent`. Matching
  the bare words would fire on "Ticket Triage Runbook" and "Scoreboard", which appear
  in ordinary operational designs with no AI anywhere.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent import ai_detection
from ingestion import drawio
from schema import (
    AiDetection,
    AiSignal,
    Component,
    DesignGraph,
    PillarScore,
    ReviewResult,
)


def graph(*labels: str) -> DesignGraph:
    """A DesignGraph with one component per label, through the REAL drawio parser.

    Through the parser rather than constructed directly, so `kind` is whatever
    `drawio.py` genuinely assigns — which is what the `classified_kind` tier reads,
    and the source of the false positives this module was built against.
    """
    cells = "".join(
        f'<mxCell id="n{i}" value="{label}" style="rounded=0;" vertex="1">'
        f"<mxGeometry/></mxCell>"
        for i, label in enumerate(labels)
    )
    xml = (
        f"<mxGraphModel><root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/>"
        f"{cells}</root></mxGraphModel>"
    )
    return drawio.parse(xml.encode())


# --------------------------------------------------------------------------- #
# The measured failure: a bare "model" substring
# --------------------------------------------------------------------------- #

# Every one of these was classified `ai_model` by the keyword map before this round.
# None of them is an AI component.
_NON_AI_WITH_MODEL = [
    "Domain Model",
    "Cost Model",
    "Threat Model Doc",
    "Provisioning Model",
    "Data model documentation",
]


@pytest.mark.parametrize("label", _NON_AI_WITH_MODEL)
def test_the_bare_model_keyword_no_longer_classifies_non_ai_as_ai_model(label: str) -> None:
    """`kind` is what reaches the evaluate prompt, so this was not cosmetic.

    A design whose only "model" was a cost model arrived at the AI-governance checks
    with a component inventory that said it had a model in it.
    """
    component = graph(label).components[0]
    assert component.kind != "ai_model", (
        f"{label!r} was classified {component.kind!r}; the bare 'model' keyword is back"
    )


@pytest.mark.parametrize("label", _NON_AI_WITH_MODEL)
def test_the_bare_model_keyword_no_longer_produces_a_false_ai_verdict(label: str) -> None:
    detection = ai_detection.detect(graph(label))
    assert detection.verdict == "absent", f"{label!r} -> {detection.verdict}"


@pytest.mark.parametrize(
    "label",
    ["Amazon Bedrock", "SageMaker Endpoint", "Claude 3.5 Sonnet", "OpenAI API", "LLM Gateway"],
)
def test_explicitly_labelled_ai_components_are_still_caught(label: str) -> None:
    """The 5/5 the old map got right must not be lost to the false-positive fix."""
    assert graph(label).components[0].kind == "ai_model"
    assert ai_detection.detect(graph(label)).verdict == "present"


# --------------------------------------------------------------------------- #
# The gap this module was built for: AI that is never labelled as AI
# --------------------------------------------------------------------------- #

# 0/15 of these were detected before. The expected verdict differs by tier on
# purpose: an explicit ML term proves a model, a business capability only suggests
# one, and the record should not overstate the difference away.
_IMPLICIT_AI = [
    ("Personalization Service", "likely"),
    ("Personalisation Service", "likely"),
    ("Recommendation Engine", "likely"),
    ("Recommender", "likely"),
    ("Inference Endpoint", "present"),
    ("Scoring Service", "likely"),
    ("Fraud Detection", "likely"),
    ("Churn Predictor", "likely"),
    ("Embeddings Store", "present"),
    ("Vector DB", "present"),
    ("Training Data Bucket", "present"),
    ("Feature Store", "present"),
    ("Semantic Search", "likely"),
    ("Chatbot", "likely"),
    ("Virtual Assistant", "likely"),
    ("Next Best Action", "likely"),
    ("Sentiment Analysis", "likely"),
    ("Anomaly Detection", "likely"),
    ("Propensity Scoring", "likely"),
    ("Content Moderation", "likely"),
    ("Dynamic Pricing", "likely"),
    ("Document classifier", "likely"),
]


@pytest.mark.parametrize(("label", "expected"), _IMPLICIT_AI)
def test_an_unlabelled_ai_component_is_detected(label: str, expected: str) -> None:
    """The step-3 case: a box that holds a model and never says so.

    This is what the keyword map missed entirely, and the reason a
    not-applicable on the AI checks could not previously be trusted.
    """
    detection = ai_detection.detect(graph(label))
    assert detection.verdict == expected, (
        f"{label!r} -> {detection.verdict} "
        f"(signals: {[s.signal for s in detection.signals]})"
    )


def test_a_personalisation_service_reads_as_likely_and_not_present() -> None:
    """The distinction is the honesty of the record, not a technicality.

    A personalisation service is almost always model-backed and genuinely might be
    hand-written rules. Reporting it as `present` would assert something the label
    does not establish; reporting it as `absent` is the bug this fixes.
    """
    detection = ai_detection.detect(graph("Personalization Service"))
    assert detection.verdict == "likely"
    assert [s.tier for s in detection.signals] == ["implicit_function"]
    assert "could be implemented as rules" in detection.rationale


@pytest.mark.parametrize(
    "label",
    ["Order Service", "Postgres", "SQS Queue", "ALB", "Ticket Triage Runbook",
     "Scoreboard", "Incident Response", "CloudFront", "Cognito"],
)
def test_an_ordinary_non_ai_component_stays_absent(label: str) -> None:
    """The control. A detector that fires on everything explains nothing."""
    assert ai_detection.detect(graph(label)).verdict == "absent"


# --------------------------------------------------------------------------- #
# Accepted limits, pinned so a change to either is deliberate
# --------------------------------------------------------------------------- #


def test_data_model_registry_is_an_accepted_false_positive() -> None:
    """"Model registry" in a diagram nearly always means an ML model registry.

    Asserted rather than fixed: narrowing the pattern to exclude this would also
    stop matching real ML model registries, which is the worse trade.
    """
    assert ai_detection.detect(graph("Data Model Registry")).verdict == "present"


@pytest.mark.parametrize("label", ["Triage", "Scoring", "Ranking"])
def test_a_bare_capability_word_with_no_system_noun_is_accepted_as_missed(
    label: str,
) -> None:
    """Deliberately not matched: the same words carry no AI sense in ops designs.

    "Ticket triage", "scoreboard" and "search ranking" are all ordinary. The
    patterns require a system noun — "triage service", "scoring engine" — and the
    cost of that is this miss.
    """
    assert ai_detection.detect(graph(label)).verdict == "absent"


@pytest.mark.parametrize(
    "label", ["Triage Service", "Scoring Engine", "Ranking Service"]
)
def test_the_same_word_with_a_system_noun_is_caught(label: str) -> None:
    assert ai_detection.detect(graph(label)).verdict == "likely"


# --------------------------------------------------------------------------- #
# Verdicts, and the difference between silence and a denial
# --------------------------------------------------------------------------- #


def test_a_design_that_states_it_has_no_ai_reads_as_denied_not_absent() -> None:
    """`denied` is WEAKER than `absent`, not stronger.

    `absent` is silence, which is a fact about the material. A denial is a claim
    inside submitted content, and the evaluate prompt already treats such claims as
    non-evidence. Collapsing the two would let a sentence in an untrusted document
    stand as the reason nineteen checks were skipped.
    """
    detection = ai_detection.detect(
        graph("API", "Postgres"),
        "No model, AI or machine-learning component is used anywhere in this system.",
    )
    assert detection.verdict == "denied"
    assert [s.tier for s in detection.signals] == ["denial"]
    assert "explicitly states it has none" in detection.rationale


def test_a_denial_alongside_real_evidence_reads_as_contradicted() -> None:
    """The most interesting outcome, and the one a reviewer most needs.

    Usually means a document and a diagram were written at different times.
    """
    detection = ai_detection.detect(
        graph("Amazon Bedrock", "API"),
        "This system does not use AI.",
    )
    assert detection.verdict == "contradicted"
    assert "may disagree" in detection.rationale


def test_a_denial_cannot_suppress_evidence() -> None:
    """A denial must never subtract. If it could, one sentence in an untrusted
    document would be enough to turn a design with Bedrock in it into `absent`."""
    with_denial = ai_detection.detect(graph("Amazon Bedrock"), "No AI is used.")
    without = ai_detection.detect(graph("Amazon Bedrock"))
    assert [s.signal for s in with_denial.positive_signals] == [
        s.signal for s in without.positive_signals
    ]


def test_nothing_at_all_reads_as_absent_and_says_what_was_searched() -> None:
    detection = ai_detection.detect(graph("API", "Postgres", "SQS Queue"))
    assert detection.verdict == "absent"
    assert detection.patterns_checked > 50
    # The "Components found: [...]" half — what makes the verdict contestable.
    assert detection.components_seen == ["API", "Postgres", "SQS Queue"]
    for label in ("API", "Postgres", "SQS Queue"):
        assert label in detection.rationale


def test_an_unrun_record_is_not_the_same_as_finding_nothing() -> None:
    """A review stored before this existed must not claim its design has no AI.

    `patterns_checked: 0` is the discriminator, and `verdict` reads it. Without this
    every historical review would silently acquire a finding nothing established.
    """
    never_ran = AiDetection()
    assert never_ran.verdict == "not_run"
    assert "did not run" in never_ran.rationale
    assert "not a finding that the design has no AI/ML" in never_ran.rationale

    ran_found_nothing = AiDetection(patterns_checked=96, components_seen=["API"])
    assert ran_found_nothing.verdict == "absent"
    assert "No AI/ML component detected" in ran_found_nothing.rationale


# --------------------------------------------------------------------------- #
# The record itself: auditable means source-attributed
# --------------------------------------------------------------------------- #


def test_every_signal_names_where_it_was_found_and_quotes_it() -> None:
    """"An AI signal was detected" is not auditable. This is what makes it so."""
    detection = ai_detection.detect(
        graph("Bedrock runtime"),
        "The service calls a hosted foundation model for classification.",
    )
    assert detection.signals
    for signal in detection.signals:
        assert signal.source, signal
        assert signal.excerpt, signal
        assert signal.signal, signal


def test_a_document_match_is_attributed_to_the_document_and_a_diagram_match_to_its_box() -> None:
    """Where a match came from is evidence in itself: a phrase in a diagram box is a
    component, the same phrase in prose might be describing a future phase."""
    detection = ai_detection.detect(
        graph("Recommendation Engine"),
        "A vector database stores embeddings for retrieval.",
    )
    sources = {s.signal: s.source for s in detection.signals}
    assert "recommendation engine" in sources
    assert "Recommendation Engine" in sources["recommendation engine"]
    assert "solution document" in sources["vector store"]


def test_the_excerpt_carries_surrounding_context_not_just_the_match() -> None:
    text = (
        "Batch jobs run nightly. The pipeline retrains the churn model weekly using "
        "training data drawn from the warehouse. Results feed the CRM."
    )
    detection = ai_detection.detect(None, text)
    excerpts = [s.excerpt for s in detection.signals]
    assert any("warehouse" in e or "pipeline" in e for e in excerpts), excerpts


def test_one_signal_repeated_many_times_is_not_reported_many_times() -> None:
    """"inference" in forty paragraphs is one fact. An unbounded record is one
    nobody reads, and a record nobody reads is the silent not-applicable again."""
    detection = ai_detection.detect(None, "inference. " * 200)
    inference = [s for s in detection.signals if s.signal == "inference"]
    assert 1 <= len(inference) <= 3, len(inference)


def test_a_component_already_classified_ai_model_is_its_own_tier() -> None:
    """It is not a text match — it is a conclusion the classify stage or the keyword
    map reached, and the record should say which rather than claim credit."""
    detection = ai_detection.detect(
        DesignGraph(
            components=[Component(id="c1", label="Opaque Box", kind="ai_model")]
        )
    )
    assert [s.tier for s in detection.signals] == ["classified_kind"]
    assert detection.verdict == "present"


def test_the_classify_stages_own_output_is_searched_too() -> None:
    """The vision path produces no draw.io XML, so on an image upload the classify
    payload is the only structured description of the design that exists."""
    detection = ai_detection.detect(
        None,
        "",
        {
            "design_summary": "A claims platform.",
            "components": [
                {"id": "c1", "label": "Triage service", "kind": "compute",
                 "service": "fargate", "provider": "aws"}
            ],
            "data_flows": [{"description": "Documents flow to a hosted LLM."}],
            "observations": [],
            "absent": ["No model versioning is described."],
        },
    )
    signals = {s.signal for s in detection.signals}
    assert "LLM" in signals
    assert "triage service" in signals
    assert detection.verdict == "present"
    assert detection.components_seen == ["Triage service"]


def test_the_absent_list_is_searched_because_it_carries_real_signal() -> None:
    """"No model governance is described" says a model EXISTS and is ungoverned.

    The classify prompt calls `absent` the most important part of its output, so
    skipping it would drop a signal from the field most likely to carry one.
    """
    detection = ai_detection.detect(
        None, "", {"components": [], "absent": ["No model registry is described."]}
    )
    assert detection.verdict == "present"
    assert any("absent" in s.source for s in detection.signals)


@pytest.mark.parametrize(
    "absence",
    [
        "No model registry is described.",
        "No model versioning is described.",
        "No AI governance policy is defined.",
        "No model monitoring or drift detection is described.",
        "No ML model owner is named.",
    ],
)
def test_a_missing_governance_statement_is_not_read_as_a_denial(absence: str) -> None:
    """Caught by these tests as a real bug, and it was the dangerous kind.

    "No model registry is described" says a model EXISTS and is ungoverned — the
    opposite of "this design has no AI". A bare `no ... model` denial pattern matched
    it, which turned a design with Bedrock in it into `contradicted`. The classify
    stage's `absent` list is written almost entirely in this phrasing, so the loose
    pattern misfired on the field most likely to carry real signal.
    """
    detection = ai_detection.detect(graph("Amazon Bedrock"), absence)
    assert detection.verdict == "present", [
        (s.tier, s.signal) for s in detection.signals
    ]
    assert not any(s.tier == "denial" for s in detection.signals)


@pytest.mark.parametrize(
    "denial",
    [
        "No model, AI or machine-learning component is used anywhere in this system.",
        "No AI is used.",
        "This design does not use machine learning.",
        "No ML component exists in this architecture.",
        "Machine learning is not in scope.",
        "The platform is built without AI.",
        "No models are used.",
    ],
)
def test_a_real_denial_is_still_recognised(denial: str) -> None:
    """The other side of the fix. Tightening the pattern must not lose the phrasings
    a real SoW actually uses — including the comma-list form."""
    detection = ai_detection.detect(graph("API", "Postgres"), denial)
    assert detection.verdict == "denied", [
        (s.tier, s.signal) for s in detection.signals
    ]


def test_a_mention_inside_a_denying_sentence_is_not_evidence() -> None:
    """The other real bug these tests caught, and the worse of the two.

    "This design does not use machine learning" contains the phrase "machine
    learning", so the denial pattern and the explicit-term pattern matched the same
    eight characters and the record came out `contradicted` — reporting a design as
    internally inconsistent for the sole reason that it stated its position clearly.
    Every unambiguous non-AI design would have been flagged.
    """
    detection = ai_detection.detect(
        graph("API"), "This design does not use machine learning."
    )
    assert detection.verdict == "denied"
    assert [s.tier for s in detection.signals] == ["denial"]


def test_evidence_OUTSIDE_the_denying_sentence_still_contradicts_it() -> None:
    """The boundary of the rule above. Scoping is per sentence, not per document —
    otherwise one denial anywhere would silence the whole design, which is exactly
    the hole a submitter would need to hide a model in."""
    detection = ai_detection.detect(
        graph("API"),
        "This design does not use machine learning. "
        "The scoring step calls a hosted foundation model.",
    )
    assert detection.verdict == "contradicted"
    signals = {s.signal for s in detection.signals}
    assert "foundation model" in signals
    assert "states AI/ML not used" in signals


def test_a_denial_in_prose_cannot_silence_a_component_in_the_diagram() -> None:
    """Different sources are never in one span. A document that says "no AI" while
    the diagram shows Bedrock is the canonical `contradicted` case."""
    detection = ai_detection.detect(graph("Amazon Bedrock"), "No AI is used.")
    assert detection.verdict == "contradicted"


@pytest.mark.parametrize(
    "denial",
    [
        # The exact sentence from Design B that this fix exists for.
        "It does not utilize any foundation models, neural networks, or generative "
        "capabilities.",
        "The service does not use any neural networks.",
        "This platform has no generative capabilities.",
        "No foundation model is involved.",
        "Built without any large language models.",
        "Deep learning is not in scope.",
        "The system does not leverage LLMs.",
    ],
)
def test_a_denial_naming_something_other_than_ai_or_ml_is_still_a_denial(
    denial: str,
) -> None:
    """Found on the tester's real Design B, and the worst shape of this bug.

    The denial nouns were only "ai", "ml" and "machine learning", and the verbs only
    use/using/include/contain. So "does not UTILIZE any FOUNDATION MODELS" matched
    nothing, formed no denied span, and `_denied_spans` could not suppress the
    positive matches inside it — "foundation model" and "neural network" were counted
    as EVIDENCE OF AI, read out of the sentence saying there is none.

    A design that stated its position as plainly as it could was the one the detector
    got wrong, and it reported `present` on a document titled "Traditional_No AI".
    """
    detection = ai_detection.detect(graph("Checkout API"), denial)
    assert detection.verdict == "denied", [
        (s.tier, s.signal) for s in detection.signals
    ]
    assert [s.tier for s in detection.signals] == ["denial"], (
        "the denied sentence must not also supply positive evidence"
    )


@pytest.mark.parametrize(
    "gap",
    [
        "No neural network monitoring is in place.",
        "No foundation model versioning is described.",
        "No LLM oversight process exists.",
        "No machine learning audit trail is kept.",
        "No AI governance policy is defined.",
    ],
)
def test_the_wider_noun_list_does_not_turn_a_governance_gap_into_a_denial(
    gap: str,
) -> None:
    """The risk the widening creates, guarded in the same commit.

    "No neural network monitoring" says a neural network EXISTS and is unmonitored.
    Reading it as "this design has no AI" would suppress every real signal in the
    sentence — the same trap a bare `no model` fell into, one noun further out.
    """
    detection = ai_detection.detect(graph("Bedrock summariser"), gap)
    assert not [s for s in detection.signals if s.tier == "denial"], gap
    assert detection.verdict == "present"


def test_signals_are_ordered_strongest_first() -> None:
    """So the record reads as an argument: what proves it, then what suggests it."""
    detection = ai_detection.detect(
        graph("Amazon Bedrock", "Recommendation Engine", "Vector DB"),
        "This design does not use ML.",
    )
    order = ["classified_kind", "named_service", "explicit_term",
             "implicit_function", "denial"]
    seen = [order.index(s.tier) for s in detection.signals]
    assert seen == sorted(seen), [s.tier for s in detection.signals]


def test_detection_never_calls_a_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Free and reproducible. If it cost a call it could not be re-run against a
    stored review, which is half of what makes it an audit trail."""
    import llm

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("ai_detection made an LLM call")

    monkeypatch.setattr(llm, "complete_json", explode)
    detection = ai_detection.detect(graph("Amazon Bedrock"), "Uses a foundation model.")
    assert detection.verdict == "present"


def test_the_same_design_always_produces_the_same_record() -> None:
    text = "A propensity model scores leads nightly from training data."
    first = ai_detection.detect(graph("Scoring Service"), text)
    second = ai_detection.detect(graph("Scoring Service"), text)
    assert first.model_dump_json() == second.model_dump_json()


def test_an_empty_design_does_not_crash_and_says_nothing_was_extracted() -> None:
    detection = ai_detection.detect(None, "", None)
    assert detection.verdict == "absent"
    assert detection.components_seen == []
    assert "no components were extracted" in detection.rationale


# --------------------------------------------------------------------------- #
# The disagreement flag — reported, never acted on
# --------------------------------------------------------------------------- #


def _pillar(evaluated: int) -> PillarScore:
    return PillarScore(
        framework="trust7",
        pillar_id="trust_foundations",
        pillar_name="Trust foundations",
        score=0.0,
        checks_total=4,
        checks_evaluated=evaluated,
        checks_passed=0,
    )


def test_a_skipped_pillar_on_a_design_with_ai_evidence_is_flagged() -> None:
    detection = ai_detection.detect(graph("Amazon Bedrock"))
    assert detection.disagrees_with_pillar(_pillar(0)) is True


def test_a_skipped_pillar_on_a_design_with_no_ai_evidence_is_not_flagged() -> None:
    """The normal, correct case. Flagging it would make the flag meaningless."""
    detection = ai_detection.detect(graph("API", "Postgres"))
    assert detection.disagrees_with_pillar(_pillar(0)) is False


def test_an_evaluated_pillar_is_never_flagged() -> None:
    """Silent in the other direction on purpose: the AI-dependent checks are spread
    across pillars that also hold non-AI ones, so "evaluated" does not imply the
    model thought there was AI here."""
    detection = ai_detection.detect(graph("API"))
    assert detection.disagrees_with_pillar(_pillar(4)) is False
    with_ai = ai_detection.detect(graph("Amazon Bedrock"))
    assert with_ai.disagrees_with_pillar(_pillar(4)) is False


def test_a_likely_verdict_also_raises_the_flag() -> None:
    """`likely` is exactly the case worth a human's eye — the unlabelled component
    the model was most likely to miss."""
    detection = ai_detection.detect(graph("Personalization Service"))
    assert detection.verdict == "likely"
    assert detection.disagrees_with_pillar(_pillar(0)) is True


def test_an_unrun_record_never_raises_the_flag() -> None:
    """Nobody looked, so there is no disagreement to report."""
    assert AiDetection().disagrees_with_pillar(_pillar(0)) is False


# --------------------------------------------------------------------------- #
# The record is audit-only
# --------------------------------------------------------------------------- #


def test_detection_moves_no_verdict_and_no_score() -> None:
    """The load-bearing constraint of this whole round.

    Two results identical but for their detection record must score identically.
    `scoring.py` reads statuses and the rubric; if it ever read this, a score would
    stop being reproducible from the rubric and a regex would be deciding checks.
    """
    import scoring

    findings = ReviewResult(review_id="r", created_at="t").findings
    with_ai = ReviewResult(
        review_id="r",
        created_at="t",
        findings=findings,
        ai_detection=ai_detection.detect(graph("Amazon Bedrock")),
    )
    without = ReviewResult(
        review_id="r", created_at="t", findings=findings, ai_detection=AiDetection()
    )
    assert scoring.score(with_ai.findings) == scoring.score(without.findings)

    source = (__import__("pathlib").Path(scoring.__file__)).read_text()
    assert "ai_detection" not in source
    assert "AiDetection" not in source


def test_the_record_survives_a_json_round_trip() -> None:
    """Reviews are stored as JSON on disk. The computed fields serialize, and
    re-reading must not reject them or lose the evidence."""
    detection = ai_detection.detect(
        graph("Amazon Bedrock", "Recommendation Engine"), "Uses training data."
    )
    restored = AiDetection.model_validate(json.loads(detection.model_dump_json()))
    assert restored.verdict == detection.verdict
    assert restored.rationale == detection.rationale
    assert len(restored.signals) == len(detection.signals)


def test_verdict_and_rationale_reach_the_wire() -> None:
    """The UI, the PDF and any external judge read these. Computed server-side so
    three surfaces cannot describe one record three ways."""
    payload = json.loads(ai_detection.detect(graph("Amazon Bedrock")).model_dump_json())
    assert payload["verdict"] == "present"
    assert "Bedrock" in payload["rationale"]
    # Would duplicate most of `signals` in every stored review for a one-line filter.
    assert "positive_signals" not in payload


def test_an_older_stored_review_loads_and_reports_not_run() -> None:
    older = ReviewResult.model_validate({"review_id": "old", "created_at": "t"})
    assert older.ai_detection.verdict == "not_run"


def test_a_stored_record_cannot_contradict_its_own_evidence() -> None:
    """`verdict` and `rationale` are computed, not stored, so a hand-edited or
    tampered `verdict` key cannot survive: the evidence decides."""
    tampered = AiDetection.model_validate(
        {
            "signals": [],
            "patterns_checked": 96,
            "components_seen": ["API"],
            "verdict": "present",
            "rationale": "AI/ML component detected. Evidence: Amazon Bedrock.",
        }
    )
    assert tampered.verdict == "absent"
    assert "Bedrock" not in tampered.rationale


# --------------------------------------------------------------------------- #
# Against the repository's real fixture designs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("claims-triage-ai", "present"),
        ("expense-portal", "denied"),
    ],
)
def test_the_real_ground_truth_fixtures_are_classified_correctly(
    stem: str, expected: str
) -> None:
    """Synthetic single-label probes prove the patterns; these prove the whole thing
    on designs written as designs, one AI-bearing and one not."""
    import pathlib

    # The synthetic stand-ins, moved out of the globbed directory when real
    # ground truth arrived. Still the right fixture here: each ships a diagram
    # AND a document, which is what makes the both-inputs assertion possible.
    root = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "fixtures/ground_truth/synthetic_stub"
    )
    design = drawio.parse((root / f"{stem}.drawio").read_bytes())
    text = (root / f"{stem}.sow.md").read_text()

    detection = ai_detection.detect(design, text)
    assert detection.verdict == expected, (
        f"{stem} -> {detection.verdict}: {[s.signal for s in detection.signals]}"
    )


def test_the_ai_bearing_fixture_names_its_evidence_across_both_inputs() -> None:
    """A record that cited only the diagram, or only the document, would be missing
    half the audit trail on a design that supplies both."""
    import pathlib

    # The synthetic stand-ins, moved out of the globbed directory when real
    # ground truth arrived. Still the right fixture here: each ships a diagram
    # AND a document, which is what makes the both-inputs assertion possible.
    root = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "fixtures/ground_truth/synthetic_stub"
    )
    detection = ai_detection.detect(
        drawio.parse((root / "claims-triage-ai.drawio").read_bytes()),
        (root / "claims-triage-ai.sow.md").read_text(),
    )
    sources = " ".join(s.source for s in detection.signals)
    assert "diagram component" in sources
    assert "solution document" in sources
    assert "SageMaker" in detection.rationale
