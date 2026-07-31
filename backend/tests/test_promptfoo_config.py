"""Offline guards on `promptfooconfig.yaml`. No API call, no model, no cost.

## Why a paid eval needs a free test

The promptfoo eval is the only thing in this repo that cannot be run in CI: it makes
real calls against a pay-per-token key, so it runs on request and by approval. That
leaves two failure modes it cannot catch about itself:

* **Config rot.** A renamed assertion function, a moved provider, a check_id that left
  the rubric, a design id that no longer matches the label file. None of those surface
  until someone spends money to find out.
* **A vacuous assertion.** An eval whose assertions have never been observed to fail
  is decoration. The 46-point regression is the whole reason the config exists, so the
  assertion that watches for it is fed a synthetic *regressed* payload here and
  required to reject it.

Everything below is derived from the real config file and the real rubric — nothing
about the eval is restated. That is deliberate: a test that hard-coded "18 checks" or
the assertion function names would keep passing after the config changed underneath it,
which is the same failure it is here to prevent.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml

import rubric

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO / "promptfooconfig.yaml"


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _file_ref(value: str) -> tuple[pathlib.Path, str]:
    """Split promptfoo's `file://path/to/x.py:function` into its two halves."""
    assert value.startswith("file://"), value
    body = value[len("file://"):]
    path, _, function = body.rpartition(":")
    return REPO / path, function


def _asserts(config: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(test, a) for test in config["tests"] for a in test["assert"]]


# --------------------------------------------------------------------------- #
# The config resolves
# --------------------------------------------------------------------------- #

def test_the_config_is_valid_yaml_with_the_four_sections_promptfoo_needs(config) -> None:
    assert config["prompts"], "promptfoo needs at least one prompt"
    assert config["providers"], "promptfoo needs a provider"
    assert config["tests"], "an eval with no tests passes trivially"
    assert config["description"]


def test_the_provider_file_exists_and_exposes_call_api(config) -> None:
    path, _ = _file_ref(config["providers"][0]["id"] + ":")
    assert path.is_file(), path

    from scripts import promptfoo_provider

    assert callable(promptfoo_provider.call_api)


def test_the_prompt_is_the_design_variable_and_carries_no_prompt_text(config) -> None:
    """The eval must address a design, not carry a copy of the shipped prompt.

    A duplicated prompt is the classic way an eval goes quietly useless: it keeps
    passing against its own copy after the real one changes. `agent/stages.py` is the
    only place prompt text lives, and this keeps it that way.
    """
    assert config["prompts"] == ["{{design}}"]

    from agent import stages

    raw = CONFIG_PATH.read_text()
    # Any 40-character run of the real evaluate system prompt appearing in the config
    # would mean prompt text had been copied in.
    prompt = stages._EVALUATE_SYSTEM
    windows = {prompt[i:i + 40] for i in range(0, max(1, len(prompt) - 40), 10)}
    leaked = sorted(w for w in windows if w and w in raw)
    assert not leaked, f"prompt text copied into the eval config: {leaked[:2]}"


def test_every_assertion_points_at_a_function_that_exists(config) -> None:
    """The failure mode this catches costs a full paid run to discover otherwise:
    promptfoo reports a missing assertion function as an error per test case, after
    the reviews have already been paid for."""
    import importlib

    for test, assertion in _asserts(config):
        assert assertion["type"] == "python", assertion
        path, function = _file_ref(assertion["value"])
        assert path.is_file(), f"{test['description']}: no such file {path}"
        module = importlib.import_module(f"scripts.{path.stem}")
        assert callable(getattr(module, function, None)), (
            f"{test['description']}: {path.name} has no callable {function!r}"
        )


def test_every_test_case_names_a_design_that_the_ground_truth_has(config) -> None:
    from scripts.accuracy_harness import load_ground_truth

    known = {
        d["id"]
        for d in load_ground_truth(REPO / "fixtures" / "ground_truth", [])
    }
    for test in config["tests"]:
        assert test["vars"]["design"] in known, (
            f"{test['description']}: unknown design {test['vars']['design']!r}"
        )


