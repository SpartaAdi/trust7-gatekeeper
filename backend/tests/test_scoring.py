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


# --------------------------------------------------------------------------- #
# A WHOLLY inapplicable pillar
#
# The single-check case above is the easy one: the pillar still has other checks,
# so it still has a denominator. This is the case where the denominator itself
# disappears — every check in a pillar comes back `not_applicable`, which is the
# NORMAL outcome for a design with no AI component at all, since six of TRUST-7's
# seven pillars ask exclusively about AI. `fixtures/ground_truth/expense-portal.json`
# is labelled that way for exactly this reason.
#
# Two failure modes are being excluded, and they are different:
#
#   * counted as FAILED — the pillar contributes 0 to the framework average,
#     which on a 7-pillar framework costs 14 points per wholly-N/A pillar and
#     would put a clean non-AI design near zero on TRUST-7;
#   * counted at all — even "excluded from the numerator but present in the
#     denominator" is the same arithmetic error wearing a different hat.
#
# `PillarScore.score` for such a pillar is 0.0, and that 0.0 is a SENTINEL, not a
# score: `scoring.score` divides by `possible` only when it is non-zero and
# returns 0.0 otherwise. The only thing that tells a sentinel apart from a
# genuinely-zero pillar is `checks_evaluated == 0`, and every consumer downstream
# reads that field before the number — ResultsView.tsx, HistoryView.tsx, report.py
# and `scoring.scoreboard` all branch on it. The last test here pins that
# invariant, because if a sentinel ever ships with a non-zero `checks_evaluated`
# the pillar silently becomes a red 0 on the heatmap and in the PDF.
# --------------------------------------------------------------------------- #

def _wholly_inapplicable_pillar() -> tuple[str, str]:
    """A (framework_key, pillar_id) to mark entirely not_applicable.

    Picked from TRUST-7 rather than hardcoded: these pillars are the ones a
    non-AI design legitimately renders wholly inapplicable, and picking by
    position survives a rubric edit that renames or reorders them.
    """
    framework = next(f for f in rubric.load() if f.key == "trust7")
    return framework.key, framework.pillars[0].pillar_id


def _pillar(frameworks: list, framework_key: str, pillar_id: str):
    return next(
        p
        for f in frameworks
        if f.framework == framework_key
        for p in f.pillars
        if p.pillar_id == pillar_id
    )


def test_a_wholly_inapplicable_pillar_leaves_the_framework_score_untouched() -> None:
    """Every check in one pillar is n/a; every other check passes.

    The framework must score 100.0. Anything less means the pillar reached the
    denominator — at 7 pillars, counting it as a 0 would give 85.7.
    """
    framework_key, pillar_id = _wholly_inapplicable_pillar()
    findings = [
        _finding(c.check_id, "not_applicable" if c.pillar_id == pillar_id else "pass")
        for c in rubric.all_checks()
    ]

    overall, frameworks = scoring.score(findings)
    framework = next(f for f in frameworks if f.framework == framework_key)

    assert framework.score == 100.0, (
        f"{framework_key} scored {framework.score} with one wholly-inapplicable "
        f"pillar and everything else passing; the n/a pillar is in the denominator"
    )
    assert overall == 100.0


def test_a_wholly_inapplicable_pillar_scores_the_same_as_one_that_is_absent() -> None:
    """The control: the same design with those checks simply not returned.

    This is the assertion that cannot be satisfied by accident. "Excluded from the
    denominator entirely" means the arithmetic is identical whether the pillar came
    back all-n/a or was never evaluated, at pillar, framework and overall level.
    """
    framework_key, pillar_id = _wholly_inapplicable_pillar()
    other_checks = [c for c in rubric.all_checks() if c.pillar_id != pillar_id]

    # Something must actually fail, or every arrangement scores 100 and the test
    # would pass on an implementation that averaged nothing at all.
    failing = {other_checks[0].check_id, other_checks[1].check_id}

    def status(check_id: str) -> str:
        return "fail" if check_id in failing else "pass"

    all_na = [_finding(c.check_id, "not_applicable") for c in rubric.all_checks()
              if c.pillar_id == pillar_id]
    all_na += [_finding(c.check_id, status(c.check_id)) for c in other_checks]
    absent = [_finding(c.check_id, status(c.check_id)) for c in other_checks]

    na_overall, na_frameworks = scoring.score(all_na)
    absent_overall, absent_frameworks = scoring.score(absent)

    assert na_overall == absent_overall
    assert (
        next(f for f in na_frameworks if f.framework == framework_key).score
        == next(f for f in absent_frameworks if f.framework == framework_key).score
    )


