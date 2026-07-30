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


# --------------------------------------------------------------------------- #
# Ingestion warnings
#
# A warning is NOT an error and NOT a rejection. It says: the review ran, and
# here is a reason to distrust how much of the design actually reached it.
#
# It exists because the failure it describes is silent. `documents.extract_text`
# raises when a PDF yields no text at all, but a PDF that yields a little text —
# a cover page in front of thirty scanned pages — succeeds, and every stage
# downstream then scores a design it mostly never saw. The score comes out
# looking exactly like a score for a design that was fully read, which is the
# worst possible presentation of it.
#
# So warnings are carried on the status while the review runs and on the result
# afterwards, and the results page leads with them. `code` is for tests and for
# the UI to key on; `message` is user-facing prose; `detail` carries the numbers
# behind the judgement so a reviewer can decide for themselves.
# --------------------------------------------------------------------------- #

WarningCode = Literal[
    # A diagram image produced a graph too sparse for the file it came from.
    "diagram_near_empty",
    # The vision model itself reported low confidence in what it transcribed.
    "vision_low_confidence",
    # draw.io XML held many shapes but few survived parsing into components.
    "drawio_mostly_unparsed",
    # A PDF produced very little text per page — partially scanned, most likely.
    "document_sparse_text",
    # The relevance gate was unsure rather than satisfied. See ingestion/relevance.py.
    "relevance_uncertain",
]


class IngestWarning(BaseModel):
    """One reason to distrust how completely the design was read."""

    code: WarningCode
    message: str = Field(description="User-facing prose. Rendered verbatim in the UI.")
    detail: str = Field(
        default="",
        description="The measurements behind the judgement, so a reviewer can weigh "
        "it rather than take it on trust.",
    )


# --------------------------------------------------------------------------- #
# Data fidelity
#
# THREE SEPARATE NUMBERS, deliberately never combined. Each measures a different
# thing against a different kind of reference, and each is trustworthy to a
# different degree:
#
#   * structural coverage is a REAL, deterministic ratio — the draw.io XML is the
#     ground truth and we can count both sides exactly;
#   * OCR coverage is an ESTIMATE against a second fallible reader, not against
#     truth. Nothing here knows what is "really" in an image;
#   * the grounding filter is a COUNT of what was removed, and says nothing
#     whatever about what survived.
#
# Averaging them would produce a single "accuracy %" that is arithmetic on three
# incompatible quantities, and would launder the estimate's uncertainty and the
# count's silence into a figure that looks measured. There is deliberately no
# composite field on `DataFidelity`, and `tests/test_data_fidelity.py` asserts
# none appears.
# --------------------------------------------------------------------------- #

# Below this, extraction lost enough of the design that a human should look.
# Applied to the structural ratio, which is exact, and to the OCR proxy, which is
# not — see `OcrCoverageProxy` for why the same threshold means less there.
COVERAGE_REVIEW_THRESHOLD = 95.0


class StructuralCoverage(BaseModel):
    """How much of a draw.io file's diagram survived into the DesignGraph.

    EXACT, not an estimate: the XML lists its own elements, so both sides of the
    ratio are counted rather than inferred. No model call.

    `dropped` itemises what did not survive and why. It is the difference between
    "82% — something is wrong" and "82% — six unlabelled decorative shapes were
    skipped, which is correct behaviour", and without it the percentage is not
    actionable.
    """

    parsed_elements: int = Field(description="Components + connections + notes.")
    total_elements: int = Field(
        description="Diagram elements in the XML: vertices and edges, including "
        "object/UserObject wrappers. Excludes draw.io's mandatory root and layer "
        "cells, which are container scaffolding and never diagram content."
    )
    percent: float
    dropped: list[str] = Field(
        default_factory=list,
        description="Why elements did not survive, most common first. Counted.",
    )