def test_every_drift_case_names_a_real_check_that_is_actually_labelled(config) -> None:
    from scripts.accuracy_harness import load_ground_truth

    known = set(rubric.checks_by_id())
    labels = {
        d["id"]: d["labels"]
        for d in load_ground_truth(REPO / "fixtures" / "ground_truth", [])
    }
    drift = [t for t in config["tests"] if "check_id" in t["vars"]]

    assert len(drift) >= 3, "the round asked for 3-5 drift cases"
    for test in drift:
        check_id = test["vars"]["check_id"]
        design = test["vars"]["design"]
        assert check_id in known, f"{check_id} is not in the rubric"
        assert check_id in labels[design], (
            f"{design} has no ground-truth label for {check_id}, so the assertion "
            f"would raise rather than fail"
        )


def test_the_drift_cases_do_not_overlap_the_gate_cases(config) -> None:
    """Independence. If a drift case named an ai_conditional check, the gate firing
    would decide it, and one regression would show up as two — or worse, a gate
    failure would be read as model drift."""
    gated = {c.check_id for c in rubric.all_checks() if c.ai_conditional}
    overlap = [
        t["vars"]["check_id"] for t in config["tests"]
        if t["vars"].get("check_id") in gated
    ]
    assert not overlap, f"drift cases sit on AI-conditional checks: {overlap}"


def test_both_designs_are_covered_and_both_polarities_of_the_gate(config) -> None:
    """A gate eval on the no-AI design alone would be satisfied by a gate that fires
    on everything. The AI-bearing design is the control."""
    designs = {t["vars"]["design"] for t in config["tests"]}
    assert designs == {
        "design_a_techassist_rag_portal",
        "design_b_checkout_payments_api",
    }

    verdicts = {
        t["vars"]["expected_ai_verdict"]
        for t in config["tests"] if "expected_ai_verdict" in t["vars"]
    }
    assert verdicts == {"denied", "present"}


def test_the_eval_runs_one_review_at_a_time(config) -> None:
    """Concurrency above 1 races two processes onto the same cache file, and buys
    latency variance rather than throughput on a provider-locked route."""
    assert config["evaluateOptions"]["maxConcurrency"] == 1


# --------------------------------------------------------------------------- #
# The assertions are not vacuous
#
# Each one is handed a good payload and a regressed one, built with the provider's own
# `summarise` so the shape cannot drift from what promptfoo will actually pass in.
# --------------------------------------------------------------------------- #

def _review(statuses: dict[str, str], *, verdict: str = "denied",
            evidence: str | None = None) -> dict[str, Any]:
    """A minimal stored-review payload, in the shape `GET /reviews/{id}` returns."""
    gated_evidence = (
        "Marked not applicable: no AI/ML component was detected in this design, and "
        "this check only applies to designs that have one. No AI/ML component "
        "detected, and the design explicitly states it has none."
        if evidence is None else evidence
    )
    checks = rubric.checks_by_id()
    return {
        "title": "t",
        "overall_score": 42.9,
        "ai_detection": {"verdict": verdict},
        "frameworks": [{"framework": "aws_waf", "score": 50.0},
                       {"framework": "trust7", "score": 0.0}],
        "findings": [
            {
                "check_id": check_id,
                "status": status,
                "evidence": gated_evidence if status == "not_applicable" else "Stated.",
                "framework": checks[check_id].framework,
                "pillar_id": checks[check_id].pillar_id,
            }
            for check_id, status in statuses.items()
        ],
    }


def _summary(statuses: dict[str, str], **kwargs: Any) -> str:
    import json

    from scripts import promptfoo_provider

    design = {"id": "design_b_checkout_payments_api"}
    return json.dumps(
        promptfoo_provider.summarise(_review(statuses, **kwargs), design)
    )


