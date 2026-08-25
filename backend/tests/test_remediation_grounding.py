"""Remediation guidance is grounded in the design source, or it is not stored.

Until this existed, `finding.remediation` had NO grounding of any kind. The only
quote check anywhere was `_use_case_notes`, which verifies against the submitter's
typed context box — a field most uploads leave empty — and never against the
document or the diagram. `remediate` did not even receive `document_text`: it saw
`_render_classification`, which is the classify stage's own restatement of the
design. So the most actionable text the tool produces, the part a delivery team is
meant to act on, was the least verified thing in the review.

Two properties matter more than the filter itself:

* **What counts as source.** Only what a person wrote — document prose, diagram
  labels and notes, the context box. Never the model's restatement. Checking a
  generated claim against another generated claim produces a green tick backed by
  nothing, which is worse than no check, and `test_the_models_own_restatement_is_
  not_quotable` is the test that says so.
* **What happens on failure.** An ungrounded remediation is not kept, not repaired,
  and not fabricated into something that passes. It rejoins the existing shortfall
  set and takes the same single bounded retry a genuinely missing entry takes; if
  the retry is ungrounded too it ends as an honest blank.
"""

from __future__ import annotations

import logging
from typing import Any

import llm
from agent import stages
from schema import Component, Connection, DesignGraph, Finding, NormalizedDesign

DOCUMENT = (
    "The claims portal runs on Amazon ECS behind an Application Load Balancer. "
    "Claim documents are archived to Amazon S3 after ninety days."
)


def _design(**overrides: Any) -> NormalizedDesign:
    base: dict[str, Any] = {
        "review_id": "r1",
        "title": "Claims portal",
        "document_text": DOCUMENT,
        "graph": DesignGraph(
            components=[
                Component(id="c0", label="Claims RDS", kind="database",
                          provider="aws", service="Amazon RDS",
                          attributes={"encryption": "not stated"}),
            ],
            connections=[Connection(source_id="c0", target_id="c0", label="TLS 1.2")],
            notes=["Single AZ for now"],
        ),
    }
    base.update(overrides)
    return NormalizedDesign(**base)


def _finding(check_id: str = "c0") -> Finding:
    return Finding(
        framework="aws_waf", pillar_id="security", check_id=check_id,
        status="fail", severity="high", title=f"Gap {check_id}",
        evidence="Not stated in the design.",
    )


def _stub(monkeypatch, first: list[dict], retry: list[dict] | None = None,
          sent: list[str] | None = None):
    def fake(**kwargs: Any):
        if sent is not None:
            sent.append(kwargs["content"][0]["text"])
        if kwargs["label"] == "remediate-missing":
            return {"remediations": retry or []}, {}
        return {
            "executive_summary": "S.",
            "remediations": first,
            "use_case_notes": [],
        }, {}

    monkeypatch.setattr(llm, "complete_json", fake)


def _entry(quote: str, check_id: str = "c0") -> dict:
    return {
        "check_id": check_id,
        "remediation": "Enable encryption at rest with a customer-managed key.",
        "effort": "low",
        "grounded_in": quote,
    }


# --------------------------------------------------------------------------- #
# What counts as the source
# --------------------------------------------------------------------------- #

def test_a_quote_from_the_document_is_grounded(monkeypatch) -> None:
    _stub(monkeypatch, [_entry("Claim documents are archived to Amazon S3")])

    text, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], {}, "sb", design=_design()
    )

    assert text["c0"].startswith("Enable encryption")
    assert quotes["c0"] == "Claim documents are archived to Amazon S3"


def test_a_diagram_component_label_is_grounded(monkeypatch) -> None:
    """A remediation quoting a labelled component counts, not only document prose.

    A diagram-only upload has no document at all, so a check that accepted document
    text alone would fail every remediation on exactly the submissions this tool was
    built for.
    """
    _stub(monkeypatch, [_entry("Claims RDS")])

    text, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], {}, "sb", design=_design(document_text="")
    )

    assert text["c0"]
    assert quotes["c0"] == "Claims RDS"


