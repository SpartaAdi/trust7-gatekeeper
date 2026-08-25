"""Pipeline orchestration.

    ingest -> normalize -> classify -> evaluate -> prioritize -> remediate
    -> structured JSON

Each stage writes its own progress to the `review_status` table as it starts and
finishes, so the UI's progress bar reflects real pipeline state rather than a
timer.
"""

from __future__ import annotations

import logging
import time

import cancel
import config
import llm
import rubric
import scoring
import storage
from agent import ai_detection, ai_gate, stages
from ingestion import normalize, relevance
from schema import (
    DesignGraph,
    DiagramSource,
    Finding,
    IngestWarning,
    NormalizedDesign,
    ReviewResult,
    ReviewStatus,
)

log = logging.getLogger(__name__)

# Characters of document text below which an empty classify result is believable.
#
# 1,000 is about two-thirds of a page. `ingestion/quality.py` puts a real page of a
# solution document at 1,500-3,000 characters, so a document-only upload under this
# has not said enough for "it describes no architecture" to be surprising.
#
# The floor exists because an empty inventory means two different things depending
# on where it came from, and only one of them is evidence about the design. From an
# analyzed diagram it is a real finding: drawio parsing is deterministic and vision
# reports what it saw, so zero components means zero components. From classify over
# text it is ambiguous — `stages.classify` documents a real run that returned
# `components: []` in 34 output tokens for an input that had 8, and its retry exists
# precisely because that is a provider quality dip rather than an empty design.
# Rejecting on a bare classify miss would discard a sound review on a bad day, which
# is the trade that docstring explicitly refuses to make.
#
# Grounded against the suite's own fixtures rather than picked: the toy documents
# sit at 32-387 characters, the smallest realistic one at 2,361, and the real
# RMBL-Control-Tower SoW at 24,215. Nothing in the corpus falls between 387 and
# 2,361, so this threshold lands in an empty band instead of cutting a cluster.
MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY = 1000


class _Progress:
    """Writes stage transitions to the status table the UI polls."""

    def __init__(self, status: ReviewStatus) -> None:
        self._status = status
        self._usages: list[dict[str, int]] = []

    def record_usage(self, usage: dict[str, int]) -> None:
        """Add one call's token usage and republish the running total.

        The accumulator lives here rather than as a local list in each pipeline
        function for one reason: every spend has to reach the status, and a list the
        caller owns only reaches it if the caller remembers to publish. Owning it
        means "recorded" and "visible to the UI" are the same act — the same argument
        as the cancellation gate living in `start`.

        Called after every model call including the ones that fail over or retry, so
        the total reflects what was actually spent rather than what a successful run
        would have cost. Note that an aborted call bills but reports nothing: a
        CallDeadlineExceeded never returns a usage object, so its tokens are real
        money this figure cannot see. It is a floor on spend and a ceiling on price.
        """
        self._usages.append(usage)
        totals = _sum(self._usages)
        self._status.token_usage = totals
        self._status.estimated_cost_usd = estimated_cost(totals)
        storage.put_status(self._status)

    def usage_totals(self) -> dict[str, int]:
        """The settled total, for the stored result."""
        return _sum(self._usages)

    def start(self, stage: str, detail: str = "") -> None:
        # The cancellation gate, and deliberately here rather than at six call
        # sites: every stage begins with `start`, so a stage added later is guarded
        # by construction instead of by whoever remembers. Checked BEFORE the
        # status write, so a cancelled review never records a stage as running.
        cancel.check(self._status.review_id)
        self._status.state = "running"
        for entry in self._status.stages:
            if entry.name == stage:
                entry.state = "running"
                entry.detail = detail
                entry.started_at = _now()
        storage.put_status(self._status)

    def finish(self, stage: str, detail: str = "") -> None:
        for entry in self._status.stages:
            if entry.name == stage:
                entry.state = "done"
                entry.finished_at = _now()
                if detail:
                    entry.detail = detail
        storage.put_status(self._status)

    def detail(self, stage: str, detail: str) -> None:
        """Update a running stage's detail line without changing its state."""
        for entry in self._status.stages:
            if entry.name == stage:
                entry.detail = detail
        storage.put_status(self._status)

    def fail(self, stage: str, error: str) -> None:
        self._status.state = "error"
        self._status.error = error
        for entry in self._status.stages:
            if entry.name == stage:
                entry.state = "error"
                entry.detail = error
                entry.finished_at = _now()
        storage.put_status(self._status)

    def warn(self, warnings: list[IngestWarning]) -> None:
        """Publish ingestion warnings onto the status the UI is already polling.

        Written as soon as ingest produces them rather than only onto the stored
        result, so a reviewer watching the progress screen learns the diagram was
        illegible while the review is still running — at which point stopping it and
        uploading a better copy is still worth doing.
        """
        if not warnings:
            return
        self._status.warnings = list(warnings)
        storage.put_status(self._status)

    def rejected(self, stage: str, message: str, detail: str = "") -> None:
        """Record a stage refusing the upload.

        Not `fail`, for the same reason `cancelled` is not: nothing malfunctioned.
        The message goes in `rejection` rather than `error` so the UI can present it
        as "we did not review this, and here is why" instead of under a "Pipeline
        error" heading that sends the submitter hunting for a fault.

        `detail` overrides the stage line for a refusal that is not the screen gate's.
        The default says "not a solution design", which is the screen gate's finding
        and only its finding — a design that screen accepted and classify then found
        no components in IS a solution design, and labelling it otherwise contradicts
        the rejection message shown beside it.
        """
        self._status.state = "rejected"
        self._status.error = ""
        self._status.rejection = message
        for entry in self._status.stages:
            if entry.name == stage:
                entry.state = "rejected"
                entry.detail = detail or "Not a solution design — not reviewed"
                entry.finished_at = _now()
        storage.put_status(self._status)

    def cancelled(self, stage: str) -> None:
        """Record a deliberate stop.

        Not `fail`: `error` is reserved for something going wrong, and the screen
        has to be able to tell the reviewer which of the two happened. The stage
        that was interrupted is marked `cancelled` rather than left `running`, so
        the tracker does not show a spinner on a stopped review.
        """
        self._status.state = "cancelled"
        self._status.error = ""
        for entry in self._status.stages:
            if entry.name == stage:
                entry.state = "cancelled"
                entry.detail = "Stopped by the reviewer"
                entry.finished_at = _now()
        storage.put_status(self._status)

    def complete(self) -> None:
        self._status.state = "complete"
        storage.put_status(self._status)