def _all_checks(gated: str) -> dict[str, str]:
    """Every rubric check, AI-conditional ones at `gated` and the rest at pass."""
    return {
        c.check_id: (gated if c.ai_conditional else "pass")
        for c in rubric.all_checks()
    }


def test_the_gate_assertion_accepts_a_correctly_gated_review() -> None:
    from scripts import promptfoo_asserts

    result = promptfoo_asserts.ai_conditional_checks_are_not_applicable(
        _summary(_all_checks("not_applicable"))
    )
    assert result["pass"], result["reason"]


def test_the_gate_assertion_rejects_the_46_point_regression() -> None:
    """The exact shape of the real failure: all 18 returned `pass`.

    If this test ever passes trivially — because the assertion was loosened, or the
    rubric stopped marking any check ai_conditional — the eval has stopped watching
    for the one thing it was built for.
    """
    from scripts import promptfoo_asserts

    result = promptfoo_asserts.ai_conditional_checks_are_not_applicable(
        _summary(_all_checks("pass"))
    )
    assert not result["pass"]
    assert "18 of 18" in result["reason"], result["reason"]
    assert "gov_model_inventory=pass" in result["reason"]


def test_the_gate_assertion_rejects_a_single_ungated_check() -> None:
    """Not just the all-18 case: one leak has to fail too, or a partial regression
    passes."""
    from scripts import promptfoo_asserts

    statuses = _all_checks("not_applicable")
    statuses["tf_privacy"] = "pass"

    result = promptfoo_asserts.ai_conditional_checks_are_not_applicable(
        _summary(statuses)
    )
    assert not result["pass"]
    assert "tf_privacy=pass" in result["reason"]


def test_the_control_assertion_rejects_an_over_firing_gate() -> None:
    """The dangerous direction: a gate that marks everything not_applicable would
    satisfy the assertion above while making the tool useless."""
    from scripts import promptfoo_asserts

    everything_gated = {c.check_id: "not_applicable" for c in rubric.all_checks()}

    assert not promptfoo_asserts.the_gate_did_not_reach_the_other_checks(
        _summary(everything_gated)
    )["pass"]
    assert promptfoo_asserts.the_gate_did_not_reach_the_other_checks(
        _summary(_all_checks("not_applicable"))
    )["pass"]


def test_the_control_assertion_watches_data_residency_specifically() -> None:
    """`ss_data_residency` is the one TRUST-7 check deliberately left ungated, and the
    label a human corrected from not_applicable to fail. A gate that swept it in would
    disagree with the ground truth by construction."""
    from scripts import promptfoo_asserts

    statuses = _all_checks("not_applicable")
    statuses["ss_data_residency"] = "not_applicable"

    result = promptfoo_asserts.the_gate_did_not_reach_the_other_checks(
        _summary(statuses)
    )
    assert not result["pass"]
    assert "ss_data_residency" in result["reason"]


def test_the_reasoning_assertion_rejects_a_silent_not_applicable() -> None:
    """A gated check with no explanation reads as a fact rather than a decision."""
    from scripts import promptfoo_asserts

    silent = _summary(_all_checks("not_applicable"), evidence="")
    assert not promptfoo_asserts.the_gated_findings_say_why(silent)["pass"]
    assert promptfoo_asserts.the_gated_findings_say_why(
        _summary(_all_checks("not_applicable"))
    )["pass"]


def test_the_detector_assertion_rejects_the_wrong_verdict() -> None:
    from scripts import promptfoo_asserts

    context = {"vars": {"expected_ai_verdict": "denied"}}
    good = _summary(_all_checks("not_applicable"), verdict="denied")
    bad = _summary(_all_checks("not_applicable"), verdict="absent")

    assert promptfoo_asserts.ai_verdict_is(good, context)["pass"]
    result = promptfoo_asserts.ai_verdict_is(bad, context)
    assert not result["pass"]
    assert "expected 'denied'" in result["reason"]


