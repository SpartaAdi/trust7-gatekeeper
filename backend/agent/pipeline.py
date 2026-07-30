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
import llm
import rubric
import scoring
import storage
from agent import stages
from ingestion import normalize, relevance
from schema import Finding, IngestWarning, ReviewResult, ReviewStatus

log = logging.getLogger(__name__)


class _Progress:
    """Writes stage transitions to the status table the UI polls."""

    def __init__(self, status: ReviewStatus) -> None:
        self._status = status

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

    def rejected(self, stage: str, message: str) -> None:
        """Record the screen stage refusing the upload.

        Not `fail`, for the same reason `cancelled` is not: nothing malfunctioned.
        The message goes in `rejection` rather than `error` so the UI can present it
        as "we did not review this, and here is why" instead of under a "Pipeline
        error" heading that sends the submitter hunting for a fault.
        """
        self._status.state = "rejected"
        self._status.error = ""
        self._status.rejection = message
        for entry in self._status.stages:
            if entry.name == stage:
                entry.state = "rejected"
                entry.detail = "Not a solution design — not reviewed"
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
    usages: list[dict[str, int]] = []
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
        usages.append(ingest_usage)
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
        usages.append(usage)

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
        usages.append(usage)
        components = stages.classified_components(classification)
        # An empty inventory against a non-empty design is worth saying out loud on
        # the screen the reviewer is watching. Ingest has already reported what it
        # found, so "0 components classified" on its own reads as a contradiction the
        # user has to work out for themselves.
        detail = f"{len(components)} components classified"
        if not components and stages.design_has_content(design):
            detail = (
                f"0 components classified despite "
                f"{len(design.graph.components)} in the diagram — the review "
                f"continues from the design text"
            )
        progress.finish("classify", detail)

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
            usages.append(usage)
        open_count = sum(1 for f in findings if f.status in ("fail", "partial"))
        progress.finish("evaluate", f"{open_count} gaps found across {len(findings)} checks")

        # ---- prioritize ----------------------------------------------------- #
        stage = "prioritize"
        progress.start("prioritize", "Ranking findings")
        ranking_payload, usage = stages.prioritize(findings, classification)
        usages.append(usage)
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
        ) = stages.remediate(
            findings,
            classification,
            scoring.scoreboard(overall, framework_scores, findings),
            context=design.context,
        )
        # The third fidelity number, and the only one not measured at ingestion:
        # the grounding filter can only be counted where it runs. Left as None when
        # the stage made no call, so "not measured" stays distinct from "0 caught".
        design.fidelity.grounding = grounding
        usages.append(usage)
        for finding in findings:
            finding.remediation = remediations.get(finding.check_id, "")
            finding.remediation_effort = efforts.get(finding.check_id, "")  # type: ignore[assignment]
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
            token_usage=_sum(usages),
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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