class OcrCoverageProxy(BaseModel):
    """An ESTIMATE of how much of an image's text reached the DesignGraph.

    A second, independent reader (Tesseract) transcribes the image, and this
    reports what fraction of the words it found also appear somewhere in the
    graph. That is a proxy and nothing more:

    * OCR is itself wrong, in both directions. It misses rotated and low-contrast
      text, and it invents words out of icons and hatching. A token it invented
      and the graph does not contain looks identical here to a label the vision
      model genuinely missed.
    * There is no ground truth for what is "really" in an image. This compares two
      fallible readers with each other, so a low number means they disagree — not
      which of them is right.
    * A diagram whose meaning is carried by shapes and arrows rather than words
      can score low while having been read correctly.

    So every surface that shows this must label it an estimate. `is_estimate` is
    always True and exists to make that non-optional in the UI rather than a
    convention someone can forget.
    """

    available: bool = Field(
        description="False when no OCR engine is installed. The metric is then "
        "absent rather than zero — see `unavailable_reason`."
    )
    unavailable_reason: str = ""
    is_estimate: bool = Field(
        default=True,
        description="Always True. A proxy against a second fallible reader, never "
        "a measurement against ground truth.",
    )
    ocr_tokens: int = 0
    matched_tokens: int = 0
    percent: float = 0.0
    sample_unmatched: list[str] = Field(
        default_factory=list,
        description="A few words OCR read that the graph does not contain. Either "
        "missed labels or OCR noise — this cannot tell which.",
    )


class GroundingFilter(BaseModel):
    """How many ungrounded claims the quote-verification filter removed this run.

    A COUNT of what was caught, and deliberately not a rate. `removed` is the
    number of use-case recommendations discarded because the phrase the model
    said it was relying on is not in the submitted context.

    It says nothing about what survived. A run with 3 removed and 2 kept does not
    mean the 2 are correct — it means their quotes were verifiable, which is a
    much weaker claim. Reporting this as "60% grounded" would invert that: it
    would read as a confidence figure for the output when it is only a tally of
    rejections, so `percent` is deliberately absent from this model.
    """

    checked: int = Field(description="Candidate recommendations the model returned.")
    removed: int = Field(description="Discarded: the grounding quote was not found.")
    incomplete: int = Field(
        default=0,
        description="Discarded for a missing field rather than a failed quote. A "
        "different failure, counted separately rather than folded into `removed`.",
    )
    removed_for: list[str] = Field(
        default_factory=list,
        description="The components the removed claims concerned, for the audit trail.",
    )


class DataFidelity(BaseModel):
    """The three fidelity numbers. Never blended — see the note above.

    Each is None when it does not apply: structural coverage only exists for a
    draw.io upload, the OCR proxy only for an image, and the grounding filter only
    when the submitter supplied context for notes to be grounded in.
    """

    structural: StructuralCoverage | None = None
    ocr_proxy: OcrCoverageProxy | None = None
    grounding: GroundingFilter | None = None

    def review_recommended(self) -> bool:
        """Whether a coverage figure is low enough that a human should look.

        Computed, not stored, so it cannot drift from the numbers it describes.
        The grounding count is NOT an input: removing an ungrounded claim is the
        filter working, not a reason to distrust the review.
        """
        if self.structural and self.structural.percent < COVERAGE_REVIEW_THRESHOLD:
            return True
        if (
            self.ocr_proxy
            and self.ocr_proxy.available
            and self.ocr_proxy.percent < COVERAGE_REVIEW_THRESHOLD
        ):
            return True
        return False


class NormalizedDesign(BaseModel):
    """Everything the agent pipeline needs, from whichever inputs were supplied."""

    review_id: str
    title: str = ""
    document_text: str = ""
    graph: DesignGraph = Field(default_factory=DesignGraph)

    # Raised during ingestion, carried through the pipeline, and stored on the
    # result. Never a reason to stop: a partially-read design is still worth
    # reviewing, it is just not worth presenting as if it were complete.
    warnings: list[IngestWarning] = Field(default_factory=list)

    # Measured during ingestion, when the raw bytes and the parsed graph are both
    # still in hand — the only point where either ratio can be computed. The
    # grounding count is filled in later, by the remediate stage.
    fidelity: DataFidelity = Field(default_factory=DataFidelity)

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


