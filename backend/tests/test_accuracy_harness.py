"""Guards on the accuracy harness itself.

The harness cannot be run against the live provider in CI — it makes real, paid
calls — so its arithmetic and its plumbing are covered here instead. That
separation matters more than it looks: an eval harness reports numbers nobody can
sanity-check by eye, so a harness bug does not surface as a failure, it surfaces
as a plausible wrong figure that gets quoted in a decision.

Three things are pinned:

* the metric arithmetic, against values worked out by hand in the test;
* the variance and majority-verdict logic, which is what the temperature change is
  judged by — including that identical runs report zero variance and differing
  runs report exactly the checks that moved;
* the end-to-end plumbing, driven through the real routes with `complete_json`
  stubbed, so `--repeats` really does produce N independent reviews and the
  report renders.

The stub makes the last group a test of the HARNESS, not of pipeline accuracy.
Nothing here measures how good the model's verdicts are.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from typing import Any

import pytest

import config
import llm
import rubric


def _load_harness() -> Any:
    """Import the harness from scripts/, which is not an importable package."""
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "accuracy_harness.py"
    spec = importlib.util.spec_from_file_location("accuracy_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()

# The synthetic stand-ins, now out of the directory a normal harness run globs.
#
# They moved when real hand-labelled ground truth arrived: a run that scored
# invented designs alongside the tester's would report a blended figure and look
# like one number. They remain the right fixture for the PLUMBING tests below —
# each ships a .drawio, so a review runs end to end without touching the vision
# path — so those pass this directory explicitly, which is now the only way to
# reach it. The tests about the SHIPPED set point at the real ground truth.
STUB_DIR = harness.GROUND_TRUTH_DIR / "synthetic_stub"


# --------------------------------------------------------------------------- #
# Metric arithmetic
# --------------------------------------------------------------------------- #

# truth, predicted. Five pairs, chosen so every class has at least one instance
# and so precision and recall differ from each other on `fail` and `pass`.
HAND_WORKED = [
    ("pass", "pass"),                      # pass TP
    ("pass", "fail"),                      # pass FN, fail FP
    ("fail", "fail"),                      # fail TP
    ("partial", "pass"),                   # partial FN, pass FP
    ("not_applicable", "not_applicable"),  # n/a TP
]


def test_per_class_precision_recall_and_f1_match_the_hand_worked_values() -> None:
    result = harness.metrics(HAND_WORKED)

    # pass:    TP 1, FP 1 (the partial called pass), FN 1 (the pass called fail)
    assert result["per_class"]["pass"]["precision"] == 0.5
    assert result["per_class"]["pass"]["recall"] == 0.5
    assert result["per_class"]["pass"]["f1"] == 0.5
    # fail:    TP 1, FP 1, FN 0 -> recall is perfect, precision is not
    assert result["per_class"]["fail"]["precision"] == 0.5
    assert result["per_class"]["fail"]["recall"] == 1.0
    assert result["per_class"]["fail"]["f1"] == pytest.approx(0.6667, abs=5e-5)
    # partial: never predicted, so nothing is right about it
    assert result["per_class"]["partial"]["precision"] == 0.0
    assert result["per_class"]["partial"]["recall"] == 0.0
    assert result["per_class"]["partial"]["predicted"] == 0
    # n/a:     the one class with nothing wrong
    assert result["per_class"]["not_applicable"]["f1"] == 1.0

    assert result["per_class"]["pass"]["support"] == 2
    assert result["per_class"]["partial"]["support"] == 1


def test_accuracy_and_the_macro_average_match_the_hand_worked_values() -> None:
    result = harness.metrics(HAND_WORKED)

    assert result["correct"] == 3
    assert result["accuracy"] == 0.6
    assert result["macro"]["precision"] == 0.5           # (0.5 + 0 + 0.5 + 1) / 4
    assert result["macro"]["recall"] == 0.625            # (0.5 + 0 + 1 + 1) / 4
    assert result["macro"]["f1"] == pytest.approx(0.5417, abs=5e-5)


def test_micro_is_accuracy_and_all_three_micro_figures_agree() -> None:
    """Single-label multi-class: micro P, R and F1 are the same number.

    Asserted rather than assumed, because three identical figures in the report
    read as a copy-paste bug unless something states they must be.
    """
    result = harness.metrics(HAND_WORKED)

    assert result["micro"]["precision"] == result["accuracy"]
    assert result["micro"]["recall"] == result["accuracy"]
    assert result["micro"]["f1"] == result["accuracy"]


def test_the_macro_average_ignores_classes_with_no_instances() -> None:
    """A class absent from the labels must not drag the macro figures down.

    Without this, a design with no `not_applicable` labels would score at most
    0.75 macro F1 no matter how right every verdict was.
    """
    perfect = [("pass", "pass"), ("fail", "fail")]

    result = harness.metrics(perfect)

    assert result["macro"]["f1"] == 1.0
    assert result["accuracy"] == 1.0


def test_the_open_gap_binary_view_collapses_the_four_statuses_correctly() -> None:
    """fail and partial are positive; pass and not_applicable are negative."""
    result = harness.metrics(HAND_WORKED)["open_gap_binary"]

    # truth-open: the fail and the partial. predicted-open: the two fails.
    assert result["true_positive"] == 1     # the fail called fail
    assert result["false_positive"] == 1    # the pass called fail
    assert result["false_negative"] == 1    # the partial called pass
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5


def test_a_fail_partial_mixup_is_wrong_four_way_but_right_on_the_open_gap_cut() -> None:
    """The reason both cuts are reported: they disagree, and both readings matter.

    Calling a partial gap a failure is a wrong verdict, and it is also a gap that
    was found. One number cannot say both.
    """
    pairs = [("partial", "fail")] * 4

    result = harness.metrics(pairs)

    assert result["accuracy"] == 0.0
    assert result["open_gap_binary"]["precision"] == 1.0
    assert result["open_gap_binary"]["recall"] == 1.0


def test_a_missing_verdict_is_counted_and_never_scored_as_a_class() -> None:
    """A check no run returned must not quietly become a correct or wrong verdict."""
    pairs = [("pass", harness.MISSING), ("fail", "fail")]

    result = harness.metrics(pairs)

    assert result["missing_verdicts"] == 1
    assert result["correct"] == 1
    assert result["accuracy"] == 0.5
    # It appears in the confusion matrix under its own column, and in no class's
    # predicted count.
    assert result["confusion"]["pass"][harness.MISSING] == 1
    assert sum(result["per_class"][s]["predicted"] for s in harness.STATUSES) == 1


def test_an_always_fail_evaluator_is_visibly_bad_on_an_n_a_heavy_design() -> None:
    """Why per-class figures are reported and not just accuracy.

    `expense-portal` is labelled n/a on 18 of TRUST-7's 19 checks. An evaluator
    that never says n/a can still post a non-trivial open-gap recall while being
    wrong about the entire framework, and the n/a row is what shows it.
    """
    pairs = [("not_applicable", "fail")] * 18 + [("fail", "fail")]

    result = harness.metrics(pairs)

    assert result["per_class"]["not_applicable"]["recall"] == 0.0
    assert result["per_class"]["not_applicable"]["support"] == 18
    assert result["per_class"]["not_applicable"]["predicted"] == 0
    assert result["open_gap_binary"]["recall"] == 1.0, (
        "the coarse cut looks perfect, which is exactly why it is not reported alone"
    )


# --------------------------------------------------------------------------- #
# Variance and majority verdicts
# --------------------------------------------------------------------------- #

def _labels(statuses: dict[str, str]) -> dict[str, dict[str, str]]:
    return {k: {"status": v, "confidence": "clear"} for k, v in statuses.items()}


def _run(verdicts: dict[str, str], score: float, pillars: dict[str, float] | None = None):
    return {
        "verdicts": verdicts,
        "overall_score": score,
        "pillar_scores": pillars or {"aws_waf.security": score},
    }


def test_identical_runs_report_no_variance_at_all() -> None:
    labels = _labels({"a": "pass", "b": "fail"})
    verdicts = {"a": "pass", "b": "fail"}
    runs = [_run(dict(verdicts), 71.4) for _ in range(3)]

    variance = harness.variance_across_runs(labels, runs)

    assert variance["fully_identical"] is True
    assert variance["unstable"] == {}
    assert variance["verdict_agreement_rate"] == 1.0
    assert variance["overall_score_spread"] == 0.0
    assert variance["overall_score_stdev"] == 0.0


def test_a_single_flipped_verdict_is_named_with_the_sequence_it_took() -> None:
    """The aggregate rate is not enough: which check moved, and to what."""
    labels = _labels({"a": "pass", "b": "fail", "c": "partial"})
    runs = [
        _run({"a": "pass", "b": "fail", "c": "partial"}, 70.0),
        _run({"a": "pass", "b": "partial", "c": "partial"}, 74.5),
        _run({"a": "pass", "b": "fail", "c": "partial"}, 70.0),
    ]

    variance = harness.variance_across_runs(labels, runs)

    assert variance["fully_identical"] is False
    assert variance["unstable"] == {"b": ["fail", "partial", "fail"]}
    assert variance["identical_across_all_runs"] == 2
    assert variance["verdict_agreement_rate"] == pytest.approx(0.6667, abs=5e-5)
    assert variance["overall_score_spread"] == 4.5


def test_a_verdict_missing_from_one_run_counts_as_instability() -> None:
    """A check answered twice and dropped once did not agree across the runs."""
    labels = _labels({"a": "pass"})
    runs = [_run({"a": "pass"}, 100.0), _run({}, 100.0), _run({"a": "pass"}, 100.0)]

    variance = harness.variance_across_runs(labels, runs)

    assert variance["unstable"] == {"a": ["pass", harness.MISSING, "pass"]}


def test_pillar_score_movement_is_reported_per_pillar() -> None:
    labels = _labels({"a": "pass"})
    runs = [
        _run({"a": "pass"}, 80.0, {"aws_waf.security": 90.0, "aws_waf.reliability": 50.0}),
        _run({"a": "pass"}, 80.0, {"aws_waf.security": 75.0, "aws_waf.reliability": 50.0}),
    ]

    spread = harness.variance_across_runs(labels, runs)["pillar_score_spread"]

    assert spread["aws_waf.security"]["spread"] == 15.0
    assert spread["aws_waf.reliability"]["spread"] == 0.0


def test_the_majority_verdict_is_the_mode_across_runs() -> None:
    runs = [
        _run({"a": "fail"}, 0.0),
        _run({"a": "partial"}, 0.0),
        _run({"a": "fail"}, 0.0),
    ]

    assert harness.majority_verdicts(runs, ["a"]) == {"a": "fail"}


def test_a_three_way_split_falls_back_to_the_first_run() -> None:
    """No majority exists, so the majority row stays a real reproducible verdict.

    The split itself is not hidden — it is already in `variance.unstable`.
    """
    runs = [
        _run({"a": "fail"}, 0.0),
        _run({"a": "partial"}, 0.0),
        _run({"a": "pass"}, 0.0),
    ]

    assert harness.majority_verdicts(runs, ["a"]) == {"a": "fail"}


# --------------------------------------------------------------------------- #
# Ground-truth loading and validation
# --------------------------------------------------------------------------- #

def test_the_shipped_fixture_set_is_valid_against_the_live_rubric() -> None:
    """The fixtures and the rubric must not drift apart silently.

    A rubric edit that renames a check would otherwise leave the harness scoring
    against a check_id that no longer exists, and the only symptom would be a
    quietly lower recall.
    """
    designs = harness.load_ground_truth(harness.GROUND_TRUTH_DIR, [])

    assert {d["id"] for d in designs} == {
        "design_a_techassist_rag_portal",
        "design_b_checkout_payments_api",
    }, "the globbed directory must hold the REAL labelled designs and nothing else"
    known = set(rubric.checks_by_id())
    for design in designs:
        assert set(design["labels"]) <= known
        assert design["document"] is not None and design["document"].is_file()


def test_every_rubric_check_is_labelled_in_the_fixture_set() -> None:
    """Coverage, stated as a test rather than left to the report's header line.

    A partially labelled design still runs — the harness scores the subset and
    prints the gap — but the shipped set is meant to be complete, and an
    unlabelled check silently narrows every figure computed from it.
    """
    designs = harness.load_ground_truth(harness.GROUND_TRUTH_DIR, [])
    all_checks = set(rubric.checks_by_id())

    for design in designs:
        missing = sorted(all_checks - set(design["labels"]))
        assert not missing, f"{design['id']} does not label: {', '.join(missing)}"


def test_the_two_shipped_designs_sit_at_opposite_ends_of_the_n_a_axis() -> None:
    """The property that makes the pair worth having.

    One design is wholly non-AI, so TRUST-7 is almost entirely inapplicable; the
    other is AI-bearing, so none of it is. A fixture set with only one of those
    shapes cannot tell a correct n/a from a lucky one.
    """
    designs = {
        d["id"]: d for d in harness.load_ground_truth(harness.GROUND_TRUTH_DIR, [])
    }

    def n_a_count(design_id: str) -> int:
        return sum(
            1 for label in designs[design_id]["labels"].values()
            if label["status"] == "not_applicable"
        )

    assert n_a_count("design_b_checkout_payments_api") >= 15
    assert n_a_count("design_a_techassist_rag_portal") <= 2

    # And the non-AI design must render at least one WHOLE pillar inapplicable —
    # the case backend/tests/test_scoring.py pins arithmetically. The real Design B
    # renders all seven of TRUST-7's, which is a stronger fixture than the synthetic
    # pair it replaced.
    labels = designs["design_b_checkout_payments_api"]["labels"]
    wholly_na = [
        pillar.pillar_id
        for framework in rubric.load()
        for pillar in framework.pillars
        if all(
            labels.get(c.check_id, {}).get("status") == "not_applicable"
            for c in pillar.checks
        )
    ]
    assert len(wholly_na) >= 5, wholly_na


def test_the_synthetic_stand_ins_are_not_reachable_from_a_default_run() -> None:
    """The stand-ins must stay out of the globbed directory.

    They are invented designs with labels authored in this repository. A run that
    scored them alongside the tester's real ones would average two incomparable
    things into one precision figure and present it as a single number — and the
    only symptom would be a plausible-looking result.

    `load_ground_truth` globs `*.json` non-recursively, so a subdirectory is
    genuinely unreachable rather than merely conventionally ignored. This asserts
    both halves: the stand-ins still exist as fixtures, and a default run cannot
    see them.
    """
    assert STUB_DIR.is_dir(), "the stand-ins should still exist as test fixtures"
    assert {p.name for p in STUB_DIR.glob("*.json")} == {
        "expense-portal.json",
        "claims-triage-ai.json",
    }

    reachable = {p.name for p in harness.GROUND_TRUTH_DIR.glob("*.json")}
    assert "expense-portal.json" not in reachable
    assert "claims-triage-ai.json" not in reachable

    ids = {d["id"] for d in harness.load_ground_truth(harness.GROUND_TRUTH_DIR, [])}
    assert "expense-portal" not in ids and "claims-triage-ai" not in ids


def test_the_real_labels_carry_the_testers_own_values() -> None:
    """The reshape renamed keys; it must not have touched what was labelled.

    `load_ground_truth` reads `status`, so the tester's `verdict` had to be renamed
    to be read at all — and a rename is exactly the kind of edit that can quietly
    become a rewrite. The loader keeps only `status` and `confidence`, so this reads
    the file to check the rest survived.
    """
    import json

    for name, expect_ai in (
        ("DESIGN A_Completed Ground Truth.json", True),
        ("DESIGN B_Completed Ground Truth.json", False),
    ):
        raw = json.loads((harness.GROUND_TRUTH_DIR / name).read_text())
        assert raw["design_is_ai_bearing"] is expect_ai
        assert len(raw["labels"]) == 45
        assert (harness.GROUND_TRUTH_DIR / raw["document"]).is_file()

        for check_id, label in raw["labels"].items():
            # The tester's own fields, carried through the rename.
            assert label["status"] in harness.STATUSES, (check_id, label["status"])
            assert label["why"].strip(), f"{check_id} lost its evidence"
            assert label["labeler"] == "Human Tester"
            assert label["date"] == "2026-07-31"
            # Not invented: the tester recorded no confidence, so the loader's
            # default applies rather than a value we made up.
            assert "confidence" not in label


def test_an_unknown_check_id_is_rejected_before_any_api_call(tmp_path) -> None:
    (tmp_path / "bad.json").write_text(
        json.dumps({"id": "bad", "labels": {"not_a_real_check": "pass"}})
    )

    with pytest.raises(harness.LabelError, match="not in the rubric"):
        harness.load_ground_truth(tmp_path, [])


def test_a_status_outside_the_enum_is_rejected(tmp_path) -> None:
    check_id = rubric.all_checks()[0].check_id
    (tmp_path / "bad.json").write_text(
        json.dumps({"id": "bad", "labels": {check_id: "probably_fine"}})
    )

    with pytest.raises(harness.LabelError, match="must be one of"):
        harness.load_ground_truth(tmp_path, [])


def test_a_bare_string_label_is_accepted_as_shorthand(tmp_path) -> None:
    """A hand-written set should not have to carry the rationale fields."""
    check_id = rubric.all_checks()[0].check_id
    (tmp_path / "terse.json").write_text(
        json.dumps({"id": "terse", "labels": {check_id: "fail"}})
    )

    designs = harness.load_ground_truth(tmp_path, [])

    assert designs[0]["labels"][check_id] == {"status": "fail", "confidence": "clear"}


def test_borderline_labels_can_be_excluded_from_a_cut() -> None:
    labels = {
        "a": {"status": "pass", "confidence": "clear"},
        "b": {"status": "fail", "confidence": "borderline"},
    }
    verdicts = {"a": "pass", "b": "pass"}

    assert harness.metrics(harness.pairs_for(labels, verdicts))["accuracy"] == 0.5
    assert harness.metrics(
        harness.pairs_for(labels, verdicts, only_clear=True)
    )["accuracy"] == 1.0


# --------------------------------------------------------------------------- #
# End-to-end plumbing, through the real routes with a stubbed model
#
# This proves the harness drives the app correctly and that `--repeats` produces N
# independent reviews whose differences it detects. It proves NOTHING about the
# accuracy of real verdicts — the verdicts here are dictated by the stub.
# --------------------------------------------------------------------------- #

@pytest.fixture()
def stubbed_pipeline(monkeypatch, tmp_path):
    """The real routes and the real pipeline, with the model replaced.

    `state["flip"]` lets a later run return a different verdict for one check, so
    the variance path is exercised rather than only the identical-runs path.
    """
    import importlib

    import storage

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", "harness-test-token")

    state: dict[str, Any] = {"calls": 0, "reviews": 0, "flip_on_review": None}
    flipped_check = rubric.all_checks()[0].check_id

    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        state["calls"] += 1
        required = set(schema.get("required", []))

        if "design_summary" in required:
            state["reviews"] += 1
            return {
                "design_summary": "A stubbed design.",
                "components": [
                    {"id": "api", "label": "API", "kind": "compute",
                     "provider": "aws", "service": "ec2", "attributes": []}
                ],
                "data_flows": [], "observations": [], "absent": [],
            }, {}

        if "findings" in required:
            # Statuses come from the shipped ground truth, so the harness's
            # comparison logic runs over realistic input rather than a constant.
            designs = {
                d["id"]: d
                for d in harness.load_ground_truth(STUB_DIR, [])
            }
            truth = designs["expense-portal"]["labels"]
            findings = []
            for check in rubric.all_checks():
                status = truth[check.check_id]["status"]
                if (
                    check.check_id == flipped_check
                    and state["reviews"] == state["flip_on_review"]
                ):
                    status = "partial" if status != "partial" else "fail"
                findings.append({
                    "check_id": check.check_id,
                    "status": status,
                    "severity": check.severity,
                    "severity_rationale": "stub",
                    "title": check.description,
                    "evidence": "stub",
                    "affected_components": [],
                })
            return {"findings": findings}, {}

        if "ranking" in required:
            return {"summary": "- stubbed", "ranking": []}, {}
        return {
            "executive_summary": "Stubbed.",
            "remediations": [],
            "use_case_notes": [],
        }, {}

    monkeypatch.setattr(llm, "complete_json", fake)
    return state


def test_the_harness_drives_three_real_reviews_through_the_real_routes(
    stubbed_pipeline,
) -> None:
    """`--repeats 3` must mean three independent reviews, not one reused result."""
    designs = harness.load_ground_truth(STUB_DIR, ["expense-portal"])

    with harness.Runner("", "harness-test-token", 0.0) as runner:
        results = [runner.review(designs[0]) for _ in range(3)]

    assert len({r["review_id"] for r in results}) == 3, "reviews were not independent"
    assert all(len(r["findings"]) == len(rubric.all_checks()) for r in results)
    # Six model calls per review: classify, evaluate x2 frameworks, prioritize,
    # remediate — and the draw.io diagram makes none, which is the point of using
    # a .drawio fixture rather than a PNG.
    assert stubbed_pipeline["calls"] >= 3 * 4


def test_an_end_to_end_run_produces_a_report_that_renders(
    stubbed_pipeline, monkeypatch,
) -> None:
    """The whole path: run, compare, analyse, render. A stub, so verdicts are known.

    Because the stub answers from the ground-truth labels themselves, the analysed
    accuracy must come out at exactly 1.0 — which is what makes this a test of the
    comparison logic. A harness that mismatched check ids, or compared against the
    wrong design, could not reach 1.0 here.
    """
    designs = harness.load_ground_truth(STUB_DIR, ["expense-portal"])
    pillar_of = {
        c.check_id: f"{c.framework}.{c.pillar_id}" for c in rubric.all_checks()
    }

    runs = []
    with harness.Runner("", "harness-test-token", 0.0) as runner:
        for _ in range(2):
            result = runner.review(designs[0])
            runs.append({
                "review_id": result["review_id"],
                "overall_score": result["overall_score"],
                "seconds": 1.0,
                "verdicts": {f["check_id"]: f["status"] for f in result["findings"]},
                "pillar_scores": harness._pillar_scores_of(result),
                "token_usage": {},
            })

    analysed = harness.analyse(designs[0], runs, pillar_of, len(rubric.all_checks()))

    assert analysed["cuts"][0]["metrics"]["accuracy"] == 1.0
    assert analysed["cuts"][0]["metrics"]["missing_verdicts"] == 0
    assert analysed["variance"]["fully_identical"] is True
    assert analysed["by_pillar"], "no per-pillar breakout was produced"
    assert len(analysed["per_check"]) == len(rubric.all_checks())

    report = {
        "generated_at": "2026-07-30T00:00:00Z",
        "provider": "openrouter", "model": "moonshotai/kimi-k2.6",
        "evaluate_temperature": llm.GREEDY_TEMPERATURE,
        "provider_order": ["coreweave"], "allow_fallbacks": False,
        "repeats": 2, "transport": "test", "wall_clock_seconds": 1.0,
        "pillar_of": pillar_of, "designs": [analysed], "route_log": [],
    }
    markdown = harness.render_markdown(report)

    assert "expense-portal" in markdown
    assert "not_applicable" in markdown
    assert "Variance across runs" in markdown
    # The temperature under test is on the face of the report, so a report cannot
    # be read without knowing which sampling setting produced it.
    assert f"temperature: `{llm.GREEDY_TEMPERATURE}`" in markdown


def test_the_harness_detects_a_verdict_that_moved_between_runs(
    stubbed_pipeline,
) -> None:
    """The variance path against real pipeline output, not hand-built dicts.

    The stub flips one check on the second review only, so this fails if the
    harness compares runs by anything other than per-check verdict.
    """
    designs = harness.load_ground_truth(STUB_DIR, ["expense-portal"])
    flipped = rubric.all_checks()[0].check_id
    stubbed_pipeline["flip_on_review"] = 2

    runs = []
    with harness.Runner("", "harness-test-token", 0.0) as runner:
        for _ in range(3):
            result = runner.review(designs[0])
            runs.append({
                "review_id": result["review_id"],
                "overall_score": result["overall_score"],
                "seconds": 1.0,
                "verdicts": {f["check_id"]: f["status"] for f in result["findings"]},
                "pillar_scores": harness._pillar_scores_of(result),
                "token_usage": {},
            })

    variance = harness.variance_across_runs(designs[0]["labels"], runs)

    assert variance["fully_identical"] is False
    assert list(variance["unstable"]) == [flipped]
    assert variance["identical_across_all_runs"] == len(rubric.all_checks()) - 1
    # A moved verdict must move the score too, or the variance figures would be
    # reporting something the reviewer never sees.
    assert variance["overall_score_spread"] > 0


def test_a_run_that_fails_is_reported_rather_than_scored(stubbed_pipeline, monkeypatch) -> None:
    """A failed review must not be silently averaged in as a run of zeroes."""
    designs = harness.load_ground_truth(STUB_DIR, ["expense-portal"])

    def explode(**kwargs: Any):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(llm, "complete_json", explode)

    with harness.Runner("", "harness-test-token", 0.0) as runner:
        with pytest.raises(RuntimeError):
            runner.review(designs[0])
