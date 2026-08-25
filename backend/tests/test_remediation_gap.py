"""Guards on the remediate shortfall: what gets logged, and what a user is told.

## The run this comes from

A real accuracy-harness run had remediate return 0 of 25 open findings. The existing
retry fired, asked again, and returned 0 of 25 again. The review was then written,
stored and served as a normal completed review.

Two things were wrong with that, and they are separate problems:

1. **Nothing recorded what the model returned.** `ROUTE_LOG` keeps metadata only —
   label, provider, finish_reason, output_tokens — and no stage payload is persisted,
   so afterwards there was no way to tell an empty array apart from entries that were
   returned and then silently discarded by `_collect_remediations`. Those two have
   opposite fixes. `test_the_shortfall_log_*` pin the discriminator.

2. **The user was told nothing.** Every roadmap row read "No remediation text was
   generated for this check." and the only page-level status was the data-fidelity
   panel reporting the diagram had been read at 100%. The rest of these tests pin the
   record that makes it visible.

## What is deliberately NOT here

Any change to the retry strategy. The root cause is not established — it is either a
provider quality dip or a systematic discard, and the logging above exists to find
out which. Guessing would mean changing a retry that may not be the problem.

Nothing here touches scoring: `test_the_gap_record_moves_no_score` asserts that.
"""

from __future__ import annotations

import json
import logging

import pytest

import scoring
from agent import stages
from schema import Finding, RemediationGap, ReviewResult


def finding(check_id: str, *, status: str = "fail", remediation: str = "") -> Finding:
    return Finding(
        framework="aws_waf",
        pillar_id="security",
        check_id=check_id,
        status=status,  # type: ignore[arg-type]
        severity="high",
        title=f"Title for {check_id}",
        evidence="Evidence.",
        remediation=remediation,
    )


def result(*findings: Finding) -> ReviewResult:
    return ReviewResult(review_id="r", created_at="t", findings=list(findings))


# --------------------------------------------------------------------------- #
# _collect_remediations — now reports WHY it discarded an entry
# --------------------------------------------------------------------------- #


def test_a_usable_entry_is_kept_and_nothing_is_reported_discarded() -> None:
    text, effort, discarded = stages._collect_remediations(
        {"remediations": [
            {"check_id": "a", "remediation": "Enable SSE-KMS.", "effort": "low"}
        ]},
        {"a"},
    )
    assert text == {"a": "Enable SSE-KMS."}
    assert effort == {"a": "low"}
    assert discarded == []


def test_an_entry_for_a_check_we_did_not_ask_about_is_reported() -> None:
    """The leading hypothesis for a TOTAL failure, and the thing that was invisible.

    If the model answers well but names the checks in a form this does not accept —
    `[sec_encryption_at_rest]` carrying the brackets `_render_findings` prints them
    inside, say — then every entry is dropped, the count reads 0, and the retry
    cannot help because there was nothing wrong with the answer.
    """
    _text, _effort, discarded = stages._collect_remediations(
        {"remediations": [
            {"check_id": "[sec_encryption_at_rest]", "remediation": "Do it.",
             "effort": "low"},
            {"check_id": "sec_invented", "remediation": "Do it.", "effort": "low"},
        ]},
        {"sec_encryption_at_rest"},
    )
    assert len(discarded) == 2
    assert "'[sec_encryption_at_rest]'" in discarded[0]
    assert "not an open finding" in discarded[0]


def test_an_entry_with_no_text_is_reported_separately() -> None:
    """A different failure from a wrong id, so it reads differently in the log."""
    _text, _effort, discarded = stages._collect_remediations(
        {"remediations": [{"check_id": "a", "remediation": "   ", "effort": "low"}]},
        {"a"},
    )
    assert discarded == ["'a': empty remediation text"]


