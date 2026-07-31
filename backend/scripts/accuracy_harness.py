"""Ground-truth accuracy harness. Standalone — not imported by the running app.

Runs each labelled design in `fixtures/ground_truth/` through the REAL pipeline
over the REAL provider, several times, and reports how the pipeline's verdicts
compare with the labels — per class, per pillar, and run to run.

    python scripts/accuracy_harness.py                       # 3 runs per design
    python scripts/accuracy_harness.py --repeats 1            # one pass, cheaper
    python scripts/accuracy_harness.py --designs design_b_checkout_payments_api
    python scripts/accuracy_harness.py --base-url https://... --demo-token ...
    python scripts/accuracy_harness.py --check-labels         # no API calls at all

Nothing is stubbed. Uploads go through `POST /uploads`, the review through
`POST /reviews`, the result through `GET /reviews/{id}` — the same routes the
frontend calls, with the demo gate in front of them and the pipeline running as a
background task. By default those routes are driven in-process through FastAPI's
TestClient, which exercises the real app object; `--base-url` points the same
sequence at a deployed instance over the network instead.

## What it reports, and what it deliberately does not

Raw numbers only. This script computes and prints; it draws no conclusion about
whether a figure is good, and there is no pass/fail threshold anywhere in it. A
threshold would need a target nobody has agreed, and inventing one here would
turn a measurement into a verdict.

Three cuts, because one number cannot carry this:

* **Per-class precision / recall / F1** over the four statuses, one-vs-rest. This
  is the cut the four-way verdict actually calls for. `not_applicable` gets its
  own row, which matters more here than it looks: the non-AI design is labelled
  n/a on 19 of TRUST-7's 19 checks, so an evaluator that never says n/a can still
  post a respectable overall accuracy while being wrong about the entire
  framework.
* **Macro and micro averages.** Macro is the unweighted mean of the per-class
  F1s, so a class with three instances counts as much as one with thirty. Micro,
  for single-label multi-class, is exactly overall accuracy. They diverge sharply
  on skewed label sets, which is why both are printed.
* **The open-gap binary view**: positive = `fail` or `partial`, negative = `pass`
  or `not_applicable`. Coarser than the four-way cut and reported alongside it,
  because "did it find the gap at all" is the question the tool exists to answer,
  and a `fail`/`partial` mix-up is a different kind of error from missing a gap.

Metrics are computed per run and reported per run. There is no single headline
number, because with any sampling variance left one run's figure is a sample
rather than a measurement — the spread across runs is itself a result. A
`majority` row is also reported: the modal verdict per check across the runs,
which is what a best-of-N pipeline would produce.

Borderline labels are counted in the headline figures and reported again with
them excluded. Some checks in the fixture set are genuinely arguable, they are
marked `"confidence": "borderline"` with the argument recorded, and a
disagreement on one of those is at least as likely to be a label problem as a
pipeline problem. Both cuts are shown so neither reading is hidden.

## Cost

`--repeats 3` over two designs is 6 full reviews, and a review is 6 model calls
(2 of them the evaluate stage's 64k-output request). That is real money on a
pay-per-token key. `--repeats 1` and `--designs` exist for that reason, and
`--check-labels` validates the fixture set against the rubric with no API call at
all.

Exit: 0 the runs completed | 2 no credential | 1 a run failed or a label is invalid
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import tempfile
import time
import traceback
from typing import Any

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

GROUND_TRUTH_DIR = REPO / "fixtures" / "ground_truth"

STATUSES = ("pass", "partial", "fail", "not_applicable")
OPEN_STATUSES = frozenset({"fail", "partial"})

# What a check's verdict is recorded as when a run never returned one. Kept out of
# STATUSES so it can never be silently averaged in as if it were a verdict: a
# missing verdict is a failed measurement, and it is counted and reported as one.
MISSING = "<missing>"


# --------------------------------------------------------------------------- #
# Ground-truth loading
# --------------------------------------------------------------------------- #

class LabelError(RuntimeError):
    """The ground-truth set does not agree with the rubric."""


def load_ground_truth(directory: pathlib.Path, wanted: list[str]) -> list[dict[str, Any]]:
    """Read the labelled designs, validating every label against the live rubric.

    Validation is strict and happens before any API call, because every failure
    mode here silently corrupts the metrics rather than raising later:

    * a `check_id` that is not in the rubric is scored against nothing, so it
      inflates the miss count for a check that does not exist;
    * a status outside the enum lands in no class and disappears from both
      precision and recall;
    * a design labelling only some checks yields metrics over a subset while
      looking like a full run, so the covered count is reported per design and a
      partial set is allowed but never silent.
    """
    import rubric

    known = set(rubric.checks_by_id())
    if not directory.is_dir():
        raise LabelError(
            f"No ground-truth directory at {directory}. Each design is a JSON file "
            f"there; see {directory / 'README.md'} for the format."
        )

    designs: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text())
        design_id = raw.get("id") or path.stem
        if wanted and design_id not in wanted:
            continue

        labels = raw.get("labels") or {}
        if not labels:
            raise LabelError(f"{path.name}: no `labels` object.")

        unknown = sorted(set(labels) - known)
        if unknown:
            raise LabelError(
                f"{path.name}: label(s) for check_id(s) not in the rubric: "
                f"{', '.join(unknown)}"
            )

        normalised: dict[str, dict[str, str]] = {}
        for check_id, entry in labels.items():
            # A bare string is accepted as shorthand for {"status": ...}, so a
            # hand-written set does not have to carry the rationale fields.
            if isinstance(entry, str):
                entry = {"status": entry}
            status = entry.get("status", "")
            if status not in STATUSES:
                raise LabelError(
                    f"{path.name}: {check_id} is labelled {status!r}; must be one "
                    f"of {', '.join(STATUSES)}"
                )
            confidence = entry.get("confidence", "clear")
            if confidence not in ("clear", "borderline"):
                raise LabelError(
                    f"{path.name}: {check_id} has confidence {confidence!r}; must "
                    f"be 'clear' or 'borderline'"
                )
            normalised[check_id] = {"status": status, "confidence": confidence}

        designs.append(
            {
                "id": design_id,
                "title": raw.get("title", design_id),
                "path": path,
                "document": _resolve(path, raw.get("document")),
                "diagram": _resolve(path, raw.get("diagram")),
                "context": raw.get("context", ""),
                "labels": normalised,
            }
        )

    if wanted:
        missing = sorted(set(wanted) - {d["id"] for d in designs})
        if missing:
            raise LabelError(f"No ground-truth design named: {', '.join(missing)}")
    if not designs:
        raise LabelError(f"No ground-truth designs found in {directory}.")
    return designs


def _resolve(json_path: pathlib.Path, value: Any) -> pathlib.Path | None:
    """A design's document or diagram, as a path relative to its JSON file.

    Kept as separate files rather than inlined text so a real SoW — a PDF, a
    .docx, a PNG diagram — can be dropped in unchanged and go through the same
    `POST /uploads` route the browser uses, exercising `ingestion/documents.py`
    and the vision path rather than bypassing them.
    """
    if not value:
        return None
    path = (json_path.parent / str(value)).resolve()
    if not path.is_file():
        raise LabelError(f"{json_path.name}: references {value!r}, which is not a file")
    return path


# --------------------------------------------------------------------------- #
# Running one review, through the real routes
# --------------------------------------------------------------------------- #

class Runner:
    """Drives `/uploads` -> `/reviews` -> `/reviews/{id}` against the real app."""

    def __init__(self, base_url: str, demo_token: str, poll_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.demo_token = demo_token
        self.poll_seconds = poll_seconds
        self._client: Any = None

    def __enter__(self) -> Runner:
        import config

        if self.base_url:
            import httpx

            # Per-request read timeout well above a single stage: the POST that
            # starts the review returns 202 immediately, but a status poll must not
            # give up on a slow instance.
            self._client = httpx.Client(base_url=self.base_url, timeout=120.0)
        else:
            from fastapi.testclient import TestClient

            import main

            self._client = TestClient(main.app)
            self.demo_token = self.demo_token or config.DEMO_ACCESS_TOKEN
        return self

    def __exit__(self, *exc: object) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()

    @property
    def _headers(self) -> dict[str, str]:
        import config

        return {config.DEMO_TOKEN_HEADER: self.demo_token}

    def upload(self, path: pathlib.Path) -> str:
        with path.open("rb") as handle:
            response = self._client.post(
                "/uploads",
                files={"file": (path.name, handle, "application/octet-stream")},
                headers=self._headers,
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"POST /uploads for {path.name} returned {response.status_code}: "
                f"{response.text[:400]}"
            )
        return response.json()["key"]

    def review(self, design: dict[str, Any]) -> dict[str, Any]:
        """One full review. Returns the parsed ReviewResult JSON."""
        body = {
            "title": design["title"],
            "context": design["context"],
            "document_key": self.upload(design["document"]) if design["document"] else "",
            "diagram_key": self.upload(design["diagram"]) if design["diagram"] else "",
        }

        response = self._client.post("/reviews", json=body, headers=self._headers)
        if response.status_code != 202:
            raise RuntimeError(
                f"POST /reviews returned {response.status_code}: {response.text[:400]}"
            )
        review_id = response.json()["review_id"]

        # In-process, Starlette runs the background task before the POST returns, so
        # the result is already there and this loop makes one pass. Over the network
        # it is the real poll the frontend does. One code path for both.
        while True:
            result = self._client.get(f"/reviews/{review_id}", headers=self._headers)
            if result.status_code == 200:
                return result.json()
            if result.status_code != 409:
                raise RuntimeError(
                    f"GET /reviews/{review_id} returned {result.status_code}: "
                    f"{result.text[:400]}"
                )
            status = self._client.get(
                f"/reviews/{review_id}/status", headers=self._headers
            ).json()
            if status.get("state") in ("error", "cancelled"):
                raise RuntimeError(
                    f"Review {review_id} ended {status.get('state')}: "
                    f"{status.get('error') or 'no error recorded'}"
                )
            time.sleep(self.poll_seconds)


# --------------------------------------------------------------------------- #
# Metrics
#
# Written out longhand rather than pulled from scikit-learn: the arithmetic is
# twenty lines, the dependency is not in requirements.txt, and a reviewer
# checking a precision figure by hand should be able to read the definition it
# came from in the same file.
# --------------------------------------------------------------------------- #

def _prf(true_positive: int, false_positive: int, false_negative: int) -> dict[str, float]:
    """Precision, recall, F1 for one class. Zero denominators report 0.0.

    0.0 rather than None: a class the pipeline never predicted has no meaningful
    precision, and reporting 0.0 with the support counts alongside it keeps the
    table arithmetic-checkable. The supports are always printed for exactly this
    reason — a 0.0 with support 0 means something different from a 0.0 with
    support 12.
    """
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
    }


def metrics(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Per-class, macro, micro and open-gap metrics over (truth, predicted) pairs."""
    per_class: dict[str, dict[str, Any]] = {}
    for status in STATUSES:
        true_positive = sum(1 for t, p in pairs if t == status and p == status)
        false_positive = sum(1 for t, p in pairs if t != status and p == status)
        false_negative = sum(1 for t, p in pairs if t == status and p != status)
        entry = _prf(true_positive, false_positive, false_negative)
        entry["support"] = sum(1 for t, _ in pairs if t == status)
        entry["predicted"] = sum(1 for _, p in pairs if p == status)
        per_class[status] = entry

    # Macro over the classes actually present in the truth labels. Averaging in a
    # class with no instances would drag every macro figure toward zero by an
    # amount that depends only on the rubric's shape, not on the pipeline.
    present = [s for s in STATUSES if per_class[s]["support"]]
    macro = {
        key: round(
            statistics.fmean([per_class[s][key] for s in present]) if present else 0.0, 4
        )
        for key in ("precision", "recall", "f1")
    }

    correct = sum(1 for t, p in pairs if t == p)
    # For single-label multi-class, micro precision == micro recall == accuracy:
    # every pair contributes exactly one prediction and one truth, so summed over
    # classes the false positives and false negatives are the same set counted
    # twice. Stated because a table showing three identical numbers otherwise
    # looks like a bug.
    micro_value = round(correct / len(pairs), 4) if pairs else 0.0

    open_tp = sum(1 for t, p in pairs if t in OPEN_STATUSES and p in OPEN_STATUSES)
    open_fp = sum(1 for t, p in pairs if t not in OPEN_STATUSES and p in OPEN_STATUSES)
    open_fn = sum(1 for t, p in pairs if t in OPEN_STATUSES and p not in OPEN_STATUSES)

    return {
        "n": len(pairs),
        "correct": correct,
        "accuracy": micro_value,
        "per_class": per_class,
        "macro": macro,
        "micro": {"precision": micro_value, "recall": micro_value, "f1": micro_value},
        "open_gap_binary": _prf(open_tp, open_fp, open_fn),
        "confusion": {
            truth: {
                predicted: sum(1 for t, p in pairs if t == truth and p == predicted)
                for predicted in (*STATUSES, MISSING)
            }
            for truth in STATUSES
        },
        "missing_verdicts": sum(1 for _, p in pairs if p == MISSING),
    }