def run(
    *,
    review_id: str,
    title: str = "",
    document_key: str = "",
    diagram_key: str = "",
    context: str = "",
    previous_review_id: str = "",
    api_key: str = "",
) -> ReviewResult:
    """Run a full review and persist the result. Raises on failure.

    A thin wrapper so the cancellation scope is stated once, at the boundary,
    rather than indenting the whole pipeline inside a `with`:

    * `llm.cancellation` makes the flag visible to the request on the wire, so a
      cancel arriving mid-stage can stop that stage and not just the next one.
    * `llm.api_key_override` bills this review's six calls to the reviewer's own
      key when they supplied one, and resets on the way out. Empty means the
      server's key, which is the unchanged default. `api_key` stops here and is
      never passed to `_run` — nothing below this line needs it, and the less of
      the pipeline that can see a credential the better.
    * `cancel.clear` runs however the review ends, so the registry does not grow
      for the lifetime of the process.
    """
    with llm.cancellation(lambda: cancel.is_cancelled(review_id)), llm.api_key_override(
        api_key
    ):
        try:
            return _run(
                review_id=review_id,
                title=title,
                document_key=document_key,
                diagram_key=diagram_key,
                context=context,
                previous_review_id=previous_review_id,
            )
        finally:
            cancel.clear(review_id)


