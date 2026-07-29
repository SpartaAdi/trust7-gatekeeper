"""Phase grouping for the "How to Improve" roadmap.

The Python half of a rule that also lives in ``frontend/src/views/roadmap.ts``. The
two runtimes cannot share code, so ``fixtures/roadmap_cases.json`` is asserted by
both test suites: a change here that is not mirrored there fails, rather than
producing a PDF that quietly disagrees with the screen.

Pure and deterministic. Reads only fields already on a stored finding — no LLM call,
no new schema field, no stage re-run.

THE RULE
--------
The primary signal is ``remediation_effort``, already written by the remediate
stage, whose own prompt defines the values as:

    low    — "a configuration or design-document change"
    medium — "a component or flow change"
    high   — "a structural change to the architecture"

Those definitions already are the three phases, so the rule uses them rather than
inferring effort from severity, which measures how much a gap matters and not how
much work it is to close.

    1. STRUCTURAL   if effort is "high", OR the fix touches more than one component.
    2. IMMEDIATE    else if effort is "low" AND severity is "high".
    3. SHORT-TERM   everything else.

The component-count override exists because a change spanning three components is a
structural change whatever it was scored — blast radius is the one effort signal in
the data that is not the model's opinion.

Where ``remediation_effort`` is absent (an older stored review, or a check the model
left out of its remediation list) the rule falls back to severity and component
count alone. Absent effort never silently becomes "low".

Pillar is deliberately NOT used: it names a topic, not an amount of work. See the
TypeScript twin for the worked example.
"""

from __future__ import annotations

from typing import Literal

from schema import Finding

Phase = Literal["immediate", "short_term", "structural"]

#: Presentation order, and the order the phases are worked in.
PHASE_ORDER: tuple[Phase, ...] = ("immediate", "short_term", "structural")

PHASE_LABEL: dict[Phase, str] = {
    "immediate": "Immediate",
    "short_term": "Short-term",
    "structural": "Structural",
}

#: What each phase means, in the reviewer's terms rather than the rule's.
PHASE_BLURB: dict[Phase, str] = {
    "immediate": "High-severity gaps closable by a configuration or document change.",
    "short_term": "A component or flow change — worth scheduling, not architecture work.",
    "structural": "Spans several components or needs a design change.",
}

#: Above one component, a fix is a structural change however it was scored.
STRUCTURAL_COMPONENT_COUNT = 2

_OPEN_STATUSES = frozenset({"fail", "partial"})


def phase_for(finding: Finding) -> Phase:
    spans_components = len(finding.affected_components) >= STRUCTURAL_COMPONENT_COUNT
    if finding.remediation_effort == "high" or spans_components:
        return "structural"
    if finding.remediation_effort == "low" and finding.severity == "high":
        return "immediate"
    if finding.remediation_effort == "" and finding.severity == "high":
        # No effort recorded. Severity and blast radius are all there is, and a
        # single-component high-severity gap is the shape that is usually cheap.
        return "immediate"
    return "short_term"


def group_by_phase(findings: list[Finding]) -> dict[Phase, list[Finding]]:
    """Every open finding, in exactly one phase.

    A partition, not a shortlist: no cap and no dedupe, because this is the whole
    plan. A roadmap that silently dropped the eleventh item would be worse than no
    roadmap.
    """
    grouped: dict[Phase, list[Finding]] = {phase: [] for phase in PHASE_ORDER}
    for finding in findings:
        if finding.status not in _OPEN_STATUSES:
            continue
        grouped[phase_for(finding)].append(finding)

    for phase in PHASE_ORDER:
        grouped[phase].sort(key=_order)
    return grouped


def _order(finding: Finding) -> tuple[int, str]:
    """Remediation order, unranked last.

    The ``check_id`` tie-break is what makes the output independent of input order:
    most findings in a real review share ``priority == 0``, and a sort keyed on
    priority alone would preserve whatever order the list happened to arrive in.
    """
    rank = finding.priority if finding.priority > 0 else 2**31
    return (rank, f"{finding.framework}:{finding.check_id}")
