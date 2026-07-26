"""The common schema every part of the pipeline speaks.

Both diagram input paths — draw.io XML parsed deterministically, and image
uploads parsed via Claude vision — converge on `DesignGraph`. Nothing
downstream can tell which path a design arrived through.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

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


class NormalizedDesign(BaseModel):
    """Everything the agent pipeline needs, from whichever inputs were supplied."""

    review_id: str
    title: str = ""
    document_text: str = ""
    graph: DesignGraph = Field(default_factory=DesignGraph)

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
        return "\n".join(lines) if lines else "(no design content supplied)"


# --------------------------------------------------------------------------- #
# Review output
# --------------------------------------------------------------------------- #

Severity = Literal["high", "medium", "low"]
CheckStatus = Literal["pass", "partial", "fail", "not_applicable"]


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

StageState = Literal["pending", "running", "done", "error"]


class StageProgress(BaseModel):
    name: str
    state: StageState = "pending"
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""


class ReviewStatus(BaseModel):
    review_id: str
    state: Literal["queued", "running", "complete", "error"] = "queued"
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
