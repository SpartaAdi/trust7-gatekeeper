"""Scoring is arithmetic, not a model call — which is what makes deltas trustworthy."""

from __future__ import annotations

import rubric
import scoring
from schema import Finding, ReviewResult


def _finding(check_id: str, status: str, severity: str | None = None) -> Finding:
    check = rubric.checks_by_id()[check_id]
    return Finding(
        framework=check.framework,
        pillar_id=check.pillar_id,
        check_id=check_id,
        status=status,  # type: ignore[arg-type]
        severity=severity or check.severity,  # type: ignore[arg-type]
        title=check.description,
    )


def _all(status: str) -> list[Finding]:
    return [_finding(c.check_id, status) for c in rubric.all_checks()]


def test_rubric_loads_both_frameworks() -> None:
    keys = {f.key for f in rubric.load()}
    assert keys == {"aws_waf", "trust7"}
    assert len(rubric.all_checks()) == 45


def test_all_pass_scores_100() -> None:
    overall, frameworks = scoring.score(_all("pass"))
    assert overall == 100.0
    assert all(f.score == 100.0 for f in frameworks)


def test_all_fail_scores_zero() -> None:
    overall, _ = scoring.score(_all("fail"))
    assert overall == 0.0


def test_partial_earns_half_credit() -> None:
    overall, _ = scoring.score(_all("partial"))
    assert overall == 50.0


def test_not_applicable_is_excluded_not_scored() -> None:
    """An inapplicable check must neither help nor hurt the score."""
    pillar = rubric.load()[0].pillars[0]
    findings = [_finding(c.check_id, "pass") for c in pillar.checks[1:]]
    findings.append(_finding(pillar.checks[0].check_id, "not_applicable"))

    _, frameworks = scoring.score(findings)
    scored = next(
        p for p in frameworks[0].pillars if p.pillar_id == pillar.pillar_id
    )
    assert scored.score == 100.0
    assert scored.checks_evaluated == len(pillar.checks) - 1
    assert scored.checks_total == len(pillar.checks)


def test_high_severity_failures_cost_more_than_low() -> None:
    pillar = next(
        p
        for p in rubric.load()[0].pillars
        if {c.severity for c in p.checks} >= {"high", "medium"}
    )
    high = next(c for c in pillar.checks if c.severity == "high")
    medium = next(c for c in pillar.checks if c.severity == "medium")

    def score_with_failure(failed_id: str) -> float:
        findings = [
            _finding(c.check_id, "fail" if c.check_id == failed_id else "pass")
            for c in pillar.checks
        ]
        _, frameworks = scoring.score(findings)
        return next(
            p.score for p in frameworks[0].pillars if p.pillar_id == pillar.pillar_id
        )

    assert score_with_failure(high.check_id) < score_with_failure(medium.check_id)


def _result(review_id: str, findings: list[Finding]) -> ReviewResult:
    overall, frameworks = scoring.score(findings)
    return ReviewResult(
        review_id=review_id,
        created_at="2026-07-26T00:00:00Z",
        overall_score=overall,
        frameworks=frameworks,
        findings=findings,
    )


def test_delta_reports_improvement_and_resolved_checks() -> None:
    check_id = rubric.all_checks()[0].check_id
    before = _result("prev", _all("fail"))
    after_findings = _all("fail")
    for finding in after_findings:
        if finding.check_id == check_id:
            finding.status = "pass"
    after = _result("curr", after_findings)

    delta = scoring.delta(before, after)

    assert delta.previous_review_id == "prev"
    assert delta.change > 0
    assert check_id in delta.resolved_checks
    assert check_id not in delta.unchanged_failures


def test_delta_reports_regressions_as_new_checks() -> None:
    check_id = rubric.all_checks()[0].check_id
    before = _result("prev", _all("pass"))
    after_findings = _all("pass")
    for finding in after_findings:
        if finding.check_id == check_id:
            finding.status = "fail"
    after = _result("curr", after_findings)

    delta = scoring.delta(before, after)

    assert delta.change < 0
    assert check_id in delta.new_checks
    assert delta.resolved_checks == []


def test_delta_is_zero_for_an_unchanged_design() -> None:
    before = _result("prev", _all("partial"))
    after = _result("curr", _all("partial"))

    delta = scoring.delta(before, after)

    assert delta.change == 0.0
    assert delta.resolved_checks == []
    assert delta.new_checks == []
    assert len(delta.unchanged_failures) == 45
    assert all(p.change == 0.0 for p in delta.pillars)