def _run(
    *,
    review_id: str,
    title: str,
    document_key: str,
    diagram_key: str,
    context: str,
    previous_review_id: str,
) -> ReviewResult:
    status = storage.get_status(review_id) or ReviewStatus.initial(review_id)
    progress = _Progress(status)
    stage = "ingest"

    try:
        # ---- ingest + normalize --------------------------------------------- #
        progress.start("ingest", "Reading uploads")
        design, ingest_usage = normalize.ingest(
            review_id=review_id,
            title=title,
            document_key=document_key,
            diagram_key=diagram_key,
            context=context,
        )
        progress.record_usage(ingest_usage)
        progress.finish(
            "ingest",
            f"{len(design.graph.components)} components from "
            f"{design.graph.source.value}",
        )

        stage = "normalize"
        progress.start("normalize", "Converging inputs on the common schema")
        # Both diagram paths already emit the common schema, so normalization is
        # the merge `ingest` performed — recorded as its own stage because the UI
        # tracks it and a future non-diagram input would do real work here.
        progress.finish("normalize", f"{len(design.document_text)} characters of document text")

        # Published before the gate below, so a warning about an illegible diagram
        # reaches the screen even if the gate then rejects the upload — the two are
        # frequently the same submission seen from two angles.
        progress.warn(design.warnings)

        # ---- screen --------------------------------------------------------- #
        #
        # The cheap gate in front of the expensive stages. One small call decides
        # whether the next five are worth making: a resume, an invoice or a
        # photograph is a valid PDF or PNG that no amount of rubric evaluation can
        # say anything useful about, and the five stages below would spend roughly
        # thirty times this call's cost proving it.
        #
        # It can only ever stop a review it positively identified as unreviewable.
        # `relevance.screen` absorbs its own failures and returns None, and an
        # `uncertain` or low-confidence verdict becomes a warning rather than a
        # refusal — see the conservatism note in ingestion/relevance.py.
        stage = "screen"
        progress.start("screen", "Checking the upload is a solution design")
        assessment, usage = relevance.screen(design)
        progress.record_usage(usage)

        if assessment is None:
            progress.finish("screen", "Relevance check unavailable — continuing")
        elif assessment.rejects:
            message = relevance.rejection_message(assessment)
            log.info(
                "Review %s rejected by the relevance gate: verdict=%s confidence=%s "
                "subject=%r",
                review_id, assessment.verdict, assessment.confidence,
                assessment.subject,
            )
            progress.rejected(stage, message)
            # No result is stored, exactly as for a cancellation: there is no review
            # to show. `NotReviewable` propagates so the caller sees the refusal, and
            # `api/routes.py` logs it without treating it as a crash.
            raise relevance.NotReviewable(message)
        elif assessment.verdict == "reviewable":
            detail = f"Recognised as {assessment.subject}" if assessment.subject else "Confirmed"
            progress.finish("screen", detail)
        else:
            # `uncertain`, or `unrelated` the gate was not confident about. The review
            # proceeds and says so.
            design.warnings.append(relevance.uncertainty_warning(assessment))
            progress.warn(design.warnings)
            progress.finish(
                "screen",
                f"Not confirmed as a design ({assessment.confidence} confidence) "
                f"— continuing with a warning",
            )

        # ---- classify ------------------------------------------------------- #
        stage = "classify"
        progress.start("classify", "Classifying components")
        classification, usage = stages.classify(design)
        progress.record_usage(usage)
        components = stages.classified_components(classification)

        # Nothing classified AND nothing in the graph means there is no inventory for
        # evaluate to score. Stop here rather than spending the four remaining calls
        # producing a review of an empty set — evaluate is the most expensive stage in
        # the pipeline, and scoring nothing yields a number that looks like a finding.
        #
        # Components empty while the graph is NOT is a different situation, already
        # handled below: the diagram gave us an inventory even though classify
        # returned none, so the review goes on.
        if not components and not design.graph.components:
            # Where the emptiness came from decides whether it is evidence. An
            # analyzed diagram that yielded nothing is a real finding; classify
            # returning nothing over text is ambiguous until the text is thin enough
            # for the answer to be plausible. See the constant for the full argument.
            diagram_analyzed = design.graph.source in (
                DiagramSource.DRAWIO, DiagramSource.IMAGE
            )
            characters = len(design.document_text.strip())
            thin = characters < MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY

            if diagram_analyzed or thin:
                message = relevance.no_components_message()
                log.info(
                    "Review %s stopped after classify: no components from either "
                    "source (source=%s, document_text=%d chars, graph components=0)",
                    review_id, design.graph.source.value, characters,
                )
                progress.rejected(
                    stage, message, detail="No components identified — not reviewed"
                )
                # Same contract as the screen gate's refusal: no result stored, and
                # `NotReviewable` propagates so `api/routes.py` logs a refusal rather
                # than a crash.
                raise relevance.NotReviewable(message)

            # Substantial text, no diagram, and classify still found nothing twice.
            # The review continues, per `stages.classify`: evaluate reads the design
            # text rather than this inventory, so the findings can still be sound.
            # Logged at WARNING because it is the signature of a degraded classify
            # call, and the only place that shows up after the fact.
            log.warning(
                "Review %s: classify returned no components for %d characters of "
                "document text with no diagram. Above the %d-character floor, so the "
                "review continues on the design text — see MIN_DOC_CHARS_TO_TRUST_AN_"
                "EMPTY_CLASSIFY.",
                review_id, characters, MIN_DOC_CHARS_TO_TRUST_AN_EMPTY_CLASSIFY,
            )

        # An empty inventory against a non-empty design is worth saying out loud on
        # the screen the reviewer is watching. Ingest has already reported what it
        # found, so "0 components classified" on its own reads as a contradiction the
        # user has to work out for themselves.
        detail = f"{len(components)} components classified"
        if not components and stages.design_has_content(design):
            if design.graph.components:
                detail = (
                    f"0 components classified despite "
                    f"{len(design.graph.components)} in the diagram — the review "
                    f"continues from the design text"
                )
            else:
                # Reachable only above the character floor: a document-only upload
                # with real text that classify found nothing in. The old wording said
                # "despite 0 in the diagram", which is not a contradiction and reads
                # as one.
                detail = (
                    "0 components classified from the document text — the review "
                    "continues from the design text"
                )
        progress.finish("classify", detail)

        # The AI/ML evidence record. Built here, straight after classify, because
        # this is the first point all three of its inputs exist. Deterministic, no
        # model call. It is both the audit trail behind a not-applicable pillar AND —
        # since the one-way gate below landed — the input that can set one. See
        # schema.AiDetection for the record and agent/ai_gate.py for the gate.
        detection = ai_detection.detect(
            design.graph, design.document_text, classification
        )
        log.info(
            "ai detection for %s: verdict=%s from %d signals across %d patterns",
            review_id, detection.verdict, len(detection.signals),
            detection.patterns_checked,
        )

        # ---- evaluate ------------------------------------------------------- #
        stage = "evaluate"
        frameworks = [f.key for f in rubric.load()]
        progress.start("evaluate", f"Evaluating {len(rubric.all_checks())} checks")
        findings: list[Finding] = []
        for index, framework_key in enumerate(frameworks, 1):
            framework = next(f for f in rubric.load() if f.key == framework_key)
            progress.detail(
                "evaluate", f"{framework.name} ({index} of {len(frameworks)})"
            )
            framework_findings, usage = stages.evaluate(
                design, classification, framework_key
            )
            findings.extend(framework_findings)
            progress.record_usage(usage)

        # ---- the one-way AI-applicability gate ------------------------------- #
        #
        # Between evaluate and prioritize deliberately: the gate's output is a
        # `not_applicable` status, and everything downstream — prioritize, remediate,
        # the roadmap, scoring — reads statuses. Running it here means one code path
        # produces the statuses and every consumer sees the same set.
        #
        # It can ONLY mark an AI-conditional check not_applicable, only when
        # detection says absent/denied, and never over an evidence-bearing verdict.
        # See agent/ai_gate.py for why each of those guards is there.
        gated = ai_gate.apply(findings, detection)
        gate_note = ""
        if gated:
            # Carried into evaluate's FINAL detail line rather than shown mid-stage
            # and then overwritten by it. A status the code set, not the model, is
            # exactly the kind of thing this project does not do silently — and the
            # transient version of this message was invisible by the time the stage
            # finished, which is the same as not sending it.
            gate_note = (
                f"; {len(gated)} AI-specific checks marked not applicable — "
                f"no AI/ML component detected"
            )
            progress.detail("evaluate", gate_note.lstrip("; "))

        open_count = sum(1 for f in findings if f.status in ("fail", "partial"))
        progress.finish(
            "evaluate",
            f"{open_count} gaps found across {len(findings)} checks{gate_note}",
        )

        # ---- prioritize ----------------------------------------------------- #
        stage = "prioritize"
        progress.start("prioritize", "Ranking findings")
        ranking_payload, usage = stages.prioritize(findings, classification)
        progress.record_usage(usage)
        ranked, backfilled = stages.apply_ranking(
            findings, ranking_payload.get("ranking", [])
        )
        # The old line printed only what the model returned, which read as though
        # that were the whole job: "19 findings ranked" against 31 open gaps, with no
        # hint that 12 were left unranked. It now states the total and the shortfall.
        detail = f"{ranked + backfilled} findings ranked"
        if backfilled:
            detail += f" ({ranked} by the model, {backfilled} by severity)"
        progress.finish("prioritize", detail)

        # ---- score, then remediate ------------------------------------------ #
        # Scoring only needs the findings, so it runs before remediation: the
        # executive summary quotes these figures rather than recounting them.
        overall, framework_scores = scoring.score(findings)

        stage = "remediate"
        progress.start("remediate", "Generating remediation and summary")
        (
            remediations,
            efforts,
            executive_summary,
            use_case_notes,
            usage,
            grounding,
            remediation_quotes,
        ) = stages.remediate(
            findings,
            classification,
            scoring.scoreboard(overall, framework_scores, findings),
            context=design.context,
            # The design SOURCE, so `grounded_in` has something real to quote and
            # something real to be checked against. Until this was passed, remediate
            # saw only the classify stage's restatement of the design.
            design=design,
        )
        # The third fidelity number, and the only one not measured at ingestion:
        # the grounding filter can only be counted where it runs. Left as None when
        # the stage made no call, so "not measured" stays distinct from "0 caught".
        design.fidelity.grounding = grounding
        progress.record_usage(usage)
        for finding in findings:
            finding.remediation = remediations.get(finding.check_id, "")
            finding.remediation_effort = efforts.get(finding.check_id, "")  # type: ignore[assignment]
            # Empty unless the quote was verified present in the design source.
            finding.remediation_grounded_in = remediation_quotes.get(finding.check_id, "")
        progress.finish("remediate", f"{len(remediations)} remediations written")

        # ---- delta and persist ----------------------------------------------- #
        findings.sort(key=lambda f: (f.priority == 0, f.priority))

        result = ReviewResult(
            review_id=review_id,
            created_at=_now(),
            title=design.title,
            overall_score=overall,
            frameworks=framework_scores,
            findings=findings,
            components=components,
            summary=ranking_payload.get("summary", ""),
            executive_summary=executive_summary,
            # From the normalized design, not the raw request: that copy has
            # already been stripped and capped by the model's own validator, so
            # what is stored is exactly what the pipeline evaluated.
            context=design.context,
            diagram_key=diagram_key,
            # Stored on the result, not only on the transient status: a reviewer
            # opening this review tomorrow must still see that its diagram was barely
            # legible, and the status file is not what they will be reading.
            warnings=design.warnings,
            # The pipeline state a follow-up re-review resumes from. Stored rather
            # than re-derived so a feedback-only round can genuinely skip ingest AND
            # classify, and so a round still works after Render's ephemeral disk has
            # taken the original uploads with it. See the field notes in schema.py.
            graph=design.graph,
            document_text=design.document_text,
            classification=classification,
            # PRE-EXISTING BUG, found while instrumenting the grounding filter and
            # fixed here because that metric is misleading without it: these were
            # unpacked from `remediate` and then never stored, so the results page's
            # "For your stated use case" section — which reads exactly this field —
            # has always rendered empty in production. Reporting "2 ungrounded claims
            # caught and removed" while silently discarding the claims that PASSED
            # the filter would describe a feature that does not reach the screen.
            use_case_notes=use_case_notes,
            # Three separate numbers, carried as measured. Nothing between here and
            # the screen combines them — see the note on DataFidelity in schema.py.
            fidelity=design.fidelity,
            # Why the AI-dependent checks were or were not applicable. Stored so the
            # answer survives the run rather than living only in a log line.
            ai_detection=detection,
            token_usage=progress.usage_totals(),
        )

        if previous_review_id:
            previous = storage.get_review(previous_review_id)
            if previous is not None:
                result.delta = scoring.delta(previous, result)
            else:
                log.warning(
                    "Previous review %s not found; skipping delta", previous_review_id
                )

        # The last gap. Every stage is gated by `progress.start`, but nothing calls
        # `start` between the final stage finishing and the result being written —
        # so without this a cancel arriving during remediate would be answered with
        # a stored, displayable review. A cancelled review is cancelled, not a
        # truncated success, and this is the line that makes that true.
        cancel.check(review_id)
        storage.put_review(result)
        progress.complete()
        return result

    except relevance.NotReviewable:
        # Before the generic handler, and deliberately not `fail`. `progress.rejected`
        # has already written the terminal status with its user-facing message; this
        # exists so the generic `except` below cannot overwrite that with
        # `state="error"` and `NotReviewable: <message>` under a "Pipeline error"
        # heading. Re-raised so the caller knows the review did not run.
        raise
    except cancel.Cancelled:
        # Deliberately before the generic handler, and deliberately not `fail`:
        # nothing went wrong. No result is stored, so `GET /reviews/{id}` has
        # nothing to serve and the history list never sees this review.
        log.info("Review %s cancelled during %s", review_id, stage)
        progress.cancelled(stage)
        raise
    except Exception as exc:  # noqa: BLE001 — recorded for the UI, then re-raised
        log.exception("Review %s failed during %s", review_id, stage)
        progress.fail(stage, f"{type(exc).__name__}: {exc}")
        raise