def test_a_wholly_inapplicable_pillar_does_not_drag_down_a_failing_framework() -> None:
    """The zero-contribution error hides when the real score is already low.

    With the rest of the framework failing, both the correct answer and the buggy
    one are small numbers. The framework average must still be taken over the
    pillars that were evaluated, so it must equal their mean exactly.
    """
    framework_key, pillar_id = _wholly_inapplicable_pillar()
    framework = next(f for f in rubric.load() if f.key == framework_key)

    findings = [
        _finding(
            c.check_id,
            "not_applicable" if c.pillar_id == pillar_id
            else ("partial" if c.framework == framework_key else "pass"),
        )
        for c in rubric.all_checks()
    ]

    _, frameworks = scoring.score(findings)
    scored = next(f for f in frameworks if f.framework == framework_key)
    evaluated = [p for p in scored.pillars if p.pillar_id != pillar_id]

    assert len(evaluated) == len(framework.pillars) - 1
    assert scored.score == round(
        sum(p.score for p in evaluated) / len(evaluated), 1
    ), (
        f"{framework_key} averaged over {len(framework.pillars)} pillars instead of "
        f"the {len(evaluated)} that were evaluated"
    )
    assert scored.score == 50.0


def test_a_wholly_inapplicable_pillar_is_not_recorded_as_failed() -> None:
    """The counts, not the average. Nothing in this pillar was assessed at all."""
    framework_key, pillar_id = _wholly_inapplicable_pillar()
    pillar = next(
        p for f in rubric.load() if f.key == framework_key
        for p in f.pillars if p.pillar_id == pillar_id
    )
    findings = [
        _finding(c.check_id, "not_applicable" if c.pillar_id == pillar_id else "pass")
        for c in rubric.all_checks()
    ]

    _, frameworks = scoring.score(findings)
    scored = _pillar(frameworks, framework_key, pillar_id)

    assert scored.checks_evaluated == 0
    assert scored.checks_passed == 0
    assert scored.checks_total == len(pillar.checks)
    # No finding in it counts as an open gap either — the roadmap, the "fix these
    # first" callout and the high-severity count all read `status`, and these
    # checks are high-severity by default in the rubric.
    assert not [
        f for f in findings
        if f.pillar_id == pillar_id and f.status in ("fail", "partial")
    ]


def test_a_zero_pillar_score_is_only_ever_a_sentinel_when_nothing_was_evaluated() -> None:
    """The invariant every consumer downstream depends on.

    `score == 0.0` is overloaded: it is both "this pillar failed everything" and
    "this pillar had nothing to evaluate". ResultsView.tsx, HistoryView.tsx,
    report.py and `scoring.scoreboard` all disambiguate on `checks_evaluated`, so
    the two must never be able to disagree — a wholly-n/a pillar reporting a
    non-zero `checks_evaluated` would render as a red 0 on the heatmap and in the
    PDF while the arithmetic quietly did the right thing.
    """
    framework_key, pillar_id = _wholly_inapplicable_pillar()
    findings = [
        _finding(c.check_id, "not_applicable" if c.pillar_id == pillar_id else "fail")
        for c in rubric.all_checks()
    ]

    _, frameworks = scoring.score(findings)

    for framework in frameworks:
        for pillar in framework.pillars:
            if pillar.pillar_id == pillar_id:
                assert pillar.score == 0.0 and pillar.checks_evaluated == 0
            else:
                # A genuine zero, and it must remain distinguishable: it carries a
                # non-zero evaluated count.
                assert pillar.score == 0.0 and pillar.checks_evaluated > 0


def test_every_trust7_pillar_being_inapplicable_excludes_the_whole_framework() -> None:
    """The real shape of a non-AI design, taken to its conclusion.

    `fixtures/ground_truth/expense-portal.json` labels 18 of TRUST-7's 19 checks
    n/a. This is the same case with the last one included: TRUST-7 has nothing to
    say, so the overall score must be the AWS WAF score alone rather than an
    average dragged halfway to zero.
    """
    findings = [
        _finding(c.check_id, "not_applicable" if c.framework == "trust7" else "pass")
        for c in rubric.all_checks()
    ]

    overall, frameworks = scoring.score(findings)
    waf = next(f for f in frameworks if f.framework == "aws_waf")

    assert waf.score == 100.0
    assert overall == waf.score, (
        f"overall {overall} is not the AWS WAF score {waf.score}; a framework with "
        f"nothing applicable was averaged in as a zero"
    )


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
