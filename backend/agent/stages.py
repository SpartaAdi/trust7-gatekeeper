"""The four agent pipeline stages.

    classify components -> evaluate against rubric -> prioritize findings
    -> generate remediation

Each stage is a single structured-output call. The rubric block is byte-identical
on every request and sits behind a cache breakpoint, so repeat reviews read it
from cache instead of paying for it again.
"""

from __future__ import annotations

from typing import Any

import llm
import rubric
from schema import Component, Finding, NormalizedDesign

# --------------------------------------------------------------------------- #
# Stage 1 — classify components
# --------------------------------------------------------------------------- #

_CLASSIFY_SYSTEM = """\
You are the component-classification stage of a solution design review.

You are given a design: an architecture graph (possibly empty) and/or the text of \
a solution document. Produce a single consolidated inventory of the design's \
components, merging what the diagram shows with what the document describes. When \
the two disagree, keep both and record the discrepancy in `observations`.

For each component set:
- `kind`: compute, storage, database, queue, messaging, streaming, analytics, \
gateway, load_balancer, cdn, dns, identity, security_control, observability, \
network_boundary, external_actor, ai_model, or unknown.
- `attributes`: only properties the design actually states — for example \
data_classification, encryption, availability, scaling, retention, residency. \
Omit an attribute rather than guessing its value.

Also record what the design does NOT establish, in `absent`. This is the most \
important part of your output: a governance review turns on what is missing, and \
later stages cannot tell silence apart from absence unless you name it here. \
Include only architecturally significant omissions.

Report the design as it is. Do not evaluate it, score it, or recommend changes."""

_CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "design_summary": {
            "type": "string",
            "description": "Two or three sentences: what this design does and how.",
        },
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "kind": {"type": "string"},
                    "provider": {"type": "string"},
                    "service": {"type": "string"},
                    "attributes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "label", "kind", "provider", "service", "attributes"],
                "additionalProperties": False,
            },
        },
        "data_flows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "crosses_trust_boundary": {"type": "boolean"},
                    "carries_sensitive_data": {"type": "boolean"},
                },
                "required": [
                    "description",
                    "crosses_trust_boundary",
                    "carries_sensitive_data",
                ],
                "additionalProperties": False,
            },
        },
        "observations": {"type": "array", "items": {"type": "string"}},
        "absent": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Architecturally significant things the design does not establish.",
        },
    },
    "required": [
        "design_summary",
        "components",
        "data_flows",
        "observations",
        "absent",
    ],
    "additionalProperties": False,
}