class UseCaseNote(BaseModel):
    """A component-level trade-off, grounded in what the submitter actually said.

    `grounded_in` is the anti-fabrication lever and the reason this model exists
    rather than a plain string. The model must quote the phrase from the
    submitted context that the recommendation rests on; a note that cannot point
    at one is not supposed to be written at all, and a note whose quote is not
    in the context is discarded before it is stored.
    """

    component: str = Field(description="The component or choice being weighed.")
    recommendation: str = Field(
        description="The trade-off, in the submitter's terms. Names both options."
    )
    grounded_in: str = Field(
        description="The phrase from the submitted context this rests on, verbatim."
    )


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
    # The submitter's own purpose/use-case text, retained so the results page can
    # tell whether any was given and show recommendations grounded in it.
    #
    # UNTRUSTED, and treated exactly as it is everywhere else. It reaches a prompt
    # only through `NormalizedDesign.as_prompt_context()`, which fences it inside
    # `untrusted.wrap()`; storing a copy here adds no new path to the model. The
    # same validator caps it, so the stored copy cannot exceed what was evaluated.
    #
    # Defaults to "" so every review written before this field existed still
    # loads, and so an upload with no context is byte-identical to before.
    context: str = ""

    # Written only when the submitter supplied context AND the model could ground
    # a trade-off in something it actually states. Empty is the normal case and
    # is not a failure — see `_REMEDIATE_SYSTEM`.
    use_case_notes: list[UseCaseNote] = Field(default_factory=list)

    # Reasons to distrust how completely the design was read. Empty is the normal
    # case. Stored on the result and not only on the status, because the status is
    # transient — a reviewer opening a stored review a day later must still see that
    # its diagram was barely legible. Defaults to empty so older reviews load.
    warnings: list[IngestWarning] = Field(default_factory=list)

    # The three fidelity numbers. Defaults to an empty DataFidelity so reviews
    # written before this existed still load, with all three fields None — which
    # reads correctly as "not measured" rather than as zero coverage.
    fidelity: DataFidelity = Field(default_factory=DataFidelity)
    delta: ScoreDelta | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def _cap_context(cls, value: str) -> str:
        """The same cap `NormalizedDesign` applies, for the same two reasons."""
        return value.strip()[:MAX_CONTEXT_CHARS]


# --------------------------------------------------------------------------- #
# Progress tracking (polled by the UI)
# --------------------------------------------------------------------------- #

STAGES: tuple[str, ...] = (
    "ingest",
    "normalize",
    # The relevance gate. Its own stage, and placed here deliberately: it is the
    # first point at which the normalized design exists, and the last point before
    # the five expensive stages begin. One small model call decides whether the
    # remaining five are worth making. See ingestion/relevance.py.
    "screen",
    "classify",
    "evaluate",
    "prioritize",
    "remediate",
)

# `cancelled` marks the stage a deliberate stop interrupted. Distinct from `error`
# because nothing went wrong, and distinct from `done` because it did not finish.
#
# `rejected` marks the screen stage refusing the upload. Distinct from `error` for
# the same reason `cancelled` is: nothing malfunctioned. The pipeline looked at
# what was submitted, decided it was not a solution design, and stopped before
# spending anything on it. Presenting that as a crash would send the submitter
# looking for a fault that does not exist.
StageState = Literal["pending", "running", "done", "error", "cancelled", "rejected"]


class StageProgress(BaseModel):
    name: str
    state: StageState = "pending"
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""


class ReviewStatus(BaseModel):
    review_id: str
    # `cancelled` and `rejected` are terminal like `complete` and `error`, and
    # neither is a kind of success: no ReviewResult is stored for either, so the
    # result route has nothing to serve and the history list never shows them.
    state: Literal[
        "queued", "running", "complete", "error", "cancelled", "rejected"
    ] = "queued"
    stages: list[StageProgress] = Field(default_factory=list)
    error: str = ""

    # Why the screen stage refused the upload, in prose written for the person who
    # uploaded it. Separate from `error` on purpose: `error` is where the pipeline
    # records `f"{type(exc).__name__}: {exc}"` for a genuine fault, and the UI
    # renders it under a "Pipeline error" heading. A refusal is neither a fault nor
    # something to debug, so it gets its own field and its own presentation.
    rejection: str = ""

    # Populated as ingestion finds them, so a reviewer watching the progress screen
    # sees "this diagram was barely legible" while the review is still running
    # rather than only at the end.
    warnings: list[IngestWarning] = Field(default_factory=list)
    updated_at: str = ""

    @classmethod
    def initial(cls, review_id: str) -> "ReviewStatus":
        return cls(
            review_id=review_id,
            state="queued",
            stages=[StageProgress(name=name) for name in STAGES],
        )