def _sum(usages: list[dict[str, int]]) -> dict[str, int]:
    total: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            total[key] = total.get(key, 0) + value
    return total


def estimated_cost(totals: dict[str, int]) -> float:
    """An upper bound on spend for a token total, in USD at list prices.

    Deliberately reads `input_tokens`/`output_tokens` rather than every key: the
    usage dict also carries `cache_read_input_tokens`, which is a SUBSET of
    `input_tokens` rather than an addition to it, and summing both would double-count
    the cached half of the prompt. See the pricing block in config.py for why cached
    input is charged at the full rate rather than its own cheaper one.
    """
    return (
        totals.get("input_tokens", 0) * config.OPENROUTER_PRICE_PROMPT_USD
        + totals.get("output_tokens", 0) * config.OPENROUTER_PRICE_COMPLETION_USD
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# Re-review: a follow-up round on an existing review
#
# A NEW versioned record every time, never an edit. The base review's file on disk
# is not rewritten, so the original and every version stay independently
# retrievable through the existing `GET /reviews/{id}` — there is no new read path
# and no migration.
#
# Two shapes, and the difference is what gets re-ingested:
#
#   * WITH a new attachment — ingest, screen and classify run on ONLY the new
#     attachment. A new diagram's graph REPLACES the previous graph outright; it is
#     never structurally merged, and the previous graph is passed to the model as
#     read-only reference so it can see what changed. A new document's text
#     replaces the previous text the same way.
#   * WITHOUT one — none of those three stages runs at all. The base review's
#     graph, document text and classification are reused verbatim from the stored
#     record, which is why they are stored (see the field notes in schema.py).
#
# Evaluate and remediate ALWAYS run. That is the point of the feature: a reviewer
# correcting a misread check changes the findings without changing the design, and
# a round that skipped evaluation could not express that.
# --------------------------------------------------------------------------- #


class ReReviewNotPossible(ValueError):
    """The base review cannot be followed up. The message is user-facing."""


def re_review(
    *,
    review_id: str,
    based_on_review_id: str,
    feedback: str,
    document_key: str = "",
    diagram_key: str = "",
    api_key: str = "",
) -> ReviewResult:
    """Run one follow-up round and persist it as a new version. Raises on failure.

    Same wrapper as `run` and for the same three reasons — a cancel must reach the
    request on the wire, a reviewer's own key must be scoped to this round, and the
    cancel registry must not grow. `review_id` is the NEW version's id, minted by
    the route; `based_on_review_id` is the version this round starts from.
    """
    with llm.cancellation(lambda: cancel.is_cancelled(review_id)), llm.api_key_override(
        api_key
    ):
        try:
            return _re_review(
                review_id=review_id,
                based_on_review_id=based_on_review_id,
                feedback=feedback,
                document_key=document_key,
                diagram_key=diagram_key,
            )
        finally:
            cancel.clear(review_id)


def _resume_design(base: ReviewResult, feedback: str) -> NormalizedDesign:
    """The base review's design, rebuilt from what was stored on it.

    Raises `ReReviewNotPossible` when the record predates the fields that make this
    possible. That is an honest refusal rather than a degraded round: without a
    stored graph and document text there is nothing to evaluate except the feedback,
    and scoring 45 checks against a sentence would produce a real-looking review of
    nothing.
    """
    if base.graph is None and not base.document_text:
        raise ReReviewNotPossible(
            f"Review {base.review_id!r} was created before follow-up re-reviews were "
            f"supported, so the design it was scored against was not retained and "
            f"cannot be re-evaluated. Submit the design again as a new review, then "
            f"follow up on that one."
        )
    return NormalizedDesign(
        review_id=base.review_id,
        title=base.title,
        document_text=base.document_text,
        graph=base.graph or DesignGraph(),
        context=base.context,
        # Warnings and fidelity belong to the round that measured them. A feedback-only
        # round re-measures nothing, so carrying the base's forward would restate a
        # measurement this round did not take.
    )


def _re_review(
    *,
    review_id: str,
    based_on_review_id: str,
    feedback: str,
    document_key: str,
    diagram_key: str,
) -> ReviewResult:
    base = storage.get_review(based_on_review_id)
    if base is None:
        raise ReReviewNotPossible(f"No review {based_on_review_id!r} to follow up on.")

    status = storage.get_status(review_id) or ReviewStatus.initial(review_id)
    progress = _Progress(status)
    has_attachment = bool(document_key or diagram_key)
    stage = "ingest"

    try:
        if has_attachment:
            design, reference_graph, classification = _ingest_new_attachment(
                review_id=review_id,
                base=base,
                progress=progress,
                document_key=document_key,
                diagram_key=diagram_key,
            )
        else:
            # Nothing is read, nothing is parsed, no model call is made for any of
            # the first four stages. They are marked done with a detail that says
            # SKIPPED — `StageState` has no `skipped` member, and adding one would
            # need a frontend change that is out of scope for this round, so the
            # detail line carries the truth rather than the state.
            design = _resume_design(base, feedback)
            reference_graph = None
            classification = base.classification or _classification_from(base)
            for skipped, detail in (
                ("ingest", "Skipped — no new attachment, reusing the stored design"),
                ("normalize", "Skipped — no new attachment"),
                ("screen", "Skipped — no new attachment to screen"),
                ("classify", f"Skipped — reusing {len(base.components)} components"),
            ):
                progress.finish(skipped, detail)

        # Recomputed for this version rather than copied from the base. On a
        # feedback-only round the design is identical so the record is too, but on a
        # round with a new attachment the design has been REPLACED — carrying the old
        # record forward would attribute the previous upload's AI evidence to a
        # design that may no longer contain it.
        detection = ai_detection.detect(
            design.graph, design.document_text, classification
        )

        # ---- evaluate — ALWAYS, even on feedback alone ----------------------- #
        stage = "evaluate"
        frameworks = [f.key for f in rubric.load()]
        progress.start("evaluate", f"Re-evaluating {len(rubric.all_checks())} checks")
        findings: list[Finding] = []
        for index, framework_key in enumerate(frameworks, 1):
            framework = next(f for f in rubric.load() if f.key == framework_key)
            progress.detail(
                "evaluate", f"{framework.name} ({index} of {len(frameworks)})"
            )
            framework_findings, usage = stages.evaluate(
                design,
                classification,
                framework_key,
                feedback=feedback,
                reference_graph=reference_graph,
            )
            findings.extend(framework_findings)
            progress.record_usage(usage)

        # ---- the one-way AI-applicability gate ------------------------------- #
        #
        # Between evaluate and prioritize deliberately: the gate's output is a
        # `not_applicable` status, and everything downstream — prioritize, remediate,
        # the roadmap, scoring — reads statuses. Running it here means one code path
        # produces the statuses and every consumer sees the same set.
        #
        # It can ONLY mark an AI-conditional check not_applicable, only when
        # detection says absent/denied, and never over an evidence-bearing verdict.
        # See agent/ai_gate.py for why each of those guards is there.
        gated = ai_gate.apply(findings, detection)
        gate_note = ""
        if gated:
            # Carried into evaluate's FINAL detail line rather than shown mid-stage
            # and then overwritten by it. A status the code set, not the model, is
            # exactly the kind of thing this project does not do silently — and the
            # transient version of this message was invisible by the time the stage
            # finished, which is the same as not sending it.
            gate_note = (
                f"; {len(gated)} AI-specific checks marked not applicable — "
                f"no AI/ML component detected"
            )
            progress.detail("evaluate", gate_note.lstrip("; "))

        open_count = sum(1 for f in findings if f.status in ("fail", "partial"))
        progress.finish(
            "evaluate",
            f"{open_count} gaps found across {len(findings)} checks{gate_note}",
        )

        # ---- prioritize ----------------------------------------------------- #
        stage = "prioritize"
        progress.start("prioritize", "Ranking findings")
        ranking_payload, usage = stages.prioritize(findings, classification)
        progress.record_usage(usage)
        ranked, backfilled = stages.apply_ranking(
            findings, ranking_payload.get("ranking", [])
        )
        detail = f"{ranked + backfilled} findings ranked"
        if backfilled:
            detail += f" ({ranked} by the model, {backfilled} by severity)"
        progress.finish("prioritize", detail)

        overall, framework_scores = scoring.score(findings)

        # ---- remediate — ALWAYS ---------------------------------------------- #
        stage = "remediate"
        progress.start("remediate", "Regenerating remediation and summary")
        (
            remediations,
            efforts,
            executive_summary,
            use_case_notes,
            usage,
            grounding,
            remediation_quotes,
        ) = stages.remediate(
            findings,
            classification,
            scoring.scoreboard(overall, framework_scores, findings),
            context=design.context,
            feedback=feedback,
            reference_graph=reference_graph,
            # A follow-up round grounds against the design it actually re-evaluated:
            # for a feedback-only round that is the stored design, and for a new
            # attachment it is the replaced one. Either way it is `design`, which is
            # exactly what evaluate was given this round.
            design=design,
        )
        progress.record_usage(usage)
        design.fidelity.grounding = grounding
        for finding in findings:
            finding.remediation = remediations.get(finding.check_id, "")
            finding.remediation_effort = efforts.get(finding.check_id, "")  # type: ignore[assignment]
            # Empty unless the quote was verified present in the design source.
            finding.remediation_grounded_in = remediation_quotes.get(finding.check_id, "")
        progress.finish("remediate", f"{len(remediations)} remediations written")

        # ---- persist as a NEW version --------------------------------------- #
        findings.sort(key=lambda f: (f.priority == 0, f.priority))

        result = ReviewResult(
            review_id=review_id,
            created_at=_now(),
            title=design.title,
            overall_score=overall,
            frameworks=framework_scores,
            findings=findings,
            components=stages.classified_components(classification),
            summary=ranking_payload.get("summary", ""),
            executive_summary=executive_summary,
            context=design.context,
            # The diagram this VERSION was scored against — the new one when an
            # attachment replaced it, otherwise the base's, so the PDF appendix and
            # the share view keep working on either shape.
            diagram_key=diagram_key or base.diagram_key,
            warnings=design.warnings,
            graph=design.graph,
            document_text=design.document_text,
            classification=classification,
            use_case_notes=use_case_notes,
            fidelity=design.fidelity,
            ai_detection=detection,
            token_usage=progress.usage_totals(),
            # ---- the version linkage ---------------------------------------- #
            version=base.version + 1,
            # The chain's identity. The base's root when it has one, otherwise the
            # base itself — which is how version 2 establishes the root that every
            # later version inherits.
            root_review_id=base.root_review_id or base.review_id,
            based_on_review_id=base.review_id,
            feedback=feedback,
        )

        # A delta against the version this round started from, so "what did my
        # feedback change" is answerable without diffing two records by hand. Free:
        # `scoring.delta` is the same arithmetic the re-analyze flow already uses.
        result.delta = scoring.delta(base, result)

        cancel.check(review_id)
        storage.put_review(result)
        progress.complete()
        return result

    except relevance.NotReviewable:
        # `progress.rejected` has already written the terminal status. Re-raised so
        # the caller knows the round did not run — and note what has NOT happened:
        # no result was stored, so the base review and every earlier version are
        # exactly as they were.
        raise
    except cancel.Cancelled:
        log.info("Re-review %s cancelled during %s", review_id, stage)
        progress.cancelled(stage)
        raise
    except Exception as exc:  # noqa: BLE001 — recorded for the UI, then re-raised
        log.exception("Re-review %s failed during %s", review_id, stage)
        progress.fail(stage, f"{type(exc).__name__}: {exc}")
        raise


def _ingest_new_attachment(
    *,
    review_id: str,
    base: ReviewResult,
    progress: _Progress,
    document_key: str,
    diagram_key: str,
) -> tuple[NormalizedDesign, object | None, dict]:
    """Ingest, screen and classify the NEW attachment only.

    Returns the design to evaluate, the previous graph when it was replaced (as
    reference for the prompt, nothing more), the fresh classification, and the token
    usage of each call made.

    The replacement rule, stated once:

    * a new attachment that yields a graph REPLACES the previous graph outright, and
      the previous one becomes read-only reference context;
    * a new attachment that yields document text REPLACES the previous text;
    * a surface the new attachment did not provide carries forward from the base
      unchanged — a new diagram does not erase the SoW that was reviewed with it.

    Nothing is structurally merged. Two graphs are never combined into one, which is
    the failure mode that would let a stale component from an old diagram be scored
    as though it were still in the design.
    """

    progress.start("ingest", "Reading the new attachment")
    fresh, ingest_usage = normalize.ingest(
        review_id=review_id,
        title=base.title,
        document_key=document_key,
        diagram_key=diagram_key,
        # Carried forward verbatim: it is the submitter's own purpose statement for
        # the same system, and it is fenced at every prompt exactly as before.
        context=base.context,
    )
    progress.record_usage(ingest_usage)
    progress.finish(
        "ingest",
        f"{len(fresh.graph.components)} components from {fresh.graph.source.value}",
    )

    # The new attachment replaced the graph only if it actually produced one.
    replaced_graph = bool(fresh.graph.components or fresh.graph.notes)
    reference_graph = base.graph if replaced_graph else None

    design = NormalizedDesign(
        review_id=review_id,
        title=base.title,
        document_text=fresh.document_text or base.document_text,
        graph=fresh.graph if replaced_graph else (base.graph or DesignGraph()),
        context=base.context,
        # Warnings and fidelity are this round's own measurements, on this round's
        # attachment. The base's are not restated.
        warnings=fresh.warnings,
        fidelity=fresh.fidelity,
    )

    progress.start("normalize", "Converging the new attachment on the common schema")
    progress.finish(
        "normalize",
        f"{len(design.document_text)} characters of document text"
        + (", diagram replaced" if replaced_graph else ", diagram carried forward"),
    )
    progress.warn(design.warnings)

    # ---- the same relevance gate the original upload went through ----------- #
    #
    # No exception for a re-review: a new attachment is an upload like any other, and
    # "it is a follow-up" is not evidence that a photograph of a cat is a design. A
    # refusal here stops the round before evaluate, so it costs one model call rather
    # than five, and leaves every existing version untouched.
    progress.start("screen", "Checking the new attachment is a solution design")
    assessment, usage = relevance.screen(design)
    progress.record_usage(usage)

    if assessment is None:
        progress.finish("screen", "Relevance check unavailable — continuing")
    elif assessment.rejects:
        message = relevance.rejection_message(assessment)
        log.info(
            "Re-review %s rejected by the relevance gate: verdict=%s confidence=%s "
            "subject=%r",
            review_id, assessment.verdict, assessment.confidence, assessment.subject,
        )
        progress.rejected("screen", message)
        raise relevance.NotReviewable(message)
    elif assessment.verdict == "reviewable":
        progress.finish(
            "screen",
            f"Recognised as {assessment.subject}" if assessment.subject else "Confirmed",
        )
    else:
        design.warnings.append(relevance.uncertainty_warning(assessment))
        progress.warn(design.warnings)
        progress.finish(
            "screen",
            f"Not confirmed as a design ({assessment.confidence} confidence) "
            f"— continuing with a warning",
        )

    # ---- classify the new material ----------------------------------------- #
    progress.start("classify", "Classifying the revised design")
    classification, usage = stages.classify(design)
    progress.record_usage(usage)
    components = stages.classified_components(classification)
    progress.finish("classify", f"{len(components)} components classified")

    return design, reference_graph, classification


def _classification_from(base: ReviewResult) -> dict:
    """A stand-in classification for a review stored before one was retained.

    Deliberately thin, and it loses `absent` — which the evaluate prompt calls the
    most important part of the inventory, because it is what lets the stage tell
    silence apart from absence. So this is a fallback for older records rather than
    a supported path, and `_resume_design` already refuses the records where it
    would matter most (no graph and no document text at all).
    """
    return {
        "design_summary": base.summary or base.executive_summary or "",
        "components": [
            {
                "id": component.id,
                "label": component.label,
                "kind": component.kind,
                "provider": component.provider,
                "service": component.service,
                "attributes": [
                    {"name": name, "value": value}
                    for name, value in component.attributes.items()
                ],
            }
            for component in base.components
        ],
        "data_flows": [],
        "observations": [],
        "absent": [],
    }
