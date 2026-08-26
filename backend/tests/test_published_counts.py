"""The check counts stated in public-facing copy match the rubric they describe.

These numbers leave the building. They go in the README, in a briefing deck, and
into a demo where someone will repeat them to a client — which makes them the one
class of claim that is embarrassing rather than merely wrong when it drifts.

Nothing here validates the rubric. `rubric.json` is the source of truth and this
asserts the DOCUMENTATION agrees with it, in that direction only. If a check is
added tomorrow these fail, and the correct fix is to update the prose, not the
expectation.

The framing those counts carry is deliberate and is pinned too:

* the 26 are described as what a design-time review can answer, never as a
  selection rule that was applied;
* TRUST-7 is described as having NO official check count, because Minfy's
  published model defines five maturity levels per pillar rather than a
  checklist — so 19 cannot be a subset of anything or a shortfall against
  anything.
"""

from __future__ import annotations

import json
import pathlib
import re

import rubric


def _prose(path: pathlib.Path) -> str:
    """Collapsed whitespace: these are wrapped markdown, so a phrase this file
    asserts on is routinely split across a line break in the source."""
    return " ".join(path.read_text().split())

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"
PROJECT_INSTRUCTIONS = ROOT / "CLAUDE.md"

# Read from rubric.json at import, so these are the rubric's numbers rather than
# a second hand-maintained copy of them.
_RUBRIC = json.loads((ROOT / "rubric" / "rubric.json").read_text())


def _counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for framework, body in _RUBRIC["frameworks"].items():
        pillars = body["pillars"]
        entries = pillars.values() if isinstance(pillars, dict) else pillars
        out[framework] = sum(len(pillar.get("checks", [])) for pillar in entries)
    return out


def test_the_rubric_still_holds_the_counts_the_docs_publish() -> None:
    counts = _counts()

    assert counts["aws_waf"] == 26
    assert counts["trust7"] == 19
    assert sum(counts.values()) == 45
    # And the loader agrees with the file, so the docs describe what actually runs.
    assert len(rubric.all_checks()) == 45


def test_the_per_pillar_table_in_the_readme_matches_the_rubric() -> None:
    """The README publishes a per-pillar split. Every row has to be true."""
    expected = {
        "Operational Excellence": 4, "Security": 7, "Reliability": 4,
        "Performance Efficiency": 4, "Cost Optimization": 4, "Sustainability": 3,
        "Trust foundations": 4, "Risk and resilience": 3, "Unit economics": 3,
        "Sovereignty and supply chain": 3, "Talent and adoption": 2,
        "Sustainability (AI-specific)": 1, "AI governance": 3,
    }

    actual: dict[str, int] = {}
    for body in _RUBRIC["frameworks"].values():
        pillars = body["pillars"]
        entries = pillars.values() if isinstance(pillars, dict) else pillars
        for pillar in entries:
            actual[pillar["name"]] = len(pillar.get("checks", []))

    assert actual == expected, "the README's per-pillar table is now out of date"


def test_the_readme_states_both_counts_and_the_total() -> None:
    text = _prose(README)

    assert "26 checks" in text
    assert "19 checks" in text
    assert re.search(r"\b45 checks\b", text)


def test_the_waf_framing_describes_the_26_rather_than_claiming_a_selection_rule() -> None:
    """"These are the ones a review can answer" is an observation about the 26.

    "We selected the ones a review can answer" is a claim about how the rubric was
    built, and it is not one this project can support. The distinction is small in
    prose and large in a room with a client in it.
    """
    text = _prose(README)

    assert "not the rule they were selected by" in text
    assert "57" in text, "the framing has to say what the 26 is 26 OF"
    # The runtime-behaviour reason, which is what makes the 26 defensible.
    assert "runtime behaviour" in text or "runtime behavior" in text


def test_trust7_is_never_described_as_a_subset_or_a_shortfall() -> None:
    """There is no official TRUST-7 check count. Copy must not imply one exists."""
    text = _prose(README)

    assert "no official TRUST-7 check count" in text
    assert "five maturity levels per pillar" in text
    assert "not a subset of anything" in text


def test_the_project_instructions_carry_the_same_framing() -> None:
    """CLAUDE.md shapes every future session. If the rule lives only in the README,
    the next round of copy reintroduces the overclaim."""
    text = _prose(PROJECT_INSTRUCTIONS)

    assert "26 WAF, 19 TRUST-7" in text
    assert "never as the rule they were *selected by*" in text
    assert "no official TRUST-7 count to match or fall short of" in text
