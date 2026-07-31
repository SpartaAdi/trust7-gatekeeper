"""Promptfoo custom provider: one real review, through the real app.

## What this is, and what it is not

Promptfoo's job here is to be a regression harness over failure modes this project
has already found and fixed. It is NOT a second evaluation of the model's quality —
`scripts/accuracy_harness.py` measures that, over the full 45-check label set, with
per-class metrics and no pass/fail threshold anywhere in it. This file exists so that
a handful of *settled* behaviours become an automatic check instead of something
noticed by luck on the next manual harness run.

Promptfoo calls a "provider" with a "prompt". Neither word means what it usually
means here:

* The **prompt** is a ground-truth design id — `design_b_checkout_payments_api`. It is
  an address, not a prompt. The real prompts live in `agent/stages.py` and this file
  deliberately contains none of their text: an eval that carried its own copy of the
  prompt would keep passing after the shipped prompt changed underneath it, which is
  the one thing it exists to prevent.
* The **provider** is the whole pipeline. One call = one full review: ingest,
  normalize, screen, classify, evaluate, the AI gate, prioritize, remediate, scoring.

## Why in-process, through the routes

It reuses `scripts.accuracy_harness.Runner` and `load_ground_truth` directly — the
same objects, not a parallel implementation. Three reasons that mattered more than
the alternatives:

* **Comparability.** A promptfoo result and a harness result come from the same code
  path, so a disagreement between them is a real finding rather than a difference in
  how the two were wired.
* **It exercises what the failure lived in.** The 46-point AI-gate failure was in
  `agent/pipeline.py`, not in a prompt. Calling `stages.evaluate` directly would test
  the stage and skip the gate; going through `POST /uploads` -> `POST /reviews` ->
  `GET /reviews/{id}` covers ingest, the gate, and scoring, and it is what the browser
  does.
* **No server, no network.** `TestClient` drives the real `main.app` object in this
  process. Only the OpenRouter calls leave the machine.

`--base-url` (an HTTP transport against a deployed instance) is deliberately NOT
plumbed through. It would double the ways an eval can fail, and a regression eval
wants one.

## Cost, and the cache that bounds it

A review is 6 model calls, two of them the evaluate stage's large-output request, and
observed latency on this provider has spanned 14s to 44 minutes. Several test cases
ask questions of the SAME design, so the review is cached on disk and reused across
processes — promptfoo spawns this file per call, so an in-memory memo would not
survive.

The cache key is a digest of everything whose change could change the answer: the
rubric, the stage prompts, the gate, the detector, scoring, the model id, and the
design's own bytes. Edit a prompt and the cache misses, which is exactly the case the
eval is for. Nothing is reused across a change it should not survive, and
`TRUST7_EVAL_CACHE=off` refuses the cache entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tempfile
from typing import Any

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GROUND_TRUTH_DIR = REPO / "fixtures" / "ground_truth"
CACHE_DIR = REPO / "local-data" / "promptfoo-cache"

#: Files whose content is allowed to invalidate a cached review. Every one of them can
#: change a verdict; nothing else in the tree can. Listed rather than globbed so that
#: adding a file to this set is a decision someone made, and so an unrelated edit
#: (a README, a frontend view) does not force six paid calls.
CACHE_INPUTS = (
    REPO / "rubric" / "rubric.json",
    BACKEND / "agent" / "stages.py",
    BACKEND / "agent" / "pipeline.py",
    BACKEND / "agent" / "ai_gate.py",
    BACKEND / "agent" / "ai_detection.py",
    BACKEND / "rubric.py",
    BACKEND / "scoring.py",
)


def _digest(design: dict[str, Any]) -> str:
    """A fingerprint of every input that could change this design's verdicts."""
    import config

    sha = hashlib.sha256()
    sha.update(f"{config.LLM_PROVIDER}/{config.MODEL}\n".encode())
    for path in CACHE_INPUTS:
        sha.update(path.read_bytes() if path.is_file() else b"<missing>")
    for key in ("document", "diagram"):
        value = design.get(key)
        if value:
            sha.update(pathlib.Path(value).read_bytes())
    sha.update(json.dumps(design["labels"], sort_keys=True).encode())
    return sha.hexdigest()[:16]