def pairs_for(
    labels: dict[str, dict[str, str]],
    verdicts: dict[str, str],
    *,
    only_clear: bool = False,
    pillar_of: dict[str, str] | None = None,
    pillar: str | None = None,
) -> list[tuple[str, str]]:
    """(truth, predicted) for every labelled check, optionally filtered."""
    out: list[tuple[str, str]] = []
    for check_id, label in sorted(labels.items()):
        if only_clear and label["confidence"] != "clear":
            continue
        if pillar is not None and (pillar_of or {}).get(check_id) != pillar:
            continue
        out.append((label["status"], verdicts.get(check_id, MISSING)))
    return out


def variance_across_runs(
    labels: dict[str, dict[str, str]], runs: list[dict[str, Any]]
) -> dict[str, Any]:
    """How much the verdicts moved between repeats of the identical design.

    This is the measurement the temperature change is judged by, and it is
    separate from accuracy on purpose: a pipeline can be stably wrong or unstably
    right, and only one of those is fixed by turning the sampling down.

    `unstable` lists every check whose verdict was not identical across all runs,
    with the sequence it took, because the aggregate rate says nothing about
    whether the movement is concentrated in a few genuinely ambiguous checks or
    spread across the rubric.
    """
    verdict_sets = [r["verdicts"] for r in runs]
    check_ids = sorted(labels)

    unstable: dict[str, list[str]] = {}
    for check_id in check_ids:
        seen = [v.get(check_id, MISSING) for v in verdict_sets]
        if len(set(seen)) > 1:
            unstable[check_id] = seen

    scores = [r["overall_score"] for r in runs]
    return {
        "runs": len(runs),
        "checks_compared": len(check_ids),
        "identical_across_all_runs": len(check_ids) - len(unstable),
        "unstable_check_count": len(unstable),
        "verdict_agreement_rate": (
            round((len(check_ids) - len(unstable)) / len(check_ids), 4)
            if check_ids
            else 0.0
        ),
        "fully_identical": not unstable,
        "unstable": unstable,
        "overall_score_per_run": scores,
        "overall_score_spread": round(max(scores) - min(scores), 2) if scores else 0.0,
        "overall_score_stdev": (
            round(statistics.stdev(scores), 3) if len(scores) > 1 else 0.0
        ),
        "pillar_score_spread": _pillar_spread(runs),
    }


