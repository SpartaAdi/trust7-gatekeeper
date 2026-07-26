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

import rubric
import scoring
import storage
from agent import stages
from ingestion import normalize
from schema import Finding, ReviewResult, ReviewStatus

log = logging.getLogger(__name__)


class _Progress:
    """Writes stage transitions to the status table the UI polls."""

    def __init__(self, status: ReviewStatus) -> None:
        self._status = status

    def start(self, stage: str, detail: str = "") -> None:
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

    def complete(self) -> None:
        self._status.state = "complete"
        storage.put_status(self._status)


def run(
    *,
    review_id: str,
    title: str = "",
    document_key: str = "",
    diagram_key: str = "",
    previous_review_id: str = "",
) -> ReviewResult:
    """Run a full review and persist the result. Raises on failure."""
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

        # ---- classify ------------------------------------------------------- #
        stage = "classify"
        progress.start("classify", "Classifying components")
        classification, usage = stages.classify(design)
        usages.append(usage)
        components = stages.classified_components(classification)
        progress.finish("classify", f"{len(components)} components classified")

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
        ranks = {
            item["check_id"]: item["rank"]
            for item in ranking_payload.get("ranking", [])
            if item.get("check_id")
        }
        for finding in findings:
            finding.priority = ranks.get(finding.check_id, 0)
        progress.finish("prioritize", f"{len(ranks)} findings ranked")

        # ---- remediate ------------------------------------------------------ #
        stage = "remediate"
        progress.start("remediate", "Generating remediation")
        remediations, efforts, usage = stages.remediate(findings, classification)
        usages.append(usage)
        for finding in findings:
            finding.remediation = remediations.get(finding.check_id, "")
            finding.remediation_effort = efforts.get(finding.check_id, "")  # type: ignore[assignment]
        progress.finish("remediate", f"{len(remediations)} remediations written")

        # ---- score, delta, persist ------------------------------------------ #
        overall, framework_scores = scoring.score(findings)
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

        storage.put_review(result)
        progress.complete()
        return result

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