def _cached(design_id: str, digest: str) -> dict[str, Any] | None:
    if os.environ.get("TRUST7_EVAL_CACHE", "").lower() == "off":
        return None
    path = CACHE_DIR / f"{design_id}.json"
    if not path.is_file():
        return None
    try:
        entry = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    # A digest mismatch is the interesting case: it means a prompt, the rubric, the
    # gate or the design itself changed since this review was stored, so reusing it
    # would report yesterday's pipeline as today's.
    return entry["review"] if entry.get("digest") == digest else None


def _store(design_id: str, digest: str, review: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{design_id}.json").write_text(
        json.dumps({"digest": digest, "review": review}, indent=2)
    )


def summarise(review: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    """The shape the assertions read. Kept small and flat on purpose.

    Separate from `call_api` so `tests/test_promptfoo_config.py` can build one from a
    hand-written review and prove the assertions fail when they should — offline, with
    no API call. An eval whose assertions have never been seen to fail is decoration.
    """
    findings = review.get("findings") or []
    return {
        "design": design["id"],
        "title": review.get("title", ""),
        "overall_score": review.get("overall_score"),
        "ai_verdict": (review.get("ai_detection") or {}).get("verdict", "not_run"),
        "statuses": {f["check_id"]: f["status"] for f in findings},
        "evidence": {f["check_id"]: f.get("evidence", "") for f in findings},
        "framework_scores": {
            f["framework"]: f["score"] for f in review.get("frameworks") or []
        },
    }


def call_api(prompt: str, options: dict[str, Any] | None = None,
             context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Promptfoo's entry point. `prompt` is a ground-truth design id."""
    design_id = (prompt or "").strip()
    if not design_id:
        return {"error": "no design id was passed as the prompt"}

    # Before `config` is imported, and local to this process: the demo gate fails
    # closed, so the in-process app needs a token to answer at all, and the run's
    # data must not land in the developer's real local-data.
    os.environ.setdefault("LOCAL_DATA_DIR", tempfile.mkdtemp(prefix="t7-promptfoo-"))
    os.environ.setdefault("DEMO_ACCESS_TOKEN", "promptfoo-local-token")

    import config
    from scripts.accuracy_harness import LabelError, Runner, load_ground_truth

    try:
        designs = load_ground_truth(GROUND_TRUTH_DIR, [design_id])
    except LabelError as exc:
        return {"error": f"ground truth: {exc}"}
    design = designs[0]
    serialisable = {
        "id": design["id"],
        "labels": design["labels"],
        "document": str(design["document"]) if design["document"] else "",
        "diagram": str(design["diagram"]) if design["diagram"] else "",
    }

    digest = _digest(serialisable)
    review = _cached(design_id, digest)
    if review is not None:
        return {
            "output": json.dumps(summarise(review, design)),
            "cached": True,
            # Zeroed rather than repeated: the tokens were paid for on the run that
            # populated the cache, and counting them again would overstate the cost
            # of this eval in promptfoo's own summary.
            "tokenUsage": {"total": 0, "prompt": 0, "completion": 0, "cached": 1},
        }

    try:
        config.llm_api_key()
    except RuntimeError as exc:
        variable = (
            "OPENROUTER_API_KEY" if config.LLM_PROVIDER == "openrouter"
            else "ANTHROPIC_API_KEY"
        )
        return {
            "error": (
                f"no credential — this eval makes REAL paid calls and cannot run "
                f"without one ({type(exc).__name__}: {exc}). Set {variable} in "
                f"backend/.env or the environment; never paste it into a prompt."
            )
        }

    try:
        with Runner(base_url="", demo_token="", poll_seconds=2.0) as runner:
            review = runner.review(design)
    except Exception as exc:  # noqa: BLE001 — promptfoo wants the message, not a trace
        return {"error": f"{type(exc).__name__}: {exc}"}

    _store(design_id, digest, review)
    usage = review.get("token_usage") or {}
    return {
        "output": json.dumps(summarise(review, design)),
        "tokenUsage": {
            "prompt": usage.get("input_tokens", 0),
            "completion": usage.get("output_tokens", 0),
            "total": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            "cached": usage.get("cache_read_input_tokens", 0),
        },
    }
