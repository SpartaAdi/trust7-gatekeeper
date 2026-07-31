# Ground-truth set for the accuracy harness

Labelled designs read by `backend/scripts/accuracy_harness.py`. Each design is one
JSON file here plus the document and diagram it points at.

```
python scripts/accuracy_harness.py --check-labels   # validate, no API call
python scripts/accuracy_harness.py --repeats 3      # the real thing, real cost
```

## What is here

**Real, hand-labelled ground truth.** Two designs, labelled by the tester, 45 checks
each:

| design | id | shape | why it is in the set |
|---|---|---|---|
| `DESIGN A_AI_Bearing.md` | `design_a_techassist_rag_portal` | AI-bearing — an internal RAG portal | Every TRUST-7 check applies (0 `not_applicable`), so precision and recall have something to separate. |
| `DESIGN B_Traditional_No AI.md` | `design_b_checkout_payments_api` | traditional, no AI anywhere | 19 checks `not_applicable`, and **all seven** TRUST-7 pillars WHOLLY inapplicable. The N/A-denominator case, end to end. |

The pair matters more than either file. A set of only weak designs lets an
always-`fail` evaluator score well; a set with no N/A-heavy design cannot tell a
correct `not_applicable` from a lucky one.
`backend/tests/test_accuracy_harness.py` asserts both properties hold, so the set
cannot lose them silently.

Neither design ships a diagram — the SoW is the whole input, so `diagram` is `""`
on both and the vision path is not exercised by a harness run.

### The labels were reshaped, never edited

They arrived as a bare JSON array of 45 label objects, which `load_ground_truth`
cannot read: it expects the wrapper this README documents, with `labels` keyed by
`check_id`. The array was wrapped and three keys renamed to the names the loader
and the template use:

| as labelled | as stored |
|---|---|
| `verdict` | `status` |
| `evidence` | `why` |
| `labeler_name` | `labeler` |

**Every value is verbatim.** No verdict, evidence string, labeler or date was
altered, and nothing was added inside a label — `confidence` is absent rather than
invented, so `load_ground_truth` applies its `"clear"` default. The wrapper's own
fields (`id`, `title`, `provenance`, `document`, `design_is_ai_bearing`) are the
only new content, and `context` is left empty rather than guessed, since it would
otherwise ride into the prompt as if the tester had written it.

## `synthetic_stub/` — the old stand-ins

`claims-triage-ai` and `expense-portal` were synthetic designs with labels authored
inside this repository, written before any real labelled set existed. They now live
in `synthetic_stub/`, which the harness does **not** glob: a run that scored
invented designs alongside the tester's would report one blended figure and look
like a single number.

They are still used as test fixtures — each ships a `.drawio`, which is what makes
them the right input for the harness's plumbing tests and for the structural-coverage
and AI-detection tests. Those pass the subdirectory explicitly.

## File format

```json
{
  "id": "design_b_checkout_payments_api",
  "title": "Global checkout and payments API (traditional, no AI)",
  "provenance": "where this design and these labels came from",
  "document": "DESIGN B_Traditional_No AI.md",
  "diagram": "",
  "context": "",
  "labels": {
    "sec_least_privilege": {
      "status": "fail",
      "confidence": "clear",
      "why": "the phrase in the design that decides it"
    }
  }
}
```

| field | required | meaning |
|---|---|---|
| `id` | no | design id used by `--designs`; defaults to the filename stem |
| `title` | no | submitted as the review title |
| `document` | no | path to the SoW, relative to this JSON file |
| `diagram` | no | path to the diagram, relative to this JSON file |
| `context` | no | the free-text "purpose and use case" a submitter can type |
| `labels` | **yes** | `check_id` -> label, for check ids in `rubric/rubric.json` |

At least one of `document` and `diagram` must be present, because
`POST /reviews` rejects a submission with neither.

`document` and `diagram` are separate files rather than inlined text so a real SoW
can be dropped in unchanged — a PDF, a `.docx`, a PNG diagram — and go through the
same `POST /uploads` route the browser uses. That is deliberate: a PNG diagram
exercises the vision path, a `.drawio` is parsed deterministically and makes no
model call at all, and inlining the text would bypass `ingestion/documents.py`
entirely. Both shipped designs use `.drawio` so their diagram contributes no
sampling variance of its own; swap in a PNG to measure the vision path too.

### Labels

`status` must be one of `pass`, `partial`, `fail`, `not_applicable` — the same four
the evaluate stage returns.

`confidence` is about the LABEL, not the design:

- `clear` (the default) — the design states something that decides the verdict.
- `borderline` — a competent reviewer could defensibly land elsewhere. The harness
  reports every figure twice, once with these included and once without, because a
  disagreement on a borderline label is at least as likely to be a labelling
  problem as a pipeline problem.

Mark borderline labels honestly. Marking everything `clear` does not raise the
score, it just removes the harness's ability to tell a wrong verdict from an
arguable one.

`why` is free text and unused by the code. Write it anyway — it is what lets the
next person audit a label instead of re-deriving it, and a label nobody can
justify is not ground truth.

A bare string is accepted as shorthand: `"sec_least_privilege": "fail"` means
status `fail`, confidence `clear`, no rationale.

### Partial sets

A design does not have to label all 45 checks. The harness computes its figures
over the labelled subset and prints the number labelled and the number unlabelled
for each design, so a partial set is always visible and never silent.

## Writing labels

Two rules keep a set worth measuring against.

**Label the design, not the check's ideal.** `pass` means the design demonstrably
satisfies the check. Silence is `fail` or `partial` — the evaluate prompt says so,
so a label that reads silence as `pass` is measuring disagreement with the rubric
rather than pipeline error.

**`not_applicable` needs the design to make the check inapplicable, not merely
unmentioned.** "This system has no AI component" makes `tf_hallucination_control`
inapplicable. "This system does not discuss encryption" does not make
`sec_encryption_at_rest` inapplicable — it makes it `fail`. Several TRUST-7 checks
read as AI-specific but are not, and `ss_data_residency` is the one that catches
people: on this reading it applies to any design holding regulated data, whether or
not the design has AI in it.

Worth flagging rather than quietly reconciling: the real Design B labels
`ss_data_residency` `not_applicable`, which is what makes all seven of its TRUST-7
pillars wholly inapplicable. That is the tester's call and is left exactly as
labelled — but it disagrees with the guidance in the paragraph above, so one of the
two should change. The synthetic `expense-portal` stub takes the other reading and
labels it `fail`.

## Generated reports

`accuracy-report-*.md` and `accuracy-report-*.json` are written here by default.
They are run output, not fixtures — pass `--out` to put them elsewhere.