def test_the_detector_assertion_fails_loudly_when_the_case_forgets_the_var() -> None:
    """Rather than passing because there was nothing to compare against."""
    from scripts import promptfoo_asserts

    result = promptfoo_asserts.ai_verdict_is(
        _summary(_all_checks("not_applicable")), {"vars": {}}
    )
    assert not result["pass"]
    assert "expected_ai_verdict" in result["reason"]


def test_the_drift_assertion_reads_the_label_file_and_rejects_a_flip(config) -> None:
    """Both halves of what makes this a drift detector: the expected verdict comes from
    the labels, not the YAML, and a wrong verdict fails."""
    from scripts import promptfoo_asserts
    from scripts.accuracy_harness import load_ground_truth

    labels = {
        d["id"]: d["labels"]
        for d in load_ground_truth(REPO / "fixtures" / "ground_truth", [])
    }
    drift = [t for t in config["tests"] if "check_id" in t["vars"]]
    other = {"pass": "fail", "fail": "pass", "partial": "pass",
             "not_applicable": "fail"}

    for test in drift:
        design, check_id = test["vars"]["design"], test["vars"]["check_id"]
        truth = labels[design][check_id]["status"]
        context = {"vars": {"check_id": check_id}}

        def summary(status: str, design=design, check_id=check_id) -> str:
            import json

            from scripts import promptfoo_provider

            review = _review({check_id: status})
            return json.dumps(promptfoo_provider.summarise(review, {"id": design}))

        assert promptfoo_asserts.verdict_matches_ground_truth(
            summary(truth), context
        )["pass"], f"{design}/{check_id}: the truthful verdict was rejected"

        result = promptfoo_asserts.verdict_matches_ground_truth(
            summary(other[truth]), context
        )
        assert not result["pass"], f"{design}/{check_id}: a flipped verdict passed"
        assert f"ground truth says {truth}" in result["reason"]


def test_a_missing_verdict_fails_rather_than_passing_quietly() -> None:
    """A review that returned no finding for a check must not read as agreement."""
    from scripts import promptfoo_asserts

    result = promptfoo_asserts.verdict_matches_ground_truth(
        _summary({"oe_observability": "pass"}), {"vars": {"check_id": "oe_iac"}}
    )
    assert not result["pass"]
    assert "no verdict returned" in result["reason"]


# --------------------------------------------------------------------------- #
# The provider's own contract, without calling it
# --------------------------------------------------------------------------- #

def test_the_provider_refuses_an_empty_design_id() -> None:
    """Promptfoo passes the rendered prompt straight through; an empty one must be an
    error, not six paid calls against a design that does not exist."""
    from scripts import promptfoo_provider

    assert "error" in promptfoo_provider.call_api("   ")


def test_the_provider_reports_an_unknown_design_without_calling_the_model() -> None:
    from scripts import promptfoo_provider

    result = promptfoo_provider.call_api("design_that_does_not_exist")

    assert "error" in result
    assert "ground truth" in result["error"]


def test_the_cache_key_covers_every_file_that_can_change_a_verdict() -> None:
    """The cache is what keeps this eval affordable, and a cache that outlives a prompt
    edit would make it lie. Asserted as a set so a new stage file has to be added here
    deliberately."""
    from scripts import promptfoo_provider

    covered = {p.name for p in promptfoo_provider.CACHE_INPUTS}
    assert covered == {
        "rubric.json", "stages.py", "pipeline.py", "ai_gate.py",
        "ai_detection.py", "rubric.py", "scoring.py",
    }
    assert all(p.is_file() for p in promptfoo_provider.CACHE_INPUTS)


def test_the_summary_carries_what_every_assertion_needs() -> None:
    import json

    from scripts import promptfoo_provider

    summary = json.loads(_summary(_all_checks("not_applicable")))

    assert set(summary) >= {
        "design", "statuses", "evidence", "ai_verdict", "overall_score",
        "framework_scores",
    }
    assert len(summary["statuses"]) == len(rubric.all_checks())
