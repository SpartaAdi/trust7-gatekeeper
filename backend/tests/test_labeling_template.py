"""The blank labeling template, and the rubric checklist export.

Both are scaffolding for a human, and both have exactly one failure mode worth
guarding: drifting out of step with `rubric/rubric.json` without anyone noticing.
A template missing a check silently narrows every figure computed from it; a
checklist missing one sends the labeller to judge 44 things and call it 45.

The other guard here is that the template stays BLANK. It is an answer key, and an
answer key with answers already in it — from a model, or from a well-meaning edit —
measures how consistent two model runs are rather than whether either is correct.
That failure would look exactly like an accuracy figure and mean nothing, so it is
asserted rather than trusted.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

import rubric

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = REPO / "fixtures" / "ground_truth" / "template"
TEMPLATE = TEMPLATE_DIR / "design-labeling-template.json"
CHECKLIST = REPO / "docs" / "rubric_checklist.md"

# The fields a human fills in. Every one must be empty in the shipped template.
FILLABLE_PER_CHECK = ("status", "confidence", "why", "labeler", "date")
FILLABLE_TOP_LEVEL = (
    "id", "title", "provenance", "labeler", "labeled_date",
    "document", "diagram", "context", "design_is_ai_bearing",
)
# Copied from the rubric so the form can be judged without opening the codebase.
# Context, not judgements — these are the only pre-filled fields permitted.
PREFILLED_PER_CHECK = ("framework", "pillar", "description", "default_severity")


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE.read_text())


def _harness():
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "accuracy_harness.py"
    spec = importlib.util.spec_from_file_location("accuracy_harness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# The template is blank
# --------------------------------------------------------------------------- #

def test_every_verdict_field_is_empty(template) -> None:
    """The whole point of the file. A pre-filled verdict is a guess masquerading as
    ground truth, and it would be indistinguishable from a human judgement later."""
    filled = [
        f"{check_id}.{field}={entry[field]!r}"
        for check_id, entry in template["labels"].items()
        for field in FILLABLE_PER_CHECK
        if entry[field] != ""
    ]

    assert not filled, f"the template ships with values already filled in: {filled}"


def test_every_top_level_field_the_labeler_owns_is_empty(template) -> None:
    filled = [f"{k}={template[k]!r}" for k in FILLABLE_TOP_LEVEL if template[k] != ""]

    assert not filled, filled


def test_no_valid_verdict_string_appears_anywhere_in_the_file() -> None:
    """A grep, deliberately.

    The field-by-field checks above pass if the SHAPE is right. This fails if any of
    the four verdict words appears as a value anywhere — including in a field the
    tests above do not know about, or in a comment that reads as a suggestion.
    """
    harness = _harness()
    document = json.loads(TEMPLATE.read_text())

    def values(node):
        if isinstance(node, dict):
            for key, value in node.items():
                # `$schema_note` is prose for the labeller and may name the statuses
                # while explaining them. Values that are judgements may not.
                if key != "$schema_note":
                    yield from values(value)
        elif isinstance(node, list):
            for item in node:
                yield from values(item)
        elif isinstance(node, str):
            yield node

    offenders = [v for v in values(document) if v.strip() in harness.STATUSES]

    assert not offenders, f"verdict value(s) present in the template: {offenders}"


def test_confidence_is_blank_rather_than_defaulted_to_clear(template) -> None:
    """`clear` is the harness's default for a missing confidence, which makes it
    tempting to pre-fill. It must not be: marking everything `clear` removes the
    harness's ability to tell a wrong verdict from an arguable one, and a labeller
    who never sees the field blank never makes that choice consciously."""
    assert all(entry["confidence"] == "" for entry in template["labels"].values())


# --------------------------------------------------------------------------- #
# The template is complete, and matches the rubric
# --------------------------------------------------------------------------- #

def test_all_45_checks_are_present_in_rubric_order(template) -> None:
    expected = [check.check_id for check in rubric.all_checks()]

    assert list(template["labels"]) == expected, (
        "the template's checks have drifted from rubric/rubric.json — regenerate it"
    )
    assert len(expected) == 45


def test_the_prefilled_context_matches_the_rubric_exactly(template) -> None:
    """`description` is what the labeller judges against. If it has drifted from the
    rubric, they are judging a different check from the one the pipeline evaluates."""
    display = {"aws_waf": "WAF-6", "trust7": "TRUST-7"}

    for check in rubric.all_checks():
        entry = template["labels"][check.check_id]
        assert entry["description"] == check.description, check.check_id
        assert entry["pillar"] == check.pillar_name, check.check_id
        assert entry["framework"] == display[check.framework], check.check_id
        assert entry["default_severity"] == check.severity, check.check_id


def test_every_check_carries_all_the_fields_a_labeler_needs(template) -> None:
    for check_id, entry in template["labels"].items():
        missing = [
            field
            for field in (*PREFILLED_PER_CHECK, *FILLABLE_PER_CHECK)
            if field not in entry
        ]
        assert not missing, f"{check_id} is missing {missing}"


# --------------------------------------------------------------------------- #
# The template cannot break the harness
# --------------------------------------------------------------------------- #

def test_the_template_is_invisible_to_the_harness() -> None:
    """It lives in a subdirectory, and `load_ground_truth` does not recurse.

    This is load-bearing, not tidiness. Every `status` in the template is "", which
    is not a valid verdict — so if the file sat in `fixtures/ground_truth/` itself,
    `--check-labels` and every real run would refuse to start.
    """
    harness = _harness()

    globbed = {path.name for path in harness.GROUND_TRUTH_DIR.glob("*.json")}

    assert TEMPLATE.name not in globbed
    assert TEMPLATE.parent.name == "template"
    assert TEMPLATE.parent.parent == harness.GROUND_TRUTH_DIR


def test_a_half_filled_copy_is_rejected_with_the_check_named(tmp_path) -> None:
    """The guard that makes an incomplete labelling session fail loudly.

    A blank `status` must stop the harness and say which check is blank, rather than
    being scored as some default. This is what the README promises `--check-labels`
    does, so it is asserted rather than described.
    """
    harness = _harness()
    document = json.loads(TEMPLATE.read_text())
    document["id"] = "half-done"
    # One check fully answered, the other 44 still blank.
    first, second = list(document["labels"])[:2]
    document["labels"][first]["status"] = "fail"
    document["labels"][first]["confidence"] = "clear"
    (tmp_path / "half-done.json").write_text(json.dumps(document))

    with pytest.raises(harness.LabelError) as caught:
        harness.load_ground_truth(tmp_path, [])

    # The first UNFINISHED check is named, so the labeller knows where they stopped.
    # Which of its blank fields is reported first does not matter; that it points at
    # a specific check rather than saying "invalid file" does.
    assert second in str(caught.value)
    assert first not in str(caught.value), "the completed check must not be blamed"


def test_a_blank_status_specifically_is_what_stops_it(tmp_path) -> None:
    """The `confidence` guard fires first on a wholly-blank entry, so this isolates
    the `status` branch — a filled confidence with no verdict must still be refused."""
    harness = _harness()
    document = json.loads(TEMPLATE.read_text())
    document["id"] = "no-verdicts"
    for entry in document["labels"].values():
        entry["confidence"] = "clear"      # every confidence filled...
    (tmp_path / "no-verdicts.json").write_text(json.dumps(document))

    with pytest.raises(harness.LabelError, match="must be one of"):
        harness.load_ground_truth(tmp_path, [])   # ...and still refused, on status


def test_a_fully_filled_copy_loads_cleanly(tmp_path) -> None:
    """The other half: the template must actually be usable once filled in.

    Statuses here are placeholder values chosen to exercise all four branches — this
    is a test of the FORM, not a set of labels for any design.
    """
    harness = _harness()
    document = json.loads(TEMPLATE.read_text())
    document["id"] = "filled"
    document["document"] = "sow.md"
    (tmp_path / "sow.md").write_text("# A design\n")
    cycle = ["pass", "partial", "fail", "not_applicable"]
    for index, entry in enumerate(document["labels"].values()):
        entry["status"] = cycle[index % 4]
        entry["confidence"] = "clear"
    (tmp_path / "filled.json").write_text(json.dumps(document))

    designs = harness.load_ground_truth(tmp_path, [])

    assert len(designs) == 1
    assert len(designs[0]["labels"]) == 45
    # The pre-filled context fields are ignored by the loader rather than tripping it.
    assert set(designs[0]["labels"][next(iter(document["labels"]))]) == {
        "status", "confidence",
    }


# --------------------------------------------------------------------------- #
# The checklist export
# --------------------------------------------------------------------------- #

def test_the_checklist_lists_every_check_with_its_description() -> None:
    """An export that has drifted is worse than none: it sends a labeller to judge a
    check the pipeline is not evaluating."""
    text = CHECKLIST.read_text()

    for check in rubric.all_checks():
        assert f"`{check.check_id}`" in text, f"{check.check_id} missing from the export"
        assert check.description in text, (
            f"{check.check_id}'s description has drifted from the rubric"
        )


def test_the_checklist_names_every_pillar_and_both_frameworks() -> None:
    text = CHECKLIST.read_text()

    for framework in rubric.load():
        assert framework.name in text
        for pillar in framework.pillars:
            assert pillar.name in text, f"pillar {pillar.pillar_id} missing"


def test_the_checklist_states_the_four_verdicts_and_the_two_rules() -> None:
    """It is read by someone judging pass/fail/partial/n_a without the codebase, so
    the vocabulary and the two rules that decide borderline calls have to be in it."""
    text = CHECKLIST.read_text()

    for verdict in ("pass", "partial", "fail", "not_applicable"):
        assert f"`{verdict}`" in text
    assert "Silence is not a pass" in text
    assert "not merely" in text and "unmentioned" in text


def test_the_checklist_reports_the_real_counts() -> None:
    text = CHECKLIST.read_text()
    pillars = sum(len(f.pillars) for f in rubric.load())

    assert f"**{len(rubric.all_checks())}**" in text
    assert f"**{pillars}**" in text