def test_a_diagram_note_a_service_and_a_connection_label_are_all_grounded(
    monkeypatch,
) -> None:
    for quote in ("Single AZ for now", "Amazon RDS", "TLS 1.2", "not stated"):
        _stub(monkeypatch, [_entry(quote)])

        _t, _e, _s, _n, _u, _g, quotes = stages.remediate(
            [_finding()], {}, "sb", design=_design()
        )

        assert quotes.get("c0") == quote, f"{quote!r} should count as design source"


def test_the_models_own_restatement_is_not_quotable(monkeypatch) -> None:
    """The load-bearing exclusion.

    `_render_classification` is the classify stage's prose ABOUT the design. If a
    quote taken from there passed this check, the tick beside a remediation would
    mean "the model agreed with itself" while looking exactly like "verified against
    what the submitter wrote". That is worse than running no check at all.
    """
    classification = {
        "design_summary": "A resilient multi-region claims platform with defence in depth.",
        "components": [{"id": "c0", "label": "Claims RDS", "kind": "database",
                        "provider": "aws", "service": "Amazon RDS", "attributes": []}],
        "observations": ["The design appears to follow a hub-and-spoke topology."],
    }
    _stub(monkeypatch, [_entry("a resilient multi-region claims platform")])

    text, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], classification, "sb", design=_design()
    )

    assert quotes == {}, "the model's own summary must not count as grounding"
    assert text.get("c0", "") == ""


def test_structural_scaffolding_is_not_quotable(monkeypatch) -> None:
    """`kind=database`, `provider=aws` and `[id=c0]` are OUR rendering, not the
    design. A model quoting one would pass a check it should not."""
    for quote in ("kind=database", "provider=aws", "[id=c0]"):
        _stub(monkeypatch, [_entry(quote)])

        _t, _e, _s, _n, _u, _g, quotes = stages.remediate(
            [_finding()], {}, "sb", design=_design()
        )

        assert quotes == {}, f"{quote!r} is scaffolding and must not ground anything"


def test_the_submitted_context_counts_as_source(monkeypatch) -> None:
    """The context box is prose a person typed, so it is source like any other."""
    _stub(monkeypatch, [_entry("must remain in the UK")])

    _t, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], {}, "sb",
        context="All customer data must remain in the UK.",
        design=_design(context="All customer data must remain in the UK."),
    )

    assert quotes.get("c0") == "must remain in the UK"


def test_matching_survives_reformatting_of_the_quote(monkeypatch) -> None:
    """Same tolerance `_use_case_notes` has, and for the same reason: a model
    re-typing a phrase reliably changes case and spacing and reliably keeps the
    words."""
    _stub(monkeypatch, [_entry("CLAIM   documents are  ARCHIVED to Amazon S3")])

    _t, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], {}, "sb", design=_design()
    )

    assert quotes.get("c0")


# --------------------------------------------------------------------------- #
# What happens when it fails
# --------------------------------------------------------------------------- #

def test_an_ungrounded_remediation_takes_the_existing_retry(monkeypatch, caplog) -> None:
    """Reuses the shortfall path rather than adding a second retry mechanism.

    The first answer's quote is fabricated, so the entry is removed and its check_id
    rejoins `wanted - set(text)` — the same set a genuinely absent entry lands in.
    The retry is grounded, so it is kept.
    """
    labels: list[str] = []

    def fake(**kwargs: Any):
        labels.append(kwargs["label"])
        if kwargs["label"] == "remediate-missing":
            return {"remediations": [_entry("Claims RDS")]}, {}
        return {"executive_summary": "S.",
                "remediations": [_entry("a phrase nobody ever wrote")],
                "use_case_notes": []}, {}

    monkeypatch.setattr(llm, "complete_json", fake)

    with caplog.at_level(logging.INFO):
        text, _e, _s, _n, _u, _g, quotes = stages.remediate(
            [_finding()], {}, "sb", design=_design()
        )

    assert labels == ["remediate", "remediate-missing"], labels
    assert text["c0"]
    assert quotes["c0"] == "Claims RDS"
    assert "not grounded in the design source" in caplog.text


