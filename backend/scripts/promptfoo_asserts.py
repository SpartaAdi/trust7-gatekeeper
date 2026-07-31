"""Assertions for `promptfooconfig.yaml`. Each one reads the provider's summary JSON.

Written in Python rather than promptfoo's inline JavaScript for one reason: they need
`rubric.py` and the ground-truth loader. An assertion that hard-codes the eighteen
AI-conditional check ids would pass forever after someone edited `rubric.json`, and
one that hard-codes an expected verdict would drift away from the label file the
accuracy harness scores against. Both read the real source instead.

Every function returns promptfoo's `GradingResult` shape — `{pass, score, reason}` —
rather than a bare bool, because the reason is what a failure is worth having. "assert
failed" tells you a regression happened; "gov_model_inventory came back `pass`, and 17
others, on a design whose AI verdict is `denied`" tells you which one.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GROUND_TRUTH_DIR = REPO / "fixtures" / "ground_truth"


def _result(passed: bool, reason: str) -> dict[str, Any]:
    return {"pass": passed, "score": 1.0 if passed else 0.0, "reason": reason}


def _parse(output: Any) -> dict[str, Any]:
    return output if isinstance(output, dict) else json.loads(output)


def _ai_conditional_ids() -> list[str]:
    import rubric

    return sorted(c.check_id for c in rubric.all_checks() if c.ai_conditional)


def _label(design_id: str, check_id: str) -> str:
    """The human label for one check, from the same loader the harness uses."""
    from scripts.accuracy_harness import load_ground_truth

    design = load_ground_truth(GROUND_TRUTH_DIR, [design_id])[0]
    entry = design["labels"].get(check_id)
    if entry is None:
        raise KeyError(
            f"{design_id} has no ground-truth label for {check_id}; the eval case "
            f"and the label file disagree about what is being tested"
        )
    return entry["status"]


# --------------------------------------------------------------------------- #
# (a) The AI-applicability gate — the 46-point regression
# --------------------------------------------------------------------------- #

def ai_conditional_checks_are_not_applicable(
    output: Any, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """THE case this eval exists for.

    On a design that states it has no AI, every check the rubric marks
    `ai_conditional` must come back `not_applicable`. In one of three otherwise
    identical harness runs, all eighteen came back `pass` instead — 89.3 instead of
    42.9 overall, 92.9 instead of 0.0 on TRUST-7 — because applicability was an
    unconstrained per-check model judgement with no gate behind it.

    A prompt edit that reintroduces that is exactly what a regression eval should
    catch, and this assertion is the reason the config exists.
    """
    summary = _parse(output)
    statuses = summary["statuses"]
    expected = _ai_conditional_ids()

    missing = [c for c in expected if c not in statuses]
    if missing:
        return _result(False, f"the review returned no verdict at all for: {missing}")

    wrong = {c: statuses[c] for c in expected if statuses[c] != "not_applicable"}
    if wrong:
        return _result(
            False,
            f"{len(wrong)} of {len(expected)} AI-conditional checks were evaluated "
            f"instead of gated on a design whose AI verdict is "
            f"{summary['ai_verdict']!r}: "
            + ", ".join(f"{c}={s}" for c, s in sorted(wrong.items())),
        )
    return _result(
        True,
        f"all {len(expected)} AI-conditional checks not_applicable "
        f"(AI verdict {summary['ai_verdict']!r})",
    )


def the_gate_did_not_reach_the_other_checks(
    output: Any, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The control group, and the more dangerous direction.

    A gate that marked everything `not_applicable` would satisfy the assertion above
    while making the tool useless. The checks the rubric does NOT mark `ai_conditional`
    must still be evaluated — including `ss_data_residency`, which is deliberately not
    gated because residency applies to any design holding regulated data, and which is
    the label the human reviewer corrected from `not_applicable` to `fail` for exactly
    that reason.
    """
    import rubric

    summary = _parse(output)
    statuses = summary["statuses"]
    others = [c.check_id for c in rubric.all_checks() if not c.ai_conditional]

    gated = [c for c in others if statuses.get(c) == "not_applicable"]
    if gated:
        return _result(
            False,
            f"{len(gated)} of {len(others)} checks that are NOT ai_conditional came "
            f"back not_applicable: {', '.join(sorted(gated))}",
        )
    return _result(True, f"all {len(others)} non-AI-conditional checks were evaluated")


def the_gated_findings_say_why(
    output: Any, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A `not_applicable` a reviewer cannot interrogate reads as a fact.

    The gate writes the detection rationale — including the component list it searched
    — into the finding's evidence, so an auditor can overrule it on sight. That is a
    stored-output property, so an eval over the real payload is the right place for it.
    """
    summary = _parse(output)
    evidence = summary["evidence"]
    silent = [
        check_id
        for check_id in _ai_conditional_ids()
        if "no AI/ML component was detected" not in (evidence.get(check_id) or "")
    ]
    if silent:
        return _result(
            False,
            f"{len(silent)} gated checks carry no reasoning for being gated: "
            f"{', '.join(silent)}",
        )
    return _result(True, "every gated finding carries the detection rationale")


def ai_verdict_is(output: Any, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The deterministic detector's own verdict, from `vars.expected_ai_verdict`.

    Checked separately from the gate because the two fail differently: a wrong verdict
    here is a detector regression (a denial phrase stopped matching), while a right
    verdict with ungated checks is a pipeline-wiring regression. Reporting them as one
    assertion would send whoever reads the failure to the wrong file.
    """
    summary = _parse(output)
    expected = ((context or {}).get("vars") or {}).get("expected_ai_verdict")
    if not expected:
        return _result(False, "the test case set no `expected_ai_verdict` var")
    actual = summary["ai_verdict"]
    return _result(
        actual == expected,
        f"AI verdict {actual!r}"
        + ("" if actual == expected else f", expected {expected!r}"),
    )


# --------------------------------------------------------------------------- #
# (b) Drift detection on settled checks
# --------------------------------------------------------------------------- #

def verdict_matches_ground_truth(
    output: Any, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One check, exact match against its human label.

    Read from the label file rather than written into the YAML, so the eval and the
    accuracy harness can never disagree about what the truth is.

    Exact match, not the harness's open-gap binary view. These cases are chosen to be
    settled ones, where the evidence is a single explicit sentence in the document —
    so a `fail` arriving as `partial` IS the drift this is watching for, not noise. It
    is deliberately NOT an attempt to push the model off hedging in general: that is a
    disclosed limitation measured by the harness across all 45 checks, and it is out of
    scope here.
    """
    summary = _parse(output)
    check_id = ((context or {}).get("vars") or {}).get("check_id")
    if not check_id:
        return _result(False, "the test case set no `check_id` var")

    expected = _label(summary["design"], check_id)
    actual = summary["statuses"].get(check_id, "<no verdict returned>")
    return _result(
        actual == expected,
        f"{check_id}: {actual}"
        + ("" if actual == expected else f", ground truth says {expected}"),
    )
