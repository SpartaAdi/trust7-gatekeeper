"""The common schema every part of the pipeline speaks.

Both diagram input paths — draw.io XML parsed deterministically, and image
uploads parsed via Claude vision — converge on `DesignGraph`. Nothing
downstream can tell which path a design arrived through.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

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

# Cap on the free-text feedback a re-review carries.
#
# Larger than MAX_CONTEXT_CHARS because it is doing more work: a reviewer
# correcting a review writes prose about several checks, quotes the design back,
# and explains what was misread. 4000 characters is a page of that.
#
# Still capped, and for the same two reasons the context field is: it rides in the
# prompt of every evaluate call (twice — once per framework) plus remediate, so
# cost scales with it; and it is submitter-typed text, which makes it the most
# direct injection surface in the system. See `agent/untrusted.py`.
MAX_FEEDBACK_CHARS = 4000


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
    # The vision model reported HIGH confidence but still named something it could
    # not make out. A bounded gap, and deliberately not the code above: reporting it
    # as low confidence contradicts the model's own report in the same warning.
    "vision_minor_gaps",
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
#
# Applied to the STRUCTURAL ratio only. It was applied to the OCR proxy too and no
# longer is: see `DataFidelity.review_recommended` for why an estimate that reads
# 83% on a perfectly-extracted diagram cannot be allowed to fire an automated flag.
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

    And it triggers NOTHING automatically. `DataFidelity.review_recommended` does not
    read this field, and the UI never renders this panel at the caution tone. It is a
    figure for a human to weigh, not a gate — because it sits under any useful
    threshold on diagrams that were extracted perfectly, so an automated flag driven
    by it would fire on correct work and teach reviewers to ignore the panel.
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

        **Only `structural` can set this.** The other two are deliberately excluded,
        for different reasons:

        * `ocr_proxy` is an ESTIMATE against a second fallible reader, and it does
          not clear the bar for firing an automated recommendation. In testing, a
          COMPLETE extraction of a five-box diagram measured 83% purely because OCR
          also read the diagram's title — and titles, legends and region labels are
          in every real diagram, so the estimate sits under any useful threshold on
          inputs that were read perfectly. An automated flag that fires on correct
          work trains people to dismiss it, and then it is worth less than nothing.
          The number stays visible and stays labelled an estimate; it just does not
          pull a lever on its own.
        * `grounding` is a count of what the filter REMOVED. Removing an ungrounded
          claim is the filter working, so feeding it in here would penalise a run
          for being filtered well.

        `structural` is the one input because it is the only one that is exact: the
        draw.io XML enumerates its own elements, so a figure under the threshold
        means elements were genuinely lost, not that two readers disagreed.
        """
        return bool(
            self.structural and self.structural.percent < COVERAGE_REVIEW_THRESHOLD
        )


# --------------------------------------------------------------------------- #
# AI/ML detection — an audit record, and the input to a one-way gate
#
# Eighteen of the forty-five checks only mean anything if the design has an AI or
# ML component; the rubric declares which eighteen, per check, via
# `ai_conditional`. This record exists so that applicability can be CHECKED rather
# than trusted: it says what was searched for, what matched, and where, for every
# review.
#
# `scoring.py` never reads it — a score stays reproducible from the statuses and
# the rubric alone. But the record is no longer inert: on an `absent`/`denied`
# verdict, `agent/ai_gate.py` may turn an AI-conditional check into
# `not_applicable`, which scoring does read. That gate is ONE-WAY (it can never
# force a check to be evaluated) and it defers to any verdict the model backed with
# evidence. Read that module before assuming this record cannot move a number.
#
# Where the record and the model disagree in the other direction, that is still
# surfaced rather than resolved — `disagrees_with_pillar` below.
# --------------------------------------------------------------------------- #

AiSignalTier = Literal[
    # A component some earlier stage already called `ai_model`. Not a text match —
    # a conclusion the classify stage or the draw.io keyword map reached.
    "classified_kind",
    # A specific AI/ML product, service or model family: Bedrock, SageMaker, GPT-4.
    "named_service",
    # Generic but unambiguous ML vocabulary: "training data", "vector store",
    # "fine-tuning", "inference".
    "explicit_term",
    # A business capability that is almost always ML-implemented and almost never
    # says so: "recommendation engine", "propensity", "churn prediction". Suggestive
    # only — such a thing genuinely can be hand-written rules.
    "implicit_function",
    # The design STATES it has no AI/ML. A claim, recorded as one, never believed on
    # its own — see the note on `verdict`.
    "denial",
]


