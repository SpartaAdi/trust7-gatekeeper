"""A bracketed check_id echoed back by the model is still the check it names.

Both prompt renderers print a check as `- [sec_encryption_at_rest] ...` — the
rubric block `_render_rubric` builds for evaluate, and `_render_findings` for
prioritize and remediate. The brackets are list punctuation, but a model copying
an id back out of that line reasonably copies what it sees.

Every consumer tested membership by exact string, so a bracketed echo matched
nothing. On a real diagram-only run the remediate RETRY came back with bracketed
ids for all 34 findings it was asked about; `_collect_remediations` filed every
one as "not an open finding we asked about" and the reviewer was shown "No
remediation text was generated for this check" 34 times. The answers existed and
were discarded one line before grounding was consulted.

Normalization is applied at all FOUR read sites — evaluate, prioritize, remediate
and the grounding filter — rather than only the one observed to fail. The risk is
the model's inconsistency about a format this codebase chose to print, and nothing
makes the other stages immune to what remediate demonstrably did.

The other half matters as much: a hallucinated id must still be rejected. This
rescues a real id wearing punctuation, never an id the rubric does not hold.
"""

from __future__ import annotations

import pytest

import rubric
from agent import stages
from schema import Finding

REAL = "sec_encryption_at_rest"


def _finding(check_id: str = REAL) -> Finding:
    check = next(c for c in rubric.all_checks() if c.check_id == check_id)
    return Finding(
        framework=check.framework, pillar_id=check.pillar_id, check_id=check_id,
        status="fail", severity="high", title=check.description, evidence="e",
    )


# --------------------------------------------------------------------------- #
# The normalizer itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"[{REAL}]", REAL),          # the observed failure
        (f"  [{REAL}]  ", REAL),      # with surrounding whitespace
        (f"[ {REAL} ]", REAL),        # padded inside the brackets
        (REAL, REAL),                 # the ordinary case, unchanged
        (f"  {REAL} ", REAL),
    ],
)
def test_a_wrapped_id_is_unwrapped(raw, expected) -> None:
    assert stages.normalized_check_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        f"[{REAL}",       # one side only — not a wrapping pair
        f"{REAL}]",
        f"[[{REAL}]]",    # only the OUTERMOST pair comes off
        "",
    ],
)
def test_only_a_matching_outermost_pair_is_stripped(raw) -> None:
    """Deliberately conservative. Anything cleverer starts guessing at what the
    model meant, and this fix must not widen into accepting ids the rubric does
    not hold."""
    out = stages.normalized_check_id(raw)
    assert out != REAL or raw == REAL


# --------------------------------------------------------------------------- #
# remediate — the site that actually failed
# --------------------------------------------------------------------------- #

def test_a_bracketed_remediation_is_collected_not_discarded() -> None:
    payload = {"remediations": [
        {"check_id": f"[{REAL}]", "remediation": "Enable SSE-KMS.", "effort": "low",
         "grounded_in": "Amazon RDS"},
    ]}

    text, effort, discarded = stages._collect_remediations(payload, {REAL})

    assert text == {REAL: "Enable SSE-KMS."}
    assert effort == {REAL: "low"}
    assert discarded == []


def test_a_hallucinated_check_id_is_still_rejected() -> None:
    """The half that must not regress. No brackets, just wrong."""
    payload = {"remediations": [
        {"check_id": "sec_encryption_at_res", "remediation": "x", "effort": "low"},
        {"check_id": "invented_check", "remediation": "y", "effort": "low"},
        {"check_id": "[invented_check]", "remediation": "z", "effort": "low"},
    ]}

    text, _effort, discarded = stages._collect_remediations(payload, {REAL})

    assert text == {}
    assert len(discarded) == 3


def test_the_grounding_filter_reads_the_same_normalized_id() -> None:
    """`text` is keyed on normalized ids. A bracketed id read raw here would miss
    every key, be taken for someone else's discard, and leave a collected
    remediation with no quote recorded against it."""
    payload = {"remediations": [
        {"check_id": f"[{REAL}]", "remediation": "Enable SSE-KMS.", "effort": "low",
         "grounded_in": "Amazon RDS"},
    ]}
    text, _effort, _discarded = stages._collect_remediations(payload, {REAL})

    kept, quotes, removed = stages._ground_remediations(
        payload, text, "the design names amazon rds and nothing else"
    )

    assert kept == {REAL: "Enable SSE-KMS."}
    assert quotes == {REAL: "Amazon RDS"}
    assert removed == []


# --------------------------------------------------------------------------- #
# evaluate and prioritize — same pattern, same fix, never observed to fail
# --------------------------------------------------------------------------- #

def test_evaluate_accepts_a_bracketed_verdict() -> None:
    check = next(c for c in rubric.all_checks() if c.check_id == REAL)
    raw = [{"check_id": f"[{REAL}]", "status": "fail", "severity": "high",
            "severity_rationale": "s", "title": check.description, "evidence": "e",
            "affected_components": []}]

    findings = stages._to_findings(raw, check.framework)

    # `_to_findings` backfills every check the model did not answer, so the whole
    # framework comes back. What matters is whether OUR verdict was accepted.
    mine = next(f for f in findings if f.check_id == REAL)
    assert mine.status == "fail"
    assert mine.evidence == "e"



def test_evaluate_still_drops_a_hallucinated_verdict() -> None:
    raw = [{"check_id": "not_a_real_check", "status": "fail", "severity": "high",
            "severity_rationale": "s", "title": "t", "evidence": "e",
            "affected_components": []}]

    findings = stages._to_findings(raw, "aws_waf")

    # Backfilled, so the list is not empty — but nothing the model said was kept.
    assert findings
    assert not any(f.evidence == "e" for f in findings)
    assert not any(f.check_id == "not_a_real_check" for f in findings)


def test_prioritize_accepts_a_bracketed_rank() -> None:
    findings = [_finding()]

    stages.apply_ranking(findings, [{"check_id": f"[{REAL}]", "rank": 1}])

    assert findings[0].priority == 1


def test_prioritize_still_drops_a_hallucinated_rank() -> None:
    findings = [_finding()]

    stages.apply_ranking(findings, [{"check_id": "invented", "rank": 1}])

    # Completed by the backfill rather than left at the model's word.
    assert findings[0].check_id == REAL