def _pillar_spread(runs: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Min, max and spread of each pillar's score across the runs."""
    collected: dict[str, list[float]] = {}
    for run in runs:
        for key, score in run["pillar_scores"].items():
            collected.setdefault(key, []).append(score)
    return {
        key: {
            "min": min(scores),
            "max": max(scores),
            "spread": round(max(scores) - min(scores), 2),
        }
        for key, scores in sorted(collected.items())
    }


def majority_verdicts(runs: list[dict[str, Any]], check_ids: list[str]) -> dict[str, str]:
    """The modal verdict per check across runs; the first run breaks a tie.

    A tie means the runs split evenly with no majority — at 3 runs that is a
    three-way split. Falling back to the first run rather than to a sentinel keeps
    the majority row a real, reproducible set of verdicts; the tie itself is
    already visible in `variance.unstable`.
    """
    out: dict[str, str] = {}
    for check_id in check_ids:
        seen = [r["verdicts"].get(check_id, MISSING) for r in runs]
        counts: dict[str, int] = {}
        for verdict in seen:
            counts[verdict] = counts.get(verdict, 0) + 1
        best = max(counts.values())
        winners = [v for v, c in counts.items() if c == best]
        out[check_id] = seen[0] if len(winners) > 1 else winners[0]
    return out


# --------------------------------------------------------------------------- #
# Report rendering
# --------------------------------------------------------------------------- #

def _table(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _open_gap(binary: dict[str, Any], key: str) -> str:
    """An open-gap figure, or an em dash when the question does not arise.

    A pillar whose every check is `not_applicable` has no positives in the truth
    labels and none predicted, so precision, recall and F1 are all 0.0 by the
    formula. Printed as 0.000 that reads as a total failure to find gaps, when in
    fact there were no gaps to find and the pipeline correctly reported none. The
    non-AI design in the fixture set makes six whole pillars look like that, so
    this is the common case rather than an edge one.
    """
    if not (binary["true_positive"] + binary["false_positive"] + binary["false_negative"]):
        return "—"
    return f"{binary[key]:.3f}"


def _prf_rows(per_class: dict[str, dict[str, Any]]) -> list[list[str]]:
    return [
        [
            status,
            f"{m['precision']:.3f}",
            f"{m['recall']:.3f}",
            f"{m['f1']:.3f}",
            str(m["support"]),
            str(m["predicted"]),
            str(m["true_positive"]),
            str(m["false_positive"]),
            str(m["false_negative"]),
        ]
        for status, m in per_class.items()
    ]


def render_markdown(report: dict[str, Any]) -> str:
    out: list[str] = [
        "# Trust7 Gatekeeper — ground-truth accuracy harness",
        "",
        "Raw output. Nothing here is interpreted and there is no pass/fail threshold.",
        "",
        f"- generated: `{report['generated_at']}`",
        f"- provider / model: `{report['provider']}` / `{report['model']}`",
        f"- evaluate-stage temperature: `{report['evaluate_temperature']}`",
        f"- routing order: `{report['provider_order']}`, "
        f"allow_fallbacks=`{report['allow_fallbacks']}`",
        f"- repeats per design: **{report['repeats']}**",
        f"- transport: {report['transport']}",
        f"- total wall clock: {report['wall_clock_seconds']:.1f}s",
        "",
        "## Metric definitions",
        "",
        "- Per-class precision/recall/F1 is one-vs-rest over the four statuses.",
        "- `macro` is the unweighted mean of the per-class figures, over classes with",
        "  non-zero support in the labels.",
        "- `micro` is single-label multi-class, so micro precision = recall = F1 =",
        "  accuracy by construction. The three identical numbers are not a bug.",
        "- `open_gap_binary` treats `fail` and `partial` as positive and `pass` and",
        "  `not_applicable` as negative — 'was the gap found at all'.",
        "- `clear only` repeats every figure with the labels marked",
        "  `\"confidence\": \"borderline\"` in the fixture removed.",
        "- A check a run returned no verdict for is recorded as `<missing>`, counted,",
        "  and never averaged in as though it were a verdict.",
        "",
    ]

    for design in report["designs"]:
        out += [
            f"## Design: `{design['id']}` — {design['title']}",
            "",
            f"- checks labelled: {design['labelled']} of {design['rubric_checks']} in "
            f"the rubric",
            f"- borderline labels: {design['borderline_labels']}",
            f"- label distribution: `{design['label_distribution']}`",
            f"- runs completed: {design['runs_completed']} of {report['repeats']}",
            "",
        ]

        if design.get("error"):
            out += ["```", design["error"], "```", ""]
        if not design.get("runs"):
            continue

        out += ["### Per-run headline", "",
                _table(
                    [
                        [
                            row["run"],
                            f"{row['accuracy']:.3f}",
                            f"{row['macro_f1']:.3f}",
                            f"{row['macro_precision']:.3f}",
                            f"{row['macro_recall']:.3f}",
                            _open_gap(row["open_gap"], "f1"),
                            f"{row['overall_score']}",
                            str(row["missing"]),
                            f"{row['seconds']:.0f}s",
                        ]
                        for row in design["per_run"]
                    ],
                    ["run", "accuracy", "macro F1", "macro P", "macro R",
                     "open-gap F1", "review score", "missing", "elapsed"],
                ),
                ""]

        for cut in design["cuts"]:
            out += [
                f"### {cut['name']}",
                "",
                f"n={cut['metrics']['n']}, correct={cut['metrics']['correct']}, "
                f"accuracy={cut['metrics']['accuracy']:.4f}, "
                f"missing={cut['metrics']['missing_verdicts']}",
                "",
                _table(
                    _prf_rows(cut["metrics"]["per_class"]),
                    ["status", "precision", "recall", "F1", "support (truth)",
                     "predicted", "TP", "FP", "FN"],
                ),
                "",
                "```",
                f"macro      P={cut['metrics']['macro']['precision']:.4f}  "
                f"R={cut['metrics']['macro']['recall']:.4f}  "
                f"F1={cut['metrics']['macro']['f1']:.4f}",
                f"micro      P={cut['metrics']['micro']['precision']:.4f}  "
                f"R={cut['metrics']['micro']['recall']:.4f}  "
                f"F1={cut['metrics']['micro']['f1']:.4f}",
                f"open-gap   P={_open_gap(cut['metrics']['open_gap_binary'], 'precision')}  "
                f"R={_open_gap(cut['metrics']['open_gap_binary'], 'recall')}  "
                f"F1={_open_gap(cut['metrics']['open_gap_binary'], 'f1')}"
                f"   (TP={cut['metrics']['open_gap_binary']['true_positive']} "
                f"FP={cut['metrics']['open_gap_binary']['false_positive']} "
                f"FN={cut['metrics']['open_gap_binary']['false_negative']})",
                "```",
                "",
                "Confusion (rows = ground truth, columns = pipeline verdict):",
                "",
                _table(
                    [
                        [truth] + [str(row[p]) for p in (*STATUSES, MISSING)]
                        for truth, row in cut["metrics"]["confusion"].items()
                    ],
                    ["truth \\ predicted", *STATUSES, MISSING],
                ),
                "",
            ]

        out += ["### By pillar", "",
                _table(
                    [
                        [
                            row["framework"],
                            row["pillar"],
                            str(row["n"]),
                            f"{row['accuracy']:.3f}",
                            f"{row['macro_precision']:.3f}",
                            f"{row['macro_recall']:.3f}",
                            f"{row['macro_f1']:.3f}",
                            _open_gap(row["open_gap"], "f1"),
                            row["label_mix"],
                        ]
                        for row in design["by_pillar"]
                    ],
                    ["framework", "pillar", "checks", "accuracy", "macro P",
                     "macro R", "macro F1", "open-gap F1", "truth mix"],
                ),
                "",
                "Pillar figures are over the pillar's checks only, computed on the "
                "majority verdict. Several pillars hold 1-4 checks, so a single "
                "disagreement moves them a long way; the check count is in the table "
                "for that reason.",
                ""]

        variance = design["variance"]
        out += [
            "### Variance across runs",
            "",
            f"- runs compared: {variance['runs']}",
            f"- checks identical across every run: "
            f"{variance['identical_across_all_runs']} of {variance['checks_compared']} "
            f"(agreement rate {variance['verdict_agreement_rate']:.4f})",
            f"- every verdict identical in every run: "
            f"**{'yes' if variance['fully_identical'] else 'no'}**",
            f"- overall review score per run: {variance['overall_score_per_run']}",
            f"- overall score spread: {variance['overall_score_spread']} "
            f"(stdev {variance['overall_score_stdev']})",
            "",
        ]
        if variance["unstable"]:
            out += [
                "Checks whose verdict changed between runs:",
                "",
                _table(
                    [
                        [check_id, report["pillar_of"].get(check_id, "?"),
                         " -> ".join(seen),
                         design["labels"][check_id]["status"]]
                        for check_id, seen in sorted(variance["unstable"].items())
                    ],
                    ["check_id", "pillar", "verdict per run", "ground truth"],
                ),
                "",
            ]
        else:
            out += ["No check changed verdict between runs.", ""]

        moved = {
            key: spread for key, spread in variance["pillar_score_spread"].items()
            if spread["spread"]
        }
        out += [
            f"Pillar scores that moved between runs: {len(moved)} of "
            f"{len(variance['pillar_score_spread'])}",
            "",
        ]
        if moved:
            out += [
                _table(
                    [
                        [key, str(v["min"]), str(v["max"]), str(v["spread"])]
                        for key, v in moved.items()
                    ],
                    ["pillar", "min", "max", "spread"],
                ),
                "",
            ]

        out += ["### Per-check diff (majority verdict)", "",
                _table(
                    [
                        [
                            row["check_id"],
                            row["pillar"],
                            row["truth"],
                            row["predicted"],
                            "yes" if row["agrees"] else "**no**",
                            row["confidence"],
                        ]
                        for row in design["per_check"]
                    ],
                    ["check_id", "pillar", "ground truth", "majority verdict",
                     "agrees", "label confidence"],
                ),
                ""]

    if report.get("route_log"):
        out += ["## Provider route log (tail)", "",
                _table(
                    [
                        [c["label"], c["provider"], c["finish_reason"],
                         str(c["output_tokens"]), f"{c['seconds']:.1f}",
                         "yes" if c["allowed"] else "NO"]
                        for c in report["route_log"]
                    ],
                    ["call", "served by", "finish", "out tokens", "secs",
                     "in allow-list"],
                ),
                ""]

    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _verdicts_of(result: dict[str, Any]) -> dict[str, str]:
    return {f["check_id"]: f["status"] for f in result.get("findings", [])}


def _pillar_scores_of(result: dict[str, Any]) -> dict[str, float]:
    return {
        f"{fw['framework']}.{p['pillar_id']}": p["score"]
        for fw in result.get("frameworks", [])
        for p in fw.get("pillars", [])
    }


def _label_distribution(labels: dict[str, dict[str, str]]) -> dict[str, int]:
    out = {status: 0 for status in STATUSES}
    for label in labels.values():
        out[label["status"]] += 1
    return out


def analyse(design: dict[str, Any], runs: list[dict[str, Any]], pillar_of: dict[str, str],
            rubric_check_count: int) -> dict[str, Any]:
    """Everything computed from one design's completed runs."""
    labels = design["labels"]
    check_ids = sorted(labels)
    majority = majority_verdicts(runs, check_ids)

    cuts = [
        {
            "name": "All labelled checks — majority verdict across runs",
            "metrics": metrics(pairs_for(labels, majority)),
        },
        {
            "name": "Clear labels only (borderline excluded) — majority verdict",
            "metrics": metrics(pairs_for(labels, majority, only_clear=True)),
        },
    ]
    for index, run in enumerate(runs, 1):
        cuts.append(
            {
                "name": f"Run {index} alone — all labelled checks",
                "metrics": metrics(pairs_for(labels, run["verdicts"])),
            }
        )

    per_run = []
    for index, run in enumerate(runs, 1):
        m = metrics(pairs_for(labels, run["verdicts"]))
        per_run.append(
            {
                "run": str(index),
                "accuracy": m["accuracy"],
                "macro_f1": m["macro"]["f1"],
                "macro_precision": m["macro"]["precision"],
                "macro_recall": m["macro"]["recall"],
                "open_gap": m["open_gap_binary"],
                "overall_score": run["overall_score"],
                "missing": m["missing_verdicts"],
                "seconds": run["seconds"],
            }
        )

    by_pillar = []
    for pillar in sorted({pillar_of[c] for c in check_ids}):
        pairs = pairs_for(labels, majority, pillar_of=pillar_of, pillar=pillar)
        m = metrics(pairs)
        mix = {s: sum(1 for t, _ in pairs if t == s) for s in STATUSES}
        by_pillar.append(
            {
                "framework": pillar.split(".", 1)[0],
                "pillar": pillar.split(".", 1)[1],
                "n": m["n"],
                "accuracy": m["accuracy"],
                "macro_precision": m["macro"]["precision"],
                "macro_recall": m["macro"]["recall"],
                "macro_f1": m["macro"]["f1"],
                "open_gap": m["open_gap_binary"],
                "label_mix": ", ".join(f"{s}={n}" for s, n in mix.items() if n),
            }
        )

    per_check = [
        {
            "check_id": check_id,
            "pillar": pillar_of[check_id],
            "truth": labels[check_id]["status"],
            "predicted": majority[check_id],
            "agrees": labels[check_id]["status"] == majority[check_id],
            "confidence": labels[check_id]["confidence"],
        }
        for check_id in check_ids
    ]

    return {
        "id": design["id"],
        "title": design["title"],
        "labels": labels,
        "labelled": len(labels),
        "rubric_checks": rubric_check_count,
        "borderline_labels": sum(
            1 for label in labels.values() if label["confidence"] == "borderline"
        ),
        "label_distribution": _label_distribution(labels),
        "runs_completed": len(runs),
        "runs": [
            {
                "run": index,
                "review_id": run["review_id"],
                "overall_score": run["overall_score"],
                "seconds": round(run["seconds"], 1),
                "verdicts": run["verdicts"],
                "pillar_scores": run["pillar_scores"],
                "token_usage": run["token_usage"],
            }
            for index, run in enumerate(runs, 1)
        ],
        "per_run": per_run,
        "cuts": cuts,
        "by_pillar": by_pillar,
        "per_check": per_check,
        "majority_verdicts": majority,
        "variance": variance_across_runs(labels, runs),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repeats", type=int, default=3,
                        help="runs per design (default 3), for the variance figures")
    parser.add_argument("--designs", nargs="*", default=[],
                        help="design ids to run; default all in the fixture directory")
    parser.add_argument("--ground-truth", default=str(GROUND_TRUTH_DIR),
                        help="directory of labelled design JSON files")
    parser.add_argument("--out", default="",
                        help="output path stem; writes .md and .json (default: "
                             "accuracy-report-<timestamp> in the ground-truth dir)")
    parser.add_argument("--base-url", default="",
                        help="drive a deployed instance over HTTP instead of the "
                             "in-process app")
    parser.add_argument("--demo-token", default=os.environ.get("DEMO_ACCESS_TOKEN", ""),
                        help="X-Demo-Token value; needed with --base-url")
    parser.add_argument("--poll-seconds", type=float, default=5.0,
                        help="status poll interval when using --base-url")
    parser.add_argument("--check-labels", action="store_true",
                        help="validate the ground-truth set against the rubric and "
                             "exit; makes no API call and needs no credential")
    args = parser.parse_args(argv)

    # Isolate this run's data so a harness run cannot disturb real local-data.
    data_dir = tempfile.mkdtemp(prefix="t7-accuracy-")
    os.environ.setdefault("LOCAL_DATA_DIR", data_dir)
    if not args.base_url and not os.environ.get("DEMO_ACCESS_TOKEN"):
        # The gate fails closed, so the in-process app needs a token to answer at
        # all. Set before `config` is imported, and local to this process.
        os.environ["DEMO_ACCESS_TOKEN"] = "accuracy-harness-local-token"

    import config
    import rubric

    pillar_of = {
        c.check_id: f"{c.framework}.{c.pillar_id}" for c in rubric.all_checks()
    }

    try:
        designs = load_ground_truth(pathlib.Path(args.ground_truth), args.designs)
    except LabelError as exc:
        print(f"GROUND TRUTH INVALID\n  {exc}")
        return 1

    print(f"ground truth:    {args.ground_truth}")
    print(f"designs:         {', '.join(d['id'] for d in designs)}")
    rubric_check_count = len(rubric.all_checks())
    for design in designs:
        labelled = len(design["labels"])
        gap = rubric_check_count - labelled
        print(
            f"  {design['id']:<22} {labelled}/{rubric_check_count} checks labelled"
            + (f"  ({gap} UNLABELLED — excluded from every figure)" if gap else "")
        )
        for status, count in _label_distribution(design["labels"]).items():
            print(f"      {status:<16} {count}")

    if args.check_labels:
        print("\n--check-labels: the ground-truth set is valid against the rubric. "
              "No API call made.")
        return 0

    try:
        key = config.llm_api_key()
    except RuntimeError as exc:
        variable = (
            "OPENROUTER_API_KEY" if config.LLM_PROVIDER == "openrouter"
            else "ANTHROPIC_API_KEY"
        )
        print(f"\nBLOCKED — no credential available.\n  {type(exc).__name__}: {exc}")
        print(
            f"\nThis harness makes REAL calls, so it cannot run without one. Supply "
            f"it without pasting it in chat:\n"
            f"  printf '{variable}=%s\\n' \"$KEY\" > backend/.env\n"
            f"  export {variable}=...\n"
            f"Then re-run. `--check-labels` validates the fixture set meanwhile."
        )
        return 2
    print(f"\ncredential:      loaded ({len(key)} chars, value never printed)")

    import llm

    transport = (
        f"HTTP to {args.base_url}" if args.base_url
        else "in-process FastAPI TestClient against the real app"
    )
    print(f"provider/model:  {config.LLM_PROVIDER} / {config.MODEL}")
    print(f"transport:       {transport}")
    print(f"repeats:         {args.repeats}")
    print(f"data dir:        {os.environ['LOCAL_DATA_DIR']}\n")

    started = time.monotonic()
    analysed: list[dict[str, Any]] = []

    with Runner(args.base_url, args.demo_token, args.poll_seconds) as runner:
        for design in designs:
            runs: list[dict[str, Any]] = []
            error = ""
            for attempt in range(1, args.repeats + 1):
                label = f"{design['id']} run {attempt}/{args.repeats}"
                print(f"  {label} ... ", end="", flush=True)
                run_started = time.monotonic()
                try:
                    result = runner.review(design)
                except BaseException as exc:  # noqa: BLE001 — reported, not swallowed
                    elapsed = time.monotonic() - run_started
                    print(f"FAILED after {elapsed:.0f}s: {type(exc).__name__}: {exc}")
                    error += (
                        f"run {attempt}: {type(exc).__name__}: {exc}\n"
                        + "".join(
                            traceback.format_exception(type(exc), exc, exc.__traceback__)
                        )
                    )
                    continue
                elapsed = time.monotonic() - run_started
                verdicts = _verdicts_of(result)
                runs.append(
                    {
                        "review_id": result["review_id"],
                        "overall_score": result["overall_score"],
                        "seconds": elapsed,
                        "verdicts": verdicts,
                        "pillar_scores": _pillar_scores_of(result),
                        "token_usage": result.get("token_usage", {}),
                    }
                )
                agreed = sum(
                    1 for check_id, lab in design["labels"].items()
                    if verdicts.get(check_id) == lab["status"]
                )
                print(
                    f"done in {elapsed:.0f}s — score {result['overall_score']}, "
                    f"{agreed}/{len(design['labels'])} verdicts match the labels"
                )

            if runs:
                entry = analyse(design, runs, pillar_of, rubric_check_count)
            else:
                entry = {
                    "id": design["id"],
                    "title": design["title"],
                    "labels": design["labels"],
                    "labelled": len(design["labels"]),
                    "rubric_checks": rubric_check_count,
                    "borderline_labels": sum(
                        1 for lab in design["labels"].values()
                        if lab["confidence"] == "borderline"
                    ),
                    "label_distribution": _label_distribution(design["labels"]),
                    "runs_completed": 0,
                    "runs": [],
                }
            if error:
                entry["error"] = error
            analysed.append(entry)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": config.LLM_PROVIDER,
        "model": config.MODEL,
        "evaluate_temperature": llm.GREEDY_TEMPERATURE,
        "provider_order": config.OPENROUTER_PROVIDER_ORDER,
        "allow_fallbacks": config.OPENROUTER_ALLOW_FALLBACKS,
        "repeats": args.repeats,
        "transport": transport,
        "wall_clock_seconds": time.monotonic() - started,
        "pillar_of": pillar_of,
        "designs": analysed,
        "route_log": [
            {
                "label": call.label,
                "provider": call.provider,
                "model": call.model,
                "finish_reason": call.finish_reason,
                "output_tokens": call.output_tokens,
                "seconds": call.seconds,
                "allowed": call.allowed,
            }
            for call in llm.route_log()
        ],
    }

    stem = args.out or str(
        pathlib.Path(args.ground_truth)
        / f"accuracy-report-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    )
    markdown_path = pathlib.Path(f"{stem}.md")
    json_path = pathlib.Path(f"{stem}.json")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report))
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False))

    print(f"\nwrote {markdown_path}")
    print(f"wrote {json_path}\n")
    print(render_markdown(report))

    completed = sum(d["runs_completed"] for d in analysed)
    expected = args.repeats * len(designs)
    if completed != expected:
        print(f"INCOMPLETE: {completed} of {expected} runs finished; see the errors above.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