def test_filtering_behaviour_itself_is_unchanged() -> None:
    """The third return value is additive. What is kept and dropped is the same."""
    text, effort, _ = stages._collect_remediations(
        {"remediations": [
            {"check_id": "a", "remediation": "Keep.", "effort": "low"},
            {"check_id": "b", "remediation": "", "effort": "low"},
            {"check_id": "zzz", "remediation": "Drop.", "effort": "high"},
        ]},
        {"a", "b"},
    )
    assert text == {"a": "Keep."}
    assert effort == {"a": "low"}


# --------------------------------------------------------------------------- #
# The log — the discriminator the next occurrence needs
# --------------------------------------------------------------------------- #


def test_the_shortfall_log_distinguishes_an_empty_array_from_a_full_discard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The whole reason this logging exists.

    `entries_returned=0` means the model returned nothing — a provider dip, and the
    retry strategy is what wants changing. `entries_returned=3, collected=0` means
    the model answered and we threw it away — the retry was never the problem.
    """
    with caplog.at_level(logging.ERROR, logger="agent.stages"):
        stages._log_shortfall("remediate", {"remediations": []}, {"a", "b"}, [])
    assert "entries_returned=0" in caplog.text
    assert "collected=0" in caplog.text
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="agent.stages"):
        stages._log_shortfall(
            "remediate",
            {"remediations": [{"check_id": "[a]", "remediation": "x", "effort": "low"}]},
            {"a"},
            ["'[a]': not an open finding we asked about"],
        )
    assert "entries_returned=1" in caplog.text
    assert "collected=0" in caplog.text
    assert "discarded=1" in caplog.text
    assert "[a]" in caplog.text


def test_the_shortfall_log_carries_the_raw_payload() -> None:
    """Recoverable nowhere else — ROUTE_LOG stores metadata, not bodies."""
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("agent.stages")
    logger.addHandler(handler)
    try:
        payload = {"executive_summary": "A summary.", "remediations": [],
                   "use_case_notes": []}
        stages._log_shortfall("remediate", payload, {"a"}, [])
    finally:
        logger.removeHandler(handler)

    written = stream.getvalue()
    assert "Raw payload:" in written
    assert "A summary." in written
    # The summary coming back while remediations did not is itself diagnostic: the
    # call completed and the model simply wrote no entries.
    assert '"remediations": []' in written


def test_the_payload_is_truncated_so_one_bad_response_cannot_flood_the_log() -> None:
    import io

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("agent.stages")
    logger.addHandler(handler)
    try:
        stages._log_shortfall(
            "remediate",
            {"remediations": [], "executive_summary": "x" * 50_000},
            {"a"},
            [],
        )
    finally:
        logger.removeHandler(handler)
    assert len(stream.getvalue()) < 4000


def test_a_non_list_remediations_value_does_not_crash_the_logger() -> None:
    """Schema enforcement should prevent this, but a logger that raises while
    reporting a failure loses the only record of that failure."""
    stages._log_shortfall("remediate", {"remediations": None}, {"a"}, [])
    stages._log_shortfall("remediate", {}, {"a"}, [])


# --------------------------------------------------------------------------- #
# The record a user is shown
# --------------------------------------------------------------------------- #


def test_a_review_with_full_guidance_reports_no_gap() -> None:
    review = result(
        finding("a", remediation="Enable SSE-KMS."),
        finding("b", status="partial", remediation="Add a DLQ."),
        finding("c", status="pass"),
    )
    gap = review.remediation_gap
    assert gap.open_findings == 2
    assert gap.without_guidance == 0
    assert gap.any_missing is False
    assert gap.total is False


def test_a_partial_shortfall_names_the_findings_it_is_missing() -> None:
    """A count alone is not checkable. The ids are what let someone confirm it."""
    review = result(
        finding("a", remediation="Enable SSE-KMS."),
        finding("b"),
        finding("c", status="partial"),
    )
    gap = review.remediation_gap
    assert (gap.open_findings, gap.without_guidance) == (3, 2)
    assert gap.check_ids == ["b", "c"]
    assert gap.any_missing is True
    assert gap.total is False


def test_the_observed_failure_reports_as_total() -> None:
    """0 of N, twice — the case that was completely silent."""
    review = result(*(finding(f"c{i}") for i in range(25)))
    gap = review.remediation_gap
    assert gap.open_findings == 25
    assert gap.without_guidance == 25
    assert gap.total is True


def test_whitespace_is_not_guidance() -> None:
    review = result(finding("a", remediation="   \n  "))
    assert review.remediation_gap.without_guidance == 1


def test_a_passing_check_with_no_remediation_is_not_a_gap() -> None:
    """Only open findings get remediation, so a blank on a pass is correct."""
    review = result(
        finding("a", status="pass"),
        finding("b", status="not_applicable"),
    )
    gap = review.remediation_gap
    assert gap.open_findings == 0
    assert gap.without_guidance == 0
    assert gap.total is False, "no open findings is not a total failure"


def test_a_clean_review_with_nothing_open_is_not_reported_as_a_failure() -> None:
    """`remediate` returns early with no model call when nothing is open. That is
    success, and must not read as a total shortfall."""
    assert result().remediation_gap.total is False


def test_the_record_is_computed_so_it_cannot_disagree_with_the_page() -> None:
    """The roadmap renders `finding.remediation`; this counts the same field.

    Stored separately, the two could drift — and the failure mode of a drifting
    warning is worse than no warning, because it is dismissed once and then ignored.
    """
    review = result(finding("a"), finding("b", remediation="Do it."))
    assert review.remediation_gap.without_guidance == 1

    review.findings[0].remediation = "Filled in later."
    assert review.remediation_gap.without_guidance == 0


def test_a_review_stored_before_this_existed_reports_correctly() -> None:
    """Computed, not stored, so there is nothing to migrate."""
    older = ReviewResult.model_validate({
        "review_id": "old",
        "created_at": "t",
        "findings": [json.loads(finding("a").model_dump_json())],
    })
    assert older.remediation_gap.without_guidance == 1
    assert older.remediation_gap.total is True


def test_the_gap_reaches_the_wire() -> None:
    payload = json.loads(result(finding("a"), finding("b")).model_dump_json())
    assert payload["remediation_gap"] == {
        "open_findings": 2,
        "without_guidance": 2,
        "check_ids": ["a", "b"],
    }
    # A count, never a rate: "0% of actions have guidance" would invite reading the
    # complement as a quality figure for guidance that does exist.
    assert "percent" not in payload["remediation_gap"]
    assert "rate" not in payload["remediation_gap"]


def test_the_gap_record_moves_no_score() -> None:
    """Scoring reads statuses and the rubric. Remediation text is not an input."""
    with_text = [finding("a", remediation="Do it."), finding("b", remediation="Do it.")]
    without = [finding("a"), finding("b")]
    assert scoring.score(with_text) == scoring.score(without)

    source = (__import__("pathlib").Path(scoring.__file__)).read_text()
    assert "remediation_gap" not in source
    assert "RemediationGap" not in source


def test_the_model_can_be_built_directly_without_a_review() -> None:
    """Used by the frontend fixtures and by the PDF; it should stand alone."""
    gap = RemediationGap(open_findings=3, without_guidance=3, check_ids=["a", "b", "c"])
    assert gap.total is True
    assert gap.any_missing is True


# --------------------------------------------------------------------------- #
# Through the real stage, reproducing the observed run
# --------------------------------------------------------------------------- #


def test_the_observed_run_end_to_end_through_remediate(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """0 of N, retry, 0 of N again — and what the stage does about it.

    Driven through the real `stages.remediate` with only `complete_json` stubbed, so
    the retry genuinely fires and the logging is exercised where it will actually
    run.
    """
    import llm

    calls: list[str] = []

    def empty(*, system, content, schema, effort, max_tokens, label="",
              temperature=None):
        calls.append(label)
        if label == "remediate-missing":
            return {"remediations": []}, {"input_tokens": 400, "output_tokens": 34}
        return (
            {"executive_summary": "A summary.", "remediations": [],
             "use_case_notes": []},
            {"input_tokens": 3000, "output_tokens": 41},
        )

    monkeypatch.setattr(llm, "complete_json", empty)
    open_findings = [finding(f"c{i}") for i in range(25)]

    with caplog.at_level(logging.WARNING, logger="agent.stages"):
        text, effort, summary, _notes, _usage, _grounding, _quotes = stages.remediate(
            open_findings, {"components": []}, scoreboard="Overall 58.2"
        )

    # The retry fired, once, and no further.
    assert calls == ["remediate", "remediate-missing"]
    assert text == {} and effort == {}
    # The summary still came back, which is itself diagnostic — the call completed.
    assert summary == "A summary."

    # Both payloads are now on the record, and the total failure is called out
    # separately from an ordinary shortfall.
    assert "remediate shortfall" in caplog.text
    assert "remediate-missing shortfall" in caplog.text
    assert "entries_returned=0" in caplog.text
    assert "NO guidance at all" in caplog.text
    assert "25 open findings" in caplog.text


def test_the_retry_re_asks_for_everything_when_the_first_call_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systematic gap, asserted rather than described.

    The retry's design assumes it is the smaller ask — "asking ONLY for what is
    missing... a fraction of the tokens". With zero collected, `missing` is every
    open finding, so it re-asks the same question at the same effort and the same
    max_tokens, with LESS context than the call that just failed. Pinned so a future
    change to the retry has to confront it.
    """
    import llm

    seen: list[dict] = []

    def record(*, system, content, schema, effort, max_tokens, label="",
               temperature=None):
        seen.append({
            "label": label,
            "effort": effort,
            "max_tokens": max_tokens,
            "text": "".join(b.get("text", "") for b in content),
        })
        if label == "remediate-missing":
            return {"remediations": []}, {}
        return {"executive_summary": "s", "remediations": [], "use_case_notes": []}, {}

    monkeypatch.setattr(llm, "complete_json", record)
    open_findings = [finding(f"c{i}") for i in range(25)]
    stages.remediate(
        open_findings, {"components": []}, scoreboard="Overall 58.2",
        context="A read-heavy internal tool.",
    )

    first, retry = seen
    # Every finding is asked about again, not a subset.
    for check in open_findings:
        assert check.check_id in retry["text"]
    # Same knobs.
    assert retry["effort"] == first["effort"]
    assert retry["max_tokens"] == first["max_tokens"]
    # And strictly less context than the call that just failed.
    assert "Scoreboard" in first["text"] and "Scoreboard" not in retry["text"]
    assert "read-heavy" in first["text"] and "read-heavy" not in retry["text"]


def test_a_partial_shortfall_still_only_asks_for_the_missing_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the retry WAS designed for, unchanged."""
    import llm

    seen: list[str] = []

    def partial(*, system, content, schema, effort, max_tokens, label="",
                temperature=None):
        body = "".join(b.get("text", "") for b in content)
        seen.append(body)
        if label == "remediate-missing":
            return {"remediations": [
                {"check_id": "c1", "remediation": "Fix c1.", "effort": "low"}
            ]}, {}
        return {"executive_summary": "s", "remediations": [
            {"check_id": "c0", "remediation": "Fix c0.", "effort": "low"}
        ], "use_case_notes": []}, {}

    monkeypatch.setattr(llm, "complete_json", partial)
    text, _effort, _s, _n, _u, _g, _q = stages.remediate(
        [finding("c0"), finding("c1")], {"components": []}
    )

    assert text == {"c0": "Fix c0.", "c1": "Fix c1."}
    # The retry asked about c1 and not c0 — the smaller ask its design assumes.
    assert "c1" in seen[1] and "c0" not in seen[1]