class AiSignal(BaseModel):
    """One piece of AI/ML evidence, and where it was found.

    `source` and `excerpt` are the whole point. "An AI signal was detected" is not
    auditable; "the phrase 'propensity model' appears in the solution document, in
    this sentence" is something a reviewer can agree or disagree with.
    """

    tier: AiSignalTier
    signal: str = Field(description="What was found, named for a human: 'Amazon "
                        "Bedrock', 'training data/job', 'churn prediction'.")
    source: str = Field(description="Where it was found — a named diagram "
                        "component, a diagram edge, the solution document, or a "
                        "specific field of the classify stage's output.")
    excerpt: str = Field(description="The match with a little surrounding text, so "
                         "the reader can judge whether it means what it looks like.")


class AiDetection(BaseModel):
    """The reproducible AI/ML evidence record for one design. No model call.

    Deliberately stores only what was OBSERVED — the signals, how many patterns ran,
    and the component labels that were searched. Every conclusion (`verdict`,
    `rationale`, `disagrees_with_pillar`) is computed from those, so a stored record
    cannot drift out of agreement with its own evidence the way a stored prose
    summary would.
    """

    signals: list[AiSignal] = Field(default_factory=list)
    patterns_checked: int = Field(
        default=0,
        description="How many patterns were run. Part of the audit trail: it "
        "distinguishes 'searched thoroughly, found nothing' from 'barely looked'.",
    )
    components_seen: list[str] = Field(
        default_factory=list,
        description="Component labels the detector searched. This is the "
        "'Components found: [...]' half of an auditable not-applicable — it lets a "
        "reviewer overrule the record on sight instead of taking it on trust.",
    )

    def _tiers(self) -> set[str]:
        return {signal.tier for signal in self.signals}

    @property
    def positive_signals(self) -> list[AiSignal]:
        """Everything except denials.

        A plain property, NOT a computed field: serializing it would repeat most of
        `signals` in every stored review and every API response for a filter the
        caller can apply in one line.
        """
        return [s for s in self.signals if s.tier != "denial"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict(
        self,
    ) -> Literal["present", "likely", "contradicted", "denied", "absent", "not_run"]:
        """What the evidence supports. Computed, so it cannot contradict `signals`.

        * `present` — a named service, unambiguous ML vocabulary, or a component
          already classified `ai_model`.
        * `likely` — only implicit capability signals. A "personalisation service"
          almost certainly holds a model, but it genuinely might be rules, and the
          record should not overstate what a phrase proves.
        * `contradicted` — the design both denies AI and shows it. The most
          interesting outcome and the one a reviewer most needs to see; it usually
          means a document and a diagram were written at different times.
        * `denied` — the design says it has no AI, and nothing contradicts that. Note
          this is still weaker than `absent`: `absent` is silence, this is a claim,
          and a claim inside submitted material is not evidence of itself.
        * `absent` — patterns ran and nothing matched.
        * `not_run` — no detection happened. A review stored before this record
          existed, and NOT the same statement as `absent`: one says "we looked and
          found nothing", the other says "nobody looked". Reporting the second as the
          first would put a claim about the design in front of a reviewer that
          nothing in the system ever established.
        """
        if not self.patterns_checked:
            return "not_run"

        tiers = self._tiers()
        strong = tiers & {"classified_kind", "named_service", "explicit_term"}
        weak = "implicit_function" in tiers
        denied = "denial" in tiers

        if denied and (strong or weak):
            return "contradicted"
        if strong:
            return "present"
        if weak:
            return "likely"
        if denied:
            return "denied"
        return "absent"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rationale(self) -> str:
        """One sentence a results page or a PDF can print verbatim.

        Computed rather than stored for the same reason `verdict` is: prose written
        at detection time and saved alongside the evidence is prose that can end up
        describing evidence it no longer matches.
        """
        named = []
        for signal in self.positive_signals:
            if signal.signal not in named:
                named.append(signal.signal)
        listed = ", ".join(named[:6]) + ("…" if len(named) > 6 else "")

        if self.verdict == "present":
            return f"AI/ML component detected. Evidence: {listed}."
        if self.verdict == "likely":
            return (
                f"AI/ML component likely but never labelled as one. Suggestive "
                f"evidence: {listed}. A capability like this is usually "
                f"model-backed, but could be implemented as rules."
            )
        if self.verdict == "contradicted":
            return (
                f"The design states it has no AI/ML, but AI/ML evidence was also "
                f"found: {listed}. Document and diagram may disagree."
            )
        if self.verdict == "denied":
            return (
                f"No AI/ML component detected, and the design explicitly states it "
                f"has none. {self._searched()}"
            )
        if self.verdict == "not_run":
            return (
                "AI/ML detection did not run for this review — it was stored before "
                "the check existed. This is not a finding that the design has no "
                "AI/ML component; re-run the review to establish that either way."
            )
        return f"No AI/ML component detected. {self._searched()}"

    def _searched(self) -> str:
        if not self.components_seen:
            return f"{self.patterns_checked} AI/ML patterns checked; no components were extracted."
        count = len(self.components_seen)
        shown = ", ".join(self.components_seen[:12])
        more = f" (+{count - 12} more)" if count > 12 else ""
        return (
            f"{self.patterns_checked} AI/ML patterns were checked against "
            f"{count} {'component' if count == 1 else 'components'}: {shown}{more}."
        )

    def disagrees_with_pillar(self, pillar: PillarScore) -> bool:
        """Whether this record contradicts a pillar being wholly not-applicable.

        True when the evaluate stage skipped every check in a pillar while the
        evidence says AI is present or likely. That is the case worth a reviewer's
        eye, and it is reported rather than corrected: nothing here overrides the
        verdicts, because a regex is not better placed than the model to decide the
        check — only better placed to be argued with.

        Symmetrically silent in the other direction. A pillar that WAS evaluated on
        a design with no AI evidence is not flagged, because the AI-dependent checks
        are spread across pillars that also contain non-AI ones, so "evaluated" does
        not imply "the model thought there was AI here".
        """
        return pillar.checks_evaluated == 0 and self.verdict in {
            "present",
            "likely",
            "contradicted",
        }


class RemediationGap(BaseModel):
    """Open findings the remediate stage produced no guidance for.

    Derived from the stored findings rather than recorded by the stage, so it cannot
    drift from what is actually on the page and so a review stored before this
    existed reports correctly without a migration.

    It exists because of a real run: remediate returned 0 of 25 open findings, the
    retry returned 0 of 25 again, and the review was written, stored and served as a
    normal completed review. Server-side there were two log lines. Client-side there
    was nothing — every roadmap row read "No remediation text was generated for this
    check.", and the only page-level status was the data-fidelity panel saying the
    diagram had been read at 100%, which reads as reassurance.

    Deliberately a COUNT of what is missing, and no rate. "22 of 28 actions have
    guidance" invites reading 79% as a quality figure for the six that do not.
    """

    open_findings: int = Field(description="Findings with status fail or partial.")
    without_guidance: int = Field(
        description="Of those, how many carry no remediation text."
    )
    check_ids: list[str] = Field(
        default_factory=list,
        description="Which ones, so the gap is checkable rather than a number.",
    )

    @property
    def any_missing(self) -> bool:
        return self.without_guidance > 0

    @property
    def total(self) -> bool:
        """Every open finding, not some. A different failure with a different cause.

        A partial shortfall is a model running out of steam on a long list. Zero of
        everything — twice, since the stage retries — is either a provider dip or a
        systematic mismatch, and it means the roadmap has no content at all.
        """
        return self.open_findings > 0 and self.without_guidance == self.open_findings


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

    # The AI/ML evidence record, so a not-applicable pillar can say why rather than
    # just saying so. Audit only — it moves no verdict and no score; see the note
    # above `AiDetection`.
    #
    # Defaults to an empty record, whose `verdict` is "absent" with
    # `patterns_checked: 0`. On a review stored before this existed that reads
    # correctly as "no detection was run", not as "no AI was found" — the two are
    # told apart by `patterns_checked`, which is why it is part of the record.
    ai_detection: AiDetection = Field(default_factory=AiDetection)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remediation_gap(self) -> RemediationGap:
        """Open findings left with no remediation text. See `RemediationGap`.

        Computed from `findings`, not stored: the roadmap and the findings list read
        the same field this counts, so the count cannot disagree with the page. It
        also means every review already on disk reports its real state without being
        rewritten.
        """
        open_findings = [f for f in self.findings if f.status in ("fail", "partial")]
        missing = [f.check_id for f in open_findings if not f.remediation.strip()]
        return RemediationGap(
            open_findings=len(open_findings),
            without_guidance=len(missing),
            check_ids=sorted(missing),
        )

    # ----------------------------------------------------------------------- #
    # The design this review was scored against, retained so a follow-up
    # re-review can resume from it.
    #
    # Stored rather than re-derived, and that is the whole point. A feedback-only
    # re-review must "skip re-ingest entirely" — so the material has to be HERE,
    # not behind a second parse of `diagram_key`. Re-deriving it would also make a
    # follow-up depend on the uploads still being on disk, and Render's free-tier
    # disk is ephemeral: the design would silently come back empty after a restart
    # and the re-review would score nothing while looking like it worked.
    #
    # `document_text` is already capped at 400,000 characters by
    # `ingestion/documents.MAX_CHARS`, so this bounds the record rather than
    # opening it up. Both default empty, so every review stored before this field
    # existed still loads — a re-review of one of those has no design to resume
    # from, and `pipeline.re_review` refuses it explicitly rather than scoring air.
    # ----------------------------------------------------------------------- #
    graph: DesignGraph | None = Field(
        default=None,
        description="The component graph this review was scored against. None on "
        "reviews stored before re-review existed.",
    )
    document_text: str = Field(
        default="",
        description="The normalized document text this review was scored against.",
    )
    classification: dict[str, Any] = Field(
        default_factory=dict,
        description="The classify stage's raw payload for this review, retained so a "
        "feedback-only re-review can skip classify as well as ingest. Its `absent` "
        "list is what the evaluate prompt calls the most important part of the "
        "inventory, so reconstructing a stand-in from `components` would quietly "
        "degrade every re-review.",
    )

    # ----------------------------------------------------------------------- #
    # Version linkage
    #
    # A re-review is a NEW record with its own `review_id`, not an edit of this
    # one. Nothing overwrites anything: the original file on disk is never
    # rewritten, so every version stays independently retrievable through the
    # existing `GET /reviews/{id}` with no new read path.
    #
    # `root_review_id` is the whole chain's identity and is what makes the set
    # discoverable from any member. It is "" on an original — deliberately, rather
    # than self-referential, so "is this a re-review?" is a truthiness check and
    # older records answer it correctly without a migration.
    # ----------------------------------------------------------------------- #
    version: int = Field(
        default=1, description="1 for an original review; 2+ for each re-review."
    )
    root_review_id: str = Field(
        default="",
        description="The original review this chain descends from. Empty on the "
        "original itself.",
    )
    based_on_review_id: str = Field(
        default="",
        description="The specific version this one was produced from — the latest "
        "in the chain at the time. Empty on an original.",
    )
    feedback: str = Field(
        default="",
        description="The reviewer's free-text feedback that prompted this version. "
        "UNTRUSTED: fenced by `untrusted.wrap()` at every prompt it reaches, and "
        "never treated as evidence that can move a verdict by assertion.",
    )

    @field_validator("feedback")
    @classmethod
    def _cap_feedback(cls, value: str) -> str:
        """Truncate at the cap, on the model so every entry point inherits it."""
        return value.strip()[:MAX_FEEDBACK_CHARS]
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

    # The running token total, republished after every stage that spends tokens.
    # Same shape as `ReviewResult.token_usage`, which holds the settled figure — this
    # is that number mid-flight, so a reviewer watching a long run can see it is
    # spending rather than stalled.
    token_usage: dict[str, int] = Field(default_factory=dict)

    # An UPPER BOUND on what the tokens above have cost, in USD, at the list prices
    # in config.py. Not a bill and not a blend: see the pricing block there for why
    # it is a ceiling. Rendered with an explicit "at most", never as a precise
    # figure.
    estimated_cost_usd: float = 0.0

    updated_at: str = ""

    @classmethod
    def initial(cls, review_id: str) -> "ReviewStatus":
        return cls(
            review_id=review_id,
            state="queued",
            stages=[StageProgress(name=name) for name in STAGES],
        )
