# Ground-truth set for the accuracy harness

Labelled designs read by `backend/scripts/accuracy_harness.py`. Each design is one
JSON file here plus the document and diagram it points at.

```
python scripts/accuracy_harness.py --check-labels   # validate, no API call
python scripts/accuracy_harness.py --repeats 3      # the real thing, real cost
```

## Status of the two designs shipped here

**They are synthetic and their labels were authored in this repository.** No
tester-supplied labelled set existed when the harness was written — the repository
and its full history contain none — so these exist to make the harness runnable
and to fix the file format. They are stand-ins. Replace them, or add alongside
them, with designs whose labels the reviewer actually owns; the harness reads
every `*.json` in this directory and needs no code change to pick up more.

`why_this_design` in each file records what it is for:

| design | shape | why it is in the set |
|---|---|---|
| `expense-portal` | weak, no AI component at all | 18 of 19 TRUST-7 checks are `not_applicable` and six of its seven pillars are WHOLLY inapplicable. The N/A-denominator case, end to end. |
| `claims-triage-ai` | mixed, AI-bearing | Every TRUST-7 check applies, and the design genuinely passes many of them, so precision and recall have something to separate. |

The pair matters more than either file. A set of only weak designs lets an
always-`fail` evaluator score well; a set with no N/A-heavy design cannot tell a
correct `not_applicable` from a lucky one.
`backend/tests/test_accuracy_harness.py` asserts both properties hold, so the set
cannot lose them silently.

## File format

```json
{
  "id": "expense-portal",
  "title": "Internal expense claim portal",
  "provenance": "where this design and these labels came from",
  "document": "expense-portal.sow.md",
  "diagram": "expense-portal.drawio",
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
people: it applies to any design holding regulated data, which is why
`expense-portal` labels it `fail` while labelling its 18 neighbours
`not_applicable`.

## Generated reports

`accuracy-report-*.md` and `accuracy-report-*.json` are written here by default.
They are run output, not fixtures — pass `--out` to put them elsewhere.
