"""Phase-grouping tests.

The shared cases in ``fixtures/roadmap_cases.json`` are asserted here AND in
``frontend/src/views/roadmap.test.ts``. That file is the only thing keeping the two
implementations of one rule from drifting, so a case added there must pass in both
suites — otherwise the PDF and the results view can disagree about which phase a
finding belongs to, and nothing would catch it.
"""

from __future__ import annotations

import itertools
import json
import pathlib

import pytest

import roadmap
from schema import Finding

_CASES = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "roadmap_cases.json")
    .read_text()
)


def finding(**overrides) -> Finding:
    base = {
        "framework": "aws_waf",
        "pillar_id": "security",
        "check_id": "SEC-1",
        "status": "fail",
        "severity": "medium",
        "title": "A gap",
        "affected_components": [],
        "remediation": "",
        "remediation_effort": "",
        "priority": 0,
    }
    return Finding(**{**base, **overrides})


SHARED = [(c["name"], c["expect"], finding(**c["finding"])) for c in _CASES["cases"]]


@pytest.mark.parametrize(
    "expected,item", [(e, f) for _, e, f in SHARED], ids=[n for n, _, _ in SHARED]
)
def test_shared_cases(expected: str, item: Finding) -> None:
    assert roadmap.phase_for(item) == expected


def test_high_effort_is_structural_at_any_severity() -> None:
    for severity in ("high", "medium", "low"):
        assert roadmap.phase_for(finding(remediation_effort="high", severity=severity)) == (
            "structural"
        )


def test_two_or_more_components_is_structural() -> None:
    assert roadmap.phase_for(
        finding(remediation_effort="low", affected_components=["a", "b"])
    ) == "structural"
    # One component is not a span.
    assert roadmap.phase_for(
        finding(remediation_effort="low", severity="high", affected_components=["a"])
    ) == "immediate"


def test_immediate_needs_both_cheap_and_high_severity() -> None:
    assert roadmap.phase_for(finding(remediation_effort="low", severity="high")) == "immediate"
    # Cheap but not urgent.
    assert roadmap.phase_for(finding(remediation_effort="low", severity="medium")) == (
        "short_term"
    )
    # Urgent but not cheap.
    assert roadmap.phase_for(finding(remediation_effort="medium", severity="high")) == (
        "short_term"
    )


def test_absent_effort_is_never_treated_as_low() -> None:
    assert roadmap.phase_for(finding(remediation_effort="", severity="medium")) == "short_term"
    assert roadmap.phase_for(finding(remediation_effort="", severity="low")) == "short_term"


def test_pillar_does_not_move_a_finding() -> None:
    # The rejected alternative was routing Operational Excellence and Reliability to
    # structural; both pillars contain some of the cheapest fixes in the rubric.
    phases = {
        roadmap.phase_for(
            finding(remediation_effort="low", severity="high", pillar_id=pillar)
        )
        for pillar in (
            "security",
            "reliability",
            "operational_excellence",
            "cost_optimization",
        )
    }
    assert phases == {"immediate"}


def test_partitions_open_findings_without_dropping_or_duplicating() -> None:
    items = [f for _, _, f in SHARED]
    grouped = roadmap.group_by_phase(items)
    placed = [f.check_id for phase in roadmap.PHASE_ORDER for f in grouped[phase]]

    assert len(placed) == len(items)
    assert len(set(placed)) == len(items)
    assert sorted(placed) == sorted(f.check_id for f in items)


def test_closed_checks_are_not_on_the_roadmap() -> None:
    items = [f for _, _, f in SHARED]
    closed = [finding(**c["finding"]) for c in _CASES["excluded"]]
    grouped = roadmap.group_by_phase(items + closed)
    placed = [f.check_id for phase in roadmap.PHASE_ORDER for f in grouped[phase]]

    for item in closed:
        assert item.check_id not in placed
    assert len(placed) == len(items)


def test_no_cap_this_is_the_whole_plan() -> None:
    many = [
        finding(check_id=f"SEC-{i}", remediation_effort="medium") for i in range(23)
    ]
    assert len(roadmap.group_by_phase(many)["short_term"]) == 23


def test_orders_each_phase_by_priority_unranked_last() -> None:
    grouped = roadmap.group_by_phase(
        [
            finding(check_id="C", remediation_effort="medium", priority=0),
            finding(check_id="A", remediation_effort="medium", priority=7),
            finding(check_id="B", remediation_effort="medium", priority=2),
        ]
    )
    assert [f.check_id for f in grouped["short_term"]] == ["B", "A", "C"]


def _signature(grouped: dict) -> str:
    return "|".join(
        f"{phase}:" + ",".join(f.check_id for f in grouped[phase])
        for phase in roadmap.PHASE_ORDER
    )


def test_identical_grouping_when_run_twice() -> None:
    items = [f for _, _, f in SHARED]
    assert _signature(roadmap.group_by_phase(items)) == _signature(
        roadmap.group_by_phase(items)
    )


def test_tied_priorities_are_ordered_by_check_id_not_by_arrival() -> None:
    """The case the shared fixture cannot cover.

    Every fixture finding has a distinct priority, so a sort keyed on priority alone
    would look deterministic there. A real review leaves most findings at priority 0,
    and that is where input order leaks through without the tie-break.
    """
    tied = [
        finding(check_id=cid, remediation_effort="medium", priority=0)
        for cid in ("SEC-9", "SEC-2", "OPS-4", "SEC-1")
    ]
    expected = ["OPS-4", "SEC-1", "SEC-2", "SEC-9"]

    for order in itertools.permutations(tied):
        grouped = roadmap.group_by_phase(list(order))
        assert [f.check_id for f in grouped["short_term"]] == expected


def test_same_check_id_under_two_frameworks_keeps_a_stable_order() -> None:
    pair = [
        finding(framework="trust7", check_id="X-1", remediation_effort="medium"),
        finding(framework="aws_waf", check_id="X-1", remediation_effort="medium"),
    ]
    for order in (pair, pair[::-1]):
        grouped = roadmap.group_by_phase(list(order))
        assert [f.framework for f in grouped["short_term"]] == ["aws_waf", "trust7"]


def test_identical_grouping_regardless_of_input_order() -> None:
    """The real guarantee.

    Findings arrive in whatever order the pipeline sorted them and most share
    ``priority == 0``; without the ``check_id`` tie-break the phases would reorder
    between two renders of the same review. Every permutation of a five-finding
    sample is checked rather than a handful of shuffles, so this cannot pass by luck.
    """
    items = [f for _, _, f in SHARED][:5]
    expected = _signature(roadmap.group_by_phase(items))

    for order in itertools.permutations(items):
        assert _signature(roadmap.group_by_phase(list(order))) == expected


def test_ignores_fields_it_must_not_read() -> None:
    items = [f for _, _, f in SHARED]
    base = _signature(roadmap.group_by_phase(items))
    perturbed = [
        f.model_copy(update={"confidence": "low", "evidence": "rewritten"}) for f in items
    ]
    assert _signature(roadmap.group_by_phase(perturbed)) == base
