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


# --------------------------------------------------------------------------- #
# Executive-summary scoreboard
# --------------------------------------------------------------------------- #


def test_scoreboard_reports_strongest_and_weakest_evaluated_pillars() -> None:
    findings = _all("pass")
    pillar = rubric.load()[0].pillars[0]
    for finding in findings:
        if finding.pillar_id == pillar.pillar_id:
            finding.status = "fail"

    overall, frameworks = scoring.score(findings)
    board = scoring.scoreboard(overall, frameworks, findings)

    assert f"Weakest pillar: {pillar.name} (0.0)" in board
    assert "Strongest pillar:" in board
    assert "High-severity findings still open:" in board


def test_scoreboard_ignores_unevaluated_pillars_when_picking_weakest() -> None:
    """An unevaluated pillar scores 0.0 and would otherwise always be 'weakest'."""
    skipped = rubric.load()[0].pillars[0]
    findings = [
        _finding(c.check_id, "not_applicable" if c.pillar_id == skipped.pillar_id else "pass")
        for c in rubric.all_checks()
    ]

    overall, frameworks = scoring.score(findings)
    board = scoring.scoreboard(overall, frameworks, findings)

    assert skipped.name not in board


def test_scoreboard_counts_only_open_high_severity_findings() -> None:
    findings = _all("pass")
    high = next(f for f in findings if f.severity == "high")
    high.status = "fail"

    overall, frameworks = scoring.score(findings)
    assert "High-severity findings still open: 1" in scoring.scoreboard(
        overall, frameworks, findings
    )


# --------------------------------------------------------------------------- #
# `confidence` is display only
#
# It is the model's self-report about its own certainty. Letting it touch the
# arithmetic would make the score non-deterministic in the one place this tool has
# to be defensible: two runs over an identical design could produce different
# numbers, and a reviewer could not reproduce a score from the rubric. These tests
# exist so that wiring it in fails loudly rather than looking like a refinement.
# --------------------------------------------------------------------------- #

def test_confidence_does_not_change_any_score() -> None:
    """Same verdicts, every confidence level: byte-identical scores."""
    baseline = _all("fail")
    scores = []
    for level in ("high", "medium", "low", ""):
        perturbed = [f.model_copy(update={"confidence": level}) for f in baseline]
        overall, frameworks = scoring.score(perturbed)
        scores.append((overall, [(fw.framework, fw.score,
                                 tuple((p.pillar_id, p.score) for p in fw.pillars))
                                for fw in frameworks]))

    assert len(set(map(repr, scores))) == 1, (
        "confidence changed a score — it must never reach the arithmetic"
    )


def test_confidence_does_not_change_a_delta() -> None:
    before = _result("prev", _all("fail"))

    resolved = _all("pass")
    plain = scoring.delta(before, _result("curr", resolved))

    # The same improvement, but every finding now reports low confidence.
    hedged = [f.model_copy(update={"confidence": "low"}) for f in resolved]
    with_confidence = scoring.delta(before, _result("curr", hedged))

    assert plain.change == with_confidence.change
    assert plain.resolved_checks == with_confidence.resolved_checks


def test_scoring_never_reads_the_field_at_all() -> None:
    """A grep, deliberately: the tests above pass if the read is a no-op today,
    and this fails the moment someone references the field at all."""
    import pathlib

    source = pathlib.Path("scoring.py").read_text()
    assert "confidence" not in source, (
        "scoring.py references `confidence`; it must stay out of the arithmetic"
    )
