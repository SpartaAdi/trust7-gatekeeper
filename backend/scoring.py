"""Deterministic scoring and score-delta computation.

Scoring is arithmetic on the findings, not a model call — the same findings
always produce the same score, which is what makes a re-review delta meaningful.
"""

from __future__ import annotations

import rubric
from schema import (
    Finding,
    FrameworkScore,
    PillarDelta,
    PillarScore,
    ReviewResult,
    ScoreDelta,
)

# Credit awarded per check status. `not_applicable` is excluded from the
# denominator rather than scored, so an irrelevant check neither helps nor hurts.
STATUS_CREDIT: dict[str, float] = {"pass": 1.0, "partial": 0.5, "fail": 0.0}


def score(findings: list[Finding]) -> tuple[float, list[FrameworkScore]]:
    """Roll findings up into pillar, framework, and overall scores (0-100)."""
    by_check = {f.check_id: f for f in findings}
    framework_scores: list[FrameworkScore] = []

    for fw in rubric.load():
        pillar_scores: list[PillarScore] = []
        for pillar in fw.pillars:
            earned = 0.0
            possible = 0.0
            evaluated = 0
            passed = 0
            for check in pillar.checks:
                finding = by_check.get(check.check_id)
                if finding is None or finding.status == "not_applicable":
                    continue
                weight = rubric.SEVERITY_WEIGHT.get(finding.severity, 2.0)
                earned += weight * STATUS_CREDIT.get(finding.status, 0.0)
                possible += weight
                evaluated += 1
                if finding.status == "pass":
                    passed += 1
            pillar_scores.append(
                PillarScore(
                    framework=fw.key,
                    pillar_id=pillar.pillar_id,
                    pillar_name=pillar.name,
                    score=round(100.0 * earned / possible, 1) if possible else 0.0,
                    checks_total=len(pillar.checks),
                    checks_evaluated=evaluated,
                    checks_passed=passed,
                )
            )

        # Pillars are weighted equally within a framework: a framework is only as
        # sound as its weakest pillar, and check counts differ per pillar for
        # reasons unrelated to importance.
        scored = [p for p in pillar_scores if p.checks_evaluated]
        framework_scores.append(
            FrameworkScore(
                framework=fw.key,
                framework_name=fw.name,
                score=round(sum(p.score for p in scored) / len(scored), 1) if scored else 0.0,
                pillars=pillar_scores,
            )
        )

    # Both frameworks count equally toward the overall score.
    scored_fw = [f for f in framework_scores if any(p.checks_evaluated for p in f.pillars)]
    overall = round(sum(f.score for f in scored_fw) / len(scored_fw), 1) if scored_fw else 0.0
    return overall, framework_scores


def delta(previous: ReviewResult, current: ReviewResult) -> ScoreDelta:
    """Compare a revised design's review against the prior one."""
    prev_pillars = {
        (p.framework, p.pillar_id): p for f in previous.frameworks for p in f.pillars
    }
    pillar_deltas: list[PillarDelta] = []
    for framework in current.frameworks:
        for pillar in framework.pillars:
            before = prev_pillars.get((pillar.framework, pillar.pillar_id))
            if before is None:
                continue
            pillar_deltas.append(
                PillarDelta(
                    framework=pillar.framework,
                    pillar_id=pillar.pillar_id,
                    pillar_name=pillar.pillar_name,
                    previous_score=before.score,
                    current_score=pillar.score,
                    change=round(pillar.score - before.score, 1),
                )
            )

    def failing(result: ReviewResult) -> set[str]:
        return {f.check_id for f in result.findings if f.status in ("fail", "partial")}

    before_failing = failing(previous)
    now_failing = failing(current)

    return ScoreDelta(
        previous_review_id=previous.review_id,
        previous_overall_score=previous.overall_score,
        current_overall_score=current.overall_score,
        change=round(current.overall_score - previous.overall_score, 1),
        pillars=pillar_deltas,
        resolved_checks=sorted(before_failing - now_failing),
        new_checks=sorted(now_failing - before_failing),
        unchanged_failures=sorted(before_failing & now_failing),
    )
