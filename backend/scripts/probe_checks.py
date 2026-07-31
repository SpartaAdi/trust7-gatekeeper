"""One real review over one design, printing the verbatim finding records for the
checks you name. Nothing is stubbed and it costs real money.

    # against the deployed service (token from the environment, never printed)
    read -rs -p 'Demo token: ' DEMO_ACCESS_TOKEN && export DEMO_ACCESS_TOKEN
    python scripts/probe_checks.py \
        --diagram ../fixtures/verification/a2i-human-review.drawio \
        --checks ta_human_in_loop rr_validation_before_prod \
        --base-url https://trust7-gatekeeper-backend-docker.onrender.com \
        --out before.json

    # or in-process against your own key, no deployment involved
    python scripts/probe_checks.py --diagram ... --checks ... --out after.json

    # then, once both exist
    python scripts/probe_checks.py --compare before.json after.json    # free, no call

## Why this is separate from accuracy_harness.py

The harness answers "how often is the pipeline right", over a labelled set, in
aggregate. This answers "what exactly did it say about THIS check on THIS design",
which is the question you ask when investigating one behaviour — and the harness
cannot answer it, because it records statuses and discards evidence.

It reuses the harness's `Runner` rather than reimplementing the upload/poll dance, so
a result here and a result there come from the same code path.

## Cost

One review: six model calls, one of them the evaluate stage's 64,000-token request.
`--compare` makes none.

Exit: 0 printed a result | 1 the run failed | 2 no credential
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

#: Printed for each requested check. `evidence` is the point of the whole script —
#: it is what says whether the model saw the mechanism or merely guessed the verdict.
FIELDS = ("status", "severity", "confidence", "title", "evidence",
          "affected_components", "remediation")


def _finding(review: dict, check_id: str) -> dict | None:
    return next(
        (f for f in review.get("findings", []) if f.get("check_id") == check_id), None
    )


def _print_finding(check_id: str, finding: dict | None) -> None:
    print(f"\n{'=' * 78}\n{check_id}\n{'=' * 78}")
    if finding is None:
        print("  NO FINDING RETURNED — the review produced no verdict for this check.")
        return
    for field in FIELDS:
        value = finding.get(field, "")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value) or "(none)"
        print(f"  {field:20} {value if value != '' else '(empty)'}")


def _mentions_human_review(finding: dict | None) -> bool:
    """Whether the evidence actually cites the mechanism, rather than just landing on
    a verdict. A `pass` that never mentions the review loop is a lucky guess, and it
    should not be read as the check working."""
    if finding is None:
        return False
    text = (finding.get("evidence", "") or "").lower()
    return any(
        term in text
        for term in ("human review", "human-in-the-loop", "a2i", "augmented ai",
                     "reviewer", "override", "approval", "approves", "oversight")
    )


def compare(before_path: pathlib.Path, after_path: pathlib.Path) -> int:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())

    print(f"before: {before_path}  ({before.get('review_id', '?')})")
    print(f"after:  {after_path}  ({after.get('review_id', '?')})\n")
    print(f"{'check_id':<32} {'before':<16} {'after':<16} moved")
    print("-" * 78)

    for check_id in sorted(set(before["checks"]) | set(after["checks"])):
        b = (before["checks"].get(check_id) or {}).get("status", "<missing>")
        a = (after["checks"].get(check_id) or {}).get("status", "<missing>")
        print(f"{check_id:<32} {b:<16} {a:<16} {'YES' if a != b else 'no'}")

    print("\nEvidence, side by side:")
    for check_id in sorted(set(before["checks"]) | set(after["checks"])):
        for label, source in (("before", before), ("after", after)):
            entry = source["checks"].get(check_id) or {}
            print(f"\n  [{check_id} · {label}] {entry.get('evidence', '(none)')}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagram", default="")
    parser.add_argument("--document", default="")
    parser.add_argument("--context", default="",
                        help="Optional project-context text, as the UI's field sends it.")
    parser.add_argument("--checks", nargs="+", default=[])
    parser.add_argument("--base-url", default="",
                        help="Deployed service. Omit to drive the app in-process.")
    parser.add_argument("--demo-token", default=os.environ.get("DEMO_ACCESS_TOKEN", ""))
    parser.add_argument("--out", default="", help="Write the result as JSON for --compare.")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), default=None)
    args = parser.parse_args(argv)

    if args.compare:
        return compare(pathlib.Path(args.compare[0]), pathlib.Path(args.compare[1]))

    if not args.diagram and not args.document:
        parser.error("give --diagram, --document, or both")
    if not args.checks:
        parser.error("give --checks with at least one check_id")

    os.environ.setdefault("LOCAL_DATA_DIR", tempfile.mkdtemp(prefix="t7-probe-"))
    if not args.base_url and not os.environ.get("DEMO_ACCESS_TOKEN"):
        os.environ["DEMO_ACCESS_TOKEN"] = "probe-local-token"

    import config
    import rubric

    unknown = sorted(set(args.checks) - set(rubric.checks_by_id()))
    if unknown:
        print(f"Not rubric check_ids: {', '.join(unknown)}")
        return 1

    if not args.base_url:
        try:
            config.llm_api_key()
        except RuntimeError as exc:
            variable = ("OPENROUTER_API_KEY" if config.LLM_PROVIDER == "openrouter"
                        else "ANTHROPIC_API_KEY")
            print(f"BLOCKED — no credential.\n  {type(exc).__name__}: {exc}")
            print(f"\nThis makes REAL calls. Supply the key without pasting it into a "
                  f"chat:\n  printf '{variable}=%s\\n' \"$KEY\" > backend/.env")
            return 2
    elif not args.demo_token:
        print("BLOCKED — --base-url needs the service's DEMO_ACCESS_TOKEN.")
        print("  read -rs -p 'Demo token: ' DEMO_ACCESS_TOKEN && export DEMO_ACCESS_TOKEN")
        return 2

    from scripts.accuracy_harness import Runner

    design = {
        "id": "probe",
        "title": pathlib.Path(args.diagram or args.document).name,
        "document": pathlib.Path(args.document).resolve() if args.document else None,
        "diagram": pathlib.Path(args.diagram).resolve() if args.diagram else None,
        "context": args.context,
    }

    where = args.base_url or "in-process against the real app"
    print(f"provider/model:  {config.LLM_PROVIDER} / {config.MODEL}")
    print(f"transport:       {where}")
    print(f"design:          {design['title']}")
    print("This spends real tokens — one review, six model calls.\n")

    try:
        with Runner(base_url=args.base_url, demo_token=args.demo_token,
                    poll_seconds=3.0) as runner:
            review = runner.review(design)
    except Exception as exc:  # noqa: BLE001 — the message is the useful part here
        print(f"RUN FAILED — {type(exc).__name__}: {exc}")
        return 1

    print(f"review_id:       {review.get('review_id')}")
    print(f"overall_score:   {review.get('overall_score')}")
    detection = review.get("ai_detection") or {}
    print(f"ai verdict:      {detection.get('verdict', '?')}")

    for check_id in args.checks:
        finding = _finding(review, check_id)
        _print_finding(check_id, finding)
        print(f"  {'cites the mechanism?':20} "
              f"{'YES' if _mentions_human_review(finding) else 'NO'}")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps({
            "review_id": review.get("review_id"),
            "overall_score": review.get("overall_score"),
            "checks": {c: _finding(review, c) for c in args.checks},
        }, indent=2))
        print(f"\nWrote {args.out} — pass it to --compare alongside the other run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
