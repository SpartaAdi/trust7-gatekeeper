"""The live token/cost counter on ReviewStatus.

The point of this is that it is LIVE. A total that only appears once the review
finishes tells a reviewer nothing during the 5-40 minutes they are watching a
progress screen, which is exactly when "is this spending, or is it stuck?" is the
question. So the tests that matter are the ones about it being published DURING the
run, not the ones about the arithmetic.

The cost figure is an upper bound, never a bill — see the pricing block in config.py
for why it is the maximum of the three locked providers rather than a blend.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import config
import llm
import rubric
import storage as storage_module
from agent import pipeline

DEMO_TOKEN = "token-counter-token"

SOW = (
    b"# Payments platform\n\n"
    b"A managed API gateway in front of a DynamoDB table, with S3 for archives. "
    b"Traffic is read-heavy and the data is customer-identifying. " * 12
)

# Distinct per-stage numbers so a total can only be right by adding the right things.
USAGE = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 400}


def _stub(calls: list[str]):
    def fake(*, system, content, schema, effort, max_tokens, label="", temperature=None):
        calls.append(label)
        required = set(schema.get("required", []))
        if "verdict" in required:
            return {
                "verdict": "reviewable", "subject": "a statement of work",
                "reason": "r", "confidence": "high",
            }, dict(USAGE)
        if "design_summary" in required:
            return {
                "design_summary": "x",
                "components": [{"id": "c0", "label": "API", "kind": "gateway",
                                "provider": "aws", "service": "Amazon API Gateway",
                                "attributes": []}],
                "data_flows": [], "observations": [], "absent": [],
            }, dict(USAGE)
        if "findings" in required:
            return {"findings": [
                {"check_id": c.check_id, "status": "fail", "severity": c.severity,
                 "severity_rationale": "s", "title": c.description, "evidence": "e",
                 "affected_components": []}
                for c in rubric.all_checks()
            ]}, dict(USAGE)
        if "ranking" in required:
            return {"summary": "- s", "ranking": []}, dict(USAGE)
        return {"executive_summary": "s", "remediations": [], "use_case_notes": []}, dict(USAGE)

    return fake


@pytest.fixture()
def client(monkeypatch, tmp_path):
    import main

    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    importlib.reload(storage_module)
    monkeypatch.setattr(config, "DEMO_ACCESS_TOKEN", DEMO_TOKEN)
    return TestClient(main.app, headers={config.DEMO_TOKEN_HEADER: DEMO_TOKEN})


def _run(client: TestClient) -> str:
    key = client.post(
        "/uploads", files={"file": ("sow.md", SOW, "text/markdown")}
    ).json()["key"]
    accepted = client.post("/reviews", json={"document_key": key})
    assert accepted.status_code == 202
    return accepted.json()["review_id"]


# --------------------------------------------------------------------------- #
# Live-ness — the reason this exists
# --------------------------------------------------------------------------- #

def test_the_total_is_published_during_the_run_not_only_at_the_end(
    client, monkeypatch
) -> None:
    """A counter that only appears at the end is not a counter.

    Snapshots every status write and asserts the token total takes more than one
    distinct non-zero value — i.e. it was republished as stages completed, rather
    than written once when the pipeline finished.
    """
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(calls))

    seen: list[int] = []
    real_put = storage_module.put_status

    def spy(status):
        seen.append(status.token_usage.get("input_tokens", 0))
        return real_put(status)

    monkeypatch.setattr(storage_module, "put_status", spy)

    _run(client)

    growing = sorted({n for n in seen if n})
    assert len(growing) > 1, f"total never changed mid-run: {seen}"
    # And it only ever goes up — a running total that dips has lost a stage.
    nonzero = [n for n in seen if n]
    assert nonzero == sorted(nonzero), f"total went backwards: {nonzero}"


def test_the_status_total_matches_the_stored_result_total(client, monkeypatch) -> None:
    """The live figure and the settled figure are the same number, not two
    accountings that can disagree."""
    calls: list[str] = []
    monkeypatch.setattr(llm, "complete_json", _stub(calls))

    review_id = _run(client)
    status = client.get(f"/reviews/{review_id}/status").json()
    result = client.get(f"/reviews/{review_id}").json()

    assert status["token_usage"] == result["token_usage"]
    assert status["token_usage"]["input_tokens"] == len(calls) * USAGE["input_tokens"]
    assert status["token_usage"]["output_tokens"] == len(calls) * USAGE["output_tokens"]


# --------------------------------------------------------------------------- #
# The cost estimate
# --------------------------------------------------------------------------- #

def test_the_cost_is_input_times_prompt_plus_output_times_completion() -> None:
    totals = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}

    assert pipeline.estimated_cost(totals) == pytest.approx(
        config.OPENROUTER_PRICE_PROMPT_USD * 1_000_000
        + config.OPENROUTER_PRICE_COMPLETION_USD * 1_000_000
    )


def test_cached_input_is_not_charged_a_second_time() -> None:
    """`cache_read_input_tokens` is a SUBSET of `input_tokens`, not an addition.

    Summing every key in the usage dict — the obvious implementation — would bill the
    cached half of the prompt twice and inflate the figure on exactly the long
    documents where it matters most.
    """
    without = {"input_tokens": 1000, "output_tokens": 100}
    with_cache = {"input_tokens": 1000, "output_tokens": 100,
                  "cache_read_input_tokens": 900}

    assert pipeline.estimated_cost(without) == pipeline.estimated_cost(with_cache)


def test_the_price_constants_are_the_ceiling_of_the_three_locked_providers() -> None:
    """Pins the figures read from OpenRouter's endpoints API on 2026-08-25, per
    million tokens:

        decart/fp4       0.5493 prompt   2.3128 completion
        inceptron/int4   0.5700 prompt   3.3900 completion
        coreweave/fp4    0.6500 prompt   3.4100 completion

    If a price moves, this fails and the comment in config.py gets re-checked against
    the live endpoint rather than drifting silently.
    """
    prompt_per_million = config.OPENROUTER_PRICE_PROMPT_USD * 1_000_000
    completion_per_million = config.OPENROUTER_PRICE_COMPLETION_USD * 1_000_000

    assert prompt_per_million == pytest.approx(max(0.5493, 0.5700, 0.6500))
    assert completion_per_million == pytest.approx(max(2.3128, 3.3900, 3.4100))


def test_the_cost_never_understates_whichever_provider_served() -> None:
    """The property the ceiling exists for. Whichever of the three actually served a
    call, the reported figure is >= what it really cost at list price."""
    totals = {"input_tokens": 500_000, "output_tokens": 200_000}
    reported = pipeline.estimated_cost(totals)

    for prompt, completion in ((0.5493, 2.3128), (0.5700, 3.3900), (0.6500, 3.4100)):
        actual = (
            totals["input_tokens"] * prompt / 1_000_000
            + totals["output_tokens"] * completion / 1_000_000
        )
        assert reported >= actual - 1e-12, f"understated vs {prompt}/{completion}"


def test_an_empty_total_costs_nothing_rather_than_raising(client) -> None:
    """A status polled before the first stage finishes has no usage yet."""
    assert pipeline.estimated_cost({}) == 0.0
