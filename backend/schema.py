"""The common schema every part of the pipeline speaks.

Both diagram input paths — draw.io XML parsed deterministically, and image
uploads parsed via Claude vision — converge on `DesignGraph`. Nothing
downstream can tell which path a design arrived through.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# Design representation (the one common schema)
# --------------------------------------------------------------------------- #


class DiagramSource(str, Enum):
    DRAWIO = "drawio"
    IMAGE = "image"
    DOCUMENT = "document"


class Component(BaseModel):
    """A single element of the design — a service, store, boundary, or actor."""

    id: str
    label: str
    kind: str = Field(
        default="unknown",
        description="Normalized component class, e.g. compute, storage, database, "
        "queue, gateway, identity, network_boundary, external_actor, ai_model.",
    )
    provider: str = Field(default="unknown", description="e.g. aws, azure, saas, on_prem.")
    service: str = Field(default="", description="Specific service name if identifiable.")
    attributes: dict[str, str] = Field(default_factory=dict)


class Connection(BaseModel):
    """A directed edge between two components."""

    source_id: str
    target_id: str
    label: str = ""
    protocol: str = ""


class DesignGraph(BaseModel):
    components: list[Component] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: DiagramSource = DiagramSource.DOCUMENT


# Cap on the optional free-text context field.
#
# It bounds two things at once: cost, since this rides in the prompt of both the
# classify and evaluate calls, and injection surface, since it is the one input a
# submitter can type directly rather than having to hide inside a document or a
# diagram label. 1000 characters is a paragraph or two — enough to say what a system
# does and who uses it, not enough to smuggle in a second document.
MAX_CONTEXT_CHARS = 1000


class NormalizedDesign(BaseModel):
    """Everything the agent pipeline needs, from whichever inputs were supplied."""

    review_id: str
    title: str = ""
    document_text: str = ""
    graph: DesignGraph = Field(default_factory=DesignGraph)

    # Optional free text the submitter typed, offered only when there is no SoW to
    # carry the same information. UNTRUSTED, exactly like document_text and diagram
    # labels: it reaches the prompt inside the `untrusted.wrap()` fence that both
    # call sites of `as_prompt_context` already apply.
    context: str = ""

    @field_validator("context")
    @classmethod
    def _cap_context(cls, value: str) -> str:
        """Truncate silently at the cap.

        On the model rather than at the route, so every entry point inherits it —
        the route, `normalize.ingest`, and a test constructing this directly. A
        route-level check would leave the other two unbounded.
        """
        return value.strip()[:MAX_CONTEXT_CHARS]

    def as_prompt_context(self) -> str:
        lines: list[str] = []
        if self.title:
            lines.append(f"# Design: {self.title}")
        if self.graph.components:
            lines.append("\n## Components (from the architecture diagram)")
            for c in self.graph.components:
                bits = [f"- {c.label} [id={c.id}]", f"kind={c.kind}", f"provider={c.provider}"]
                if c.service:
                    bits.append(f"service={c.service}")
                for k, v in sorted(c.attributes.items()):
                    bits.append(f"{k}={v}")
                lines.append("  ".join(bits))
        if self.graph.connections:
            lines.append("\n## Data flows")
            for e in self.graph.connections:
                arrow = f"- {e.source_id} -> {e.target_id}"
                if e.label:
                    arrow += f" ({e.label})"
                if e.protocol:
                    arrow += f" via {e.protocol}"
                lines.append(arrow)
        if self.graph.notes:
            lines.append("\n## Diagram notes")
            lines.extend(f"- {n}" for n in self.graph.notes)
        if self.document_text:
            lines.append("\n## Solution document / SoW")
            lines.append(self.document_text)
        # Appended ONLY when non-empty, which is what makes an upload without context
        # byte-identical to before this field existed. tests/test_context_field.py
        # asserts that equivalence rather than trusting it.
        if self.context:
            lines.append("\n## Submitter-supplied context (purpose and use case)")
            lines.append(self.context)
        return "\n".join(lines) if lines else "(no design content supplied)"


# --------------------------------------------------------------------------- #
# Review output
# --------------------------------------------------------------------------- #

Severity = Literal["high", "medium", "low"]
CheckStatus = Literal["pass", "partial", "fail", "not_applicable"]

# How sure the model is that its own observation is right, given what it was shown.
# DISPLAY ONLY. See the field note on `Finding.confidence` — this must never reach
# `scoring.py`, and `tests/test_scoring.py` asserts that it does not.
Confidence = Literal["high", "medium", "low"]


class Finding(BaseModel):
    """One rubric check evaluated against the design."""

    framework: str
    pillar_id: str
    check_id: str
    status: CheckStatus
    severity: Severity
    title: str
    evidence: str = Field(
        default="",
        description="What in the design supports this verdict, or what is absent.",
    )
    affected_components: list[str] = Field(default_factory=list)
    remediation: str = ""
    remediation_effort: Literal["low", "medium", "high", ""] = ""
    priority: int = 0

    # The model's confidence in its OWN observation, not a property of the design:
    # "low" means the input was ambiguous (a diagram with unlabelled edges), not
    # that the gap is minor. Severity already carries how much the gap matters.
    #
    # DISPLAY ONLY, and deliberately so. Weighting a score by the model's
    # self-reported certainty would make the arithmetic non-deterministic in the
    # one place the tool has to be defensible: two runs over an identical design
    # could then produce different scores, and a reviewer could not reproduce a
    # number from the rubric. `scoring.py` does not read this field, and a test
    # asserts that perturbing it leaves every score byte-identical.
    #
    # Defaults to "" rather than "high" — an older stored review has no confidence
    # recorded, and inventing "high" for it would be a claim the model never made.
    confidence: Confidence | Literal[""] = ""


class PillarScore(BaseModel):
    framework: str
    pillar_id: str
    pillar_name: str
    score: float
    checks_total: int
    checks_evaluated: int
    checks_passed: int


class FrameworkScore(BaseModel):
    framework: str
    framework_name: str
    score: float
    pillars: list[PillarScore] = Field(default_factory=list)


class PillarDelta(BaseModel):
    framework: str
    pillar_id: str
    pillar_name: str
    previous_score: float
    current_score: float
    change: float


class ScoreDelta(BaseModel):
    """Comparison against a prior review of the same design."""

    previous_review_id: str
    previous_overall_score: float
    current_overall_score: float
    change: float
    pillars: list[PillarDelta] = Field(default_factory=list)
    resolved_checks: list[str] = Field(default_factory=list)
    new_checks: list[str] = Field(default_factory=list)
    unchanged_failures: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    review_id: str
    created_at: str
    title: str = ""
    overall_score: float = 0.0
    frameworks: list[FrameworkScore] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    components: list[Component] = Field(default_factory=list)
    summary: str = ""
    executive_summary: str = Field(
        default="",
        description="Deploy-readiness summary written by the remediate stage.",
    )
    diagram_key: str = Field(
        default="",
        description="Upload key of the architecture diagram, retained so the PDF "
        "export can embed it in its appendix. Empty for older reviews.",
    )
    delta: ScoreDelta | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Progress tracking (polled by the UI)
# --------------------------------------------------------------------------- #

STAGES: tuple[str, ...] = (
    "ingest",
    "normalize",
    "classify",
    "evaluate",
    "prioritize",
    "remediate",
)

# `cancelled` marks the stage a deliberate stop interrupted. Distinct from `error`
# because nothing went wrong, and distinct from `done` because it did not finish.
StageState = Literal["pending", "running", "done", "error", "cancelled"]


class StageProgress(BaseModel):
    name: str
    state: StageState = "pending"
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""


class ReviewStatus(BaseModel):
    review_id: str
    # `cancelled` is terminal like `complete` and `error`, and is NOT a kind of
    # success: no ReviewResult is stored for a cancelled review, so the result
    # route has nothing to serve and the history list never shows it.
    state: Literal["queued", "running", "complete", "error", "cancelled"] = "queued"
    stages: list[StageProgress] = Field(default_factory=list)
    error: str = ""
    updated_at: str = ""

    @classmethod
    def initial(cls, review_id: str) -> "ReviewStatus":
        return cls(
            review_id=review_id,
            state="queued",
            stages=[StageProgress(name=name) for name in STAGES],
        )
