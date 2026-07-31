"""Guards on `scripts/probe_checks.py`, without spending anything.

Same reasoning as `test_verify_script.py`: this script is read during an
investigation, at the moment someone is deciding whether a check works. A broken
report there reads as a broken pipeline, and it is the most expensive way for the
script to fail — the run has already been paid for by the time anyone sees it.

Two things are pinned. That it refuses to spend money it cannot account for — no
credential, no token, or a check_id that is not in the rubric all exit before the
first call. And that `--compare` and the mechanism-citation flag report what actually
happened, since those are the two outputs a decision gets made from.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import probe_checks  # noqa: E402

FIXTURE = ROOT.parent / "fixtures" / "verification" / "a2i-human-review.drawio"


def _entry(status: str, evidence: str) -> dict:
    return {"check_id": "ta_human_in_loop", "status": status, "evidence": evidence}


def _written(path: pathlib.Path, checks: dict) -> pathlib.Path:
    path.write_text(json.dumps({"review_id": "r", "overall_score": 1.0,
                                "checks": checks}))
    return path


# --------------------------------------------------------------------------- #
# It does not spend money it cannot account for
# --------------------------------------------------------------------------- #

def test_an_unknown_check_id_is_refused_before_any_call(capsys, monkeypatch) -> None:
    """The cheapest possible mistake to make and the most annoying to discover after
    six model calls: a typo in a check_id, which would otherwise run the whole review
    and then print "NO FINDING RETURNED"."""
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", "t")

    code = probe_checks.main(
        ["--diagram", str(FIXTURE), "--checks", "ta_human_in_loop", "nope_not_real"]
    )

    assert code == 1
    assert "nope_not_real" in capsys.readouterr().out


def test_no_credential_exits_two_rather_than_failing_mid_review(
    capsys, monkeypatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import config

    monkeypatch.setattr(config, "llm_api_key",
                        lambda: (_ for _ in ()).throw(RuntimeError("not set")))

    code = probe_checks.main(["--diagram", str(FIXTURE), "--checks", "ta_human_in_loop"])
    out = capsys.readouterr().out

    assert code == 2
    assert "BLOCKED" in out
    # And it says how to supply one WITHOUT pasting it somewhere it would be recorded.
    assert "without pasting it into a chat" in out


def test_a_deployed_run_without_a_token_is_refused(capsys, monkeypatch) -> None:
    """`--base-url` reaches a gated service, so a missing token would spend the
    upload and then 401."""
    monkeypatch.delenv("DEMO_ACCESS_TOKEN", raising=False)

    code = probe_checks.main([
        "--diagram", str(FIXTURE), "--checks", "ta_human_in_loop",
        "--base-url", "https://example.invalid",
    ])

    assert code == 2
    assert "DEMO_ACCESS_TOKEN" in capsys.readouterr().out


def test_compare_makes_no_call_at_all(tmp_path, capsys) -> None:
    """The whole point of writing runs to disk: the comparison is free and repeatable,
    so nobody re-runs a paid review to look at it twice."""
    before = _written(tmp_path / "b.json", {"ta_human_in_loop": _entry("fail", "None.")})
    after = _written(tmp_path / "a.json", {"ta_human_in_loop": _entry("pass", "A2I.")})

    assert probe_checks.main(["--compare", str(before), str(after)]) == 0

    out = capsys.readouterr().out
    assert "fail" in out and "pass" in out
    assert "YES" in out, "a moved verdict must be marked as moved"


def test_compare_marks_an_unmoved_verdict_as_unmoved(tmp_path, capsys) -> None:
    """The result this round is most likely to produce, and the one it would be
    easiest to misread as 'the fix worked'."""
    same = {"ta_human_in_loop": _entry("fail", "No human review step is described.")}
    before = _written(tmp_path / "b.json", same)
    after = _written(tmp_path / "a.json", same)

    probe_checks.main(["--compare", str(before), str(after)])

    out = capsys.readouterr().out
    assert "YES" not in out
    assert "no" in out


def test_compare_reports_a_check_present_in_only_one_run(tmp_path, capsys) -> None:
    """A run that returned no verdict for a check must not silently vanish from the
    table — that is a failed measurement, not agreement."""
    before = _written(tmp_path / "b.json", {"ta_human_in_loop": _entry("fail", "x")})
    after = _written(tmp_path / "a.json", {"rr_validation_before_prod": _entry("pass", "y")})

    probe_checks.main(["--compare", str(before), str(after)])

    out = capsys.readouterr().out
    assert "ta_human_in_loop" in out and "rr_validation_before_prod" in out
    assert "<missing>" in out


# --------------------------------------------------------------------------- #
# The mechanism-citation flag
# --------------------------------------------------------------------------- #

def test_a_pass_that_never_cites_the_mechanism_is_reported_as_not_citing_it() -> None:
    """The distinction the investigation turns on. A `pass` whose evidence never
    mentions the review loop is a verdict that happens to be right, and reading it as
    'the check works' is how a real gap survives a probe."""
    lucky = _entry("pass", "The design appears to meet this requirement.")

    assert probe_checks._mentions_human_review(lucky) is False


@pytest.mark.parametrize("evidence", [
    "The A2I review loop gates the write.",
    "A reviewer approves or overrides each low-confidence decision.",
    "Amazon Augmented AI routes tasks to a human.",
    "Human-in-the-loop review precedes the save.",
])
def test_evidence_that_does_cite_the_mechanism_is_recognised(evidence: str) -> None:
    assert probe_checks._mentions_human_review(_entry("pass", evidence)) is True


def test_a_missing_finding_does_not_count_as_citing_anything() -> None:
    assert probe_checks._mentions_human_review(None) is False


def test_a_missing_finding_prints_as_missing_rather_than_empty(capsys) -> None:
    probe_checks._print_finding("ta_human_in_loop", None)

    assert "NO FINDING RETURNED" in capsys.readouterr().out


def test_every_reported_field_is_one_a_finding_actually_has() -> None:
    """So a renamed schema field shows up here as a missing column rather than as a
    silently blank line in the report."""
    from schema import Finding

    assert set(probe_checks.FIELDS) <= set(Finding.model_fields)