def test_still_ungrounded_after_the_retry_is_left_blank_not_fabricated(
    monkeypatch, caplog
) -> None:
    """The honest blank. Never a fallback string, never the finding title, never the
    ungrounded text kept because it sounded fine."""
    _stub(
        monkeypatch,
        first=[_entry("a phrase nobody ever wrote")],
        retry=[_entry("another phrase nobody ever wrote")],
    )

    with caplog.at_level(logging.ERROR):
        text, _e, _s, _n, _u, _g, quotes = stages.remediate(
            [_finding()], {}, "sb", design=_design()
        )

    assert text.get("c0", "") == ""
    assert quotes == {}
    assert "still not grounded" in caplog.text


def test_the_retry_is_held_to_the_same_bar(monkeypatch) -> None:
    """A retry exempt from the check would be a way to launder an ungrounded answer
    into a stored one — the opposite of what the retry is for."""
    _stub(monkeypatch, first=[], retry=[_entry("a phrase nobody ever wrote")])

    text, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], {}, "sb", design=_design()
    )

    assert text.get("c0", "") == ""
    assert quotes == {}


def test_an_empty_quote_fails_like_a_wrong_one(monkeypatch) -> None:
    _stub(monkeypatch, first=[_entry("")], retry=[])

    text, _e, _s, _n, _u, _g, quotes = stages.remediate(
        [_finding()], {}, "sb", design=_design()
    )

    assert text.get("c0", "") == ""
    assert quotes == {}


def test_with_no_design_the_check_is_skipped_rather_than_failing_everything(
    monkeypatch, caplog
) -> None:
    """"Could not check" is not "checked and found nothing".

    Running the filter against an empty haystack would blank a whole review's
    guidance over a missing argument. Nothing is marked grounded either way, so the
    tick still means verified.
    """
    _stub(monkeypatch, [_entry("Claims RDS")])

    with caplog.at_level(logging.WARNING):
        text, _e, _s, _n, _u, _g, quotes = stages.remediate([_finding()], {}, "sb")

    assert text["c0"], "guidance must not be blanked because no design was passed"
    assert quotes == {}, "and nothing may be reported as verified"
    assert "could not be checked" in caplog.text


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #

def test_the_design_source_reaches_the_prompt_fenced(monkeypatch) -> None:
    """The model can only quote what it is shown, and every submitter-supplied
    string reaches it inside the same guard."""
    sent: list[str] = []
    _stub(monkeypatch, [_entry("Claims RDS")], sent=sent)

    stages.remediate([_finding()], {}, "sb", design=_design())

    body = sent[0]
    assert "Design source" in body
    assert "Claim documents are archived to Amazon S3" in body
    assert "Claims RDS" in body


def test_no_design_adds_no_source_block(monkeypatch) -> None:
    """A caller without a design produces the prompt as it was before this existed."""
    sent: list[str] = []
    _stub(monkeypatch, [_entry("Claims RDS")], sent=sent)

    stages.remediate([_finding()], {}, "sb")

    assert "Design source" not in sent[0]


def test_the_schema_requires_a_quote_on_every_entry() -> None:
    """Required, not optional: an optional field is one the model may simply omit,
    and an omitted quote would be indistinguishable from an unverifiable one."""
    item = stages._REMEDIATE_SCHEMA["properties"]["remediations"]["items"]

    assert "grounded_in" in item["required"]
    # The retry schema reuses the same items object, so it cannot drift.
    retry = stages._REMEDIATE_RETRY_SCHEMA["properties"]["remediations"]["items"]
    assert retry is item