def classify(design: NormalizedDesign) -> tuple[dict[str, Any], dict[str, int]]:
    """Build a consolidated component inventory from the normalized design."""
    return llm.complete_json(
        system=[
            {
                "type": "text",
                "text": _CLASSIFY_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        content=[{"type": "text", "text": design.as_prompt_context()}],
        schema=_CLASSIFY_SCHEMA,
        effort="medium",
        max_tokens=16000,
    )


def classified_components(payload: dict[str, Any]) -> list[Component]:
    """Convert the classify stage's output into common-schema components."""
    out: list[Component] = []
    for raw in payload.get("components", []):
        out.append(
            Component(
                id=raw.get("id", ""),
                label=raw.get("label", ""),
                kind=raw.get("kind", "unknown"),
                provider=raw.get("provider", "unknown"),
                service=raw.get("service", ""),
                attributes={
                    a["name"]: a["value"]
                    for a in raw.get("attributes", [])
                    if a.get("name")
                },
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Stage 2 — evaluate against the rubric
# --------------------------------------------------------------------------- #

_EVALUATE_SYSTEM = """\
You are the evaluation stage of a solution design review. You assess a design \
against a fixed rubric and return one verdict per check.

Rules:
- Evaluate every check you are given, exactly once, using its given check_id.
- `status` is one of:
  - `pass` — the design demonstrably satisfies the check.
  - `partial` — the design partly addresses it, or states an intent without the \
mechanism.
  - `fail` — the design does not address it, or addresses it in a way that \
defeats the check's purpose.
  - `not_applicable` — the check cannot apply to this design's shape. Use this \
sparingly, and justify it in `evidence`. A check is not inapplicable merely \
because the design is silent on it.
- Silence is not a pass. If the design does not establish something the check \
requires, that is `fail` or `partial`, not `pass` and not `not_applicable`.
- `evidence` must cite what in the design drove the verdict — a component, a \
data flow, a quoted phrase from the document — or state plainly what is absent. \
Never speculate about what the design "probably" does.
- `severity` starts from the check's given default. Raise it when this design's \
specifics make the gap more dangerous (sensitive data, external exposure, \
irreversibility); lower it when they make it less so. Justify any change in \
`severity_rationale`.
- `affected_components` lists the component ids the finding concerns, where \
identifiable.

Judge the design that was submitted, on the rubric's terms. Do not reward or \
penalise a design for resembling any particular reference architecture."""

_EVALUATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["pass", "partial", "fail", "not_applicable"],
                    },
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "severity_rationale": {"type": "string"},
                    "title": {
                        "type": "string",
                        "description": "Short, specific statement of the finding.",
                    },
                    "evidence": {"type": "string"},
                    "affected_components": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "check_id",
                    "status",
                    "severity",
                    "severity_rationale",
                    "title",
                    "evidence",
                    "affected_components",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def evaluate(
    design: NormalizedDesign,
    classification: dict[str, Any],
    framework_key: str,
) -> tuple[list[Finding], dict[str, int]]:
    """Evaluate one framework's checks against the classified design."""
    framework = next((f for f in rubric.load() if f.key == framework_key), None)
    if framework is None:
        raise ValueError(f"Unknown framework {framework_key!r}")

    checks_block = _framework_block(framework)
    payload, usage = llm.complete_json(
        system=[
            {"type": "text", "text": _EVALUATE_SYSTEM},
            {
                "type": "text",
                "text": f"# Rubric\n\n{checks_block}",
                # Identical on every review — the cache breakpoint goes here.
                "cache_control": {"type": "ephemeral"},
            },
        ],
        content=[
            {
                "type": "text",
                "text": (
                    f"{design.as_prompt_context()}\n\n"
                    f"## Component inventory (from the classification stage)\n"
                    f"{_render_classification(classification)}\n\n"
                    f"Evaluate this design against every check in the rubric above."
                ),
            }
        ],
        schema=_EVALUATE_SCHEMA,
        # The stage that decides the score. Worth the tokens.
        effort="high",
        max_tokens=32000,
    )

    return _to_findings(payload.get("findings", []), framework_key), usage


def _framework_block(framework: rubric.Framework) -> str:
    lines = [f"## Framework: {framework.name} (key: {framework.key})"]
    for pillar in framework.pillars:
        lines.append(f"\n### Pillar: {pillar.name} (id: {pillar.pillar_id})")
        for check in pillar.checks:
            lines.append(
                f"- [{check.check_id}] (default severity: {check.severity}) "
                f"{check.description}"
            )
    return "\n".join(lines)


def _to_findings(raw_findings: list[dict[str, Any]], framework_key: str) -> list[Finding]:
    """Map raw verdicts onto the rubric, dropping anything unrecognized."""
    by_id = rubric.checks_by_id()
    seen: set[str] = set()
    findings: list[Finding] = []

    for raw in raw_findings:
        check_id = raw.get("check_id", "")
        check = by_id.get(check_id)
        if check is None or check.framework != framework_key or check_id in seen:
            # A hallucinated or duplicated check_id would corrupt the score;
            # the missing-check backfill below covers the gap it leaves.
            continue
        seen.add(check_id)
        findings.append(
            Finding(
                framework=check.framework,
                pillar_id=check.pillar_id,
                check_id=check_id,
                status=raw.get("status", "fail"),
                severity=raw.get("severity", check.severity),
                title=raw.get("title", "") or check.description,
                evidence=raw.get("evidence", ""),
                affected_components=raw.get("affected_components", []),
            )
        )

    # Any check the model skipped is recorded as unevaluated rather than silently
    # dropped — a missing check would otherwise inflate the pillar score.
    for check in rubric.all_checks():
        if check.framework == framework_key and check.check_id not in seen:
            findings.append(
                Finding(
                    framework=check.framework,
                    pillar_id=check.pillar_id,
                    check_id=check.check_id,
                    status="fail",
                    severity=check.severity,
                    title=check.description,
                    evidence="Not evaluated: the evaluation stage returned no verdict "
                    "for this check. Treated as unmet pending re-review.",
                )
            )
    return findings


def _render_classification(classification: dict[str, Any]) -> str:
    lines = [classification.get("design_summary", "")]
    for component in classification.get("components", []):
        attrs = ", ".join(
            f"{a['name']}={a['value']}" for a in component.get("attributes", [])
        )
        line = f"- {component.get('label')} [id={component.get('id')}] " \
               f"kind={component.get('kind')} provider={component.get('provider')}"
        if attrs:
            line += f" ({attrs})"
        lines.append(line)
    if flows := classification.get("data_flows", []):
        lines.append("\nData flows:")
        for flow in flows:
            markers = []
            if flow.get("crosses_trust_boundary"):
                markers.append("crosses trust boundary")
            if flow.get("carries_sensitive_data"):
                markers.append("carries sensitive data")
            suffix = f" [{'; '.join(markers)}]" if markers else ""
            lines.append(f"- {flow.get('description')}{suffix}")
    if observations := classification.get("observations", []):
        lines.append("\nObservations:")
        lines.extend(f"- {o}" for o in observations)
    if absent := classification.get("absent", []):
        lines.append("\nNot established by the design:")
        lines.extend(f"- {a}" for a in absent)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage 3 — prioritize findings
# --------------------------------------------------------------------------- #

_PRIORITIZE_SYSTEM = """\
You are the prioritization stage of a solution design review.

You are given the findings that did not pass. Rank them by the order in which a \
delivery team should address them. Rank 1 is most urgent.

Weigh severity together with this design's specifics: blast radius, whether the \
gap is irreversible once the system is live, whether it blocks a compliance \
obligation the design itself invokes, and whether fixing it later costs \
disproportionately more than fixing it now. Severity alone does not determine \
rank — a medium-severity gap that becomes very expensive to close after launch \
can outrank a high-severity one that can be closed any time.

Rank every finding you are given, once each, with consecutive ranks starting at 1.

Also write a `summary`: a short, plain assessment of where this design stands. \
Address the reviewer, not the design's author, and lead with the conclusion."""

_PRIORITIZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_id": {"type": "string"},
                    "rank": {"type": "integer"},
                    "rationale": {"type": "string"},
                },
                "required": ["check_id", "rank", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "ranking"],
    "additionalProperties": False,
}


def prioritize(
    findings: list[Finding], classification: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, int]]:
    """Rank the non-passing findings and write the review summary."""
    open_findings = [f for f in findings if f.status in ("fail", "partial")]
    if not open_findings:
        return {
            "summary": "No gaps found: the design satisfies every applicable check "
            "in both frameworks.",
            "ranking": [],
        }, {}

    return llm.complete_json(
        system=[
            {
                "type": "text",
                "text": _PRIORITIZE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        content=[
            {
                "type": "text",
                "text": (
                    f"## Design\n{classification.get('design_summary', '')}\n\n"
                    f"## Findings to rank\n{_render_findings(open_findings)}"
                ),
            }
        ],
        schema=_PRIORITIZE_SCHEMA,
        effort="medium",
        max_tokens=16000,
    )


# --------------------------------------------------------------------------- #
# Stage 4 — generate remediation
# --------------------------------------------------------------------------- #

_REMEDIATE_SYSTEM = """\
You are the remediation stage of a solution design review.

For each finding, write what the delivery team should change. Be concrete and \
specific to this design: name the component, the mechanism, and the outcome that \
closes the gap. A remediation a team can act on without asking a follow-up \
question is the goal.

Do not restate the finding, moralise, or pad with generic best-practice prose. \
Where a change has a material cost or operational tradeoff, say so in one clause \
— the reviewer needs to weigh it, not be sold on it.

`effort` estimates the work to implement: low (a configuration or design-document \
change), medium (a component or flow change), high (a structural change to the \
architecture)."""

_REMEDIATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "remediations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_id": {"type": "string"},
                    "remediation": {"type": "string"},
                    "effort": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["check_id", "remediation", "effort"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["remediations"],
    "additionalProperties": False,
}


def remediate(
    findings: list[Finding], classification: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Generate remediation guidance for the non-passing findings."""
    open_findings = [f for f in findings if f.status in ("fail", "partial")]
    if not open_findings:
        return {}, {}, {}

    payload, usage = llm.complete_json(
        system=[
            {
                "type": "text",
                "text": _REMEDIATE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        content=[
            {
                "type": "text",
                "text": (
                    f"## Design\n{_render_classification(classification)}\n\n"
                    f"## Findings needing remediation\n{_render_findings(open_findings)}"
                ),
            }
        ],
        schema=_REMEDIATE_SCHEMA,
        effort="medium",
        max_tokens=32000,
    )

    text: dict[str, str] = {}
    effort: dict[str, str] = {}
    for item in payload.get("remediations", []):
        check_id = item.get("check_id", "")
        if check_id:
            text[check_id] = item.get("remediation", "")
            effort[check_id] = item.get("effort", "")
    return text, effort, usage


def _render_findings(findings: list[Finding]) -> str:
    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"- [{finding.check_id}] ({finding.severity}, {finding.status}) "
            f"{finding.title}\n  evidence: {finding.evidence}"
        )
    return "\n".join(lines)
