# Labeling template — fill this in by hand

`design-labeling-template.json` is a blank form with all 45 rubric checks
pre-populated and **every verdict left empty**. It is the input to
`backend/scripts/accuracy_harness.py`.

**This is meant to be filled in outside Claude Code, by a human who has read the
design.** Not generated, not autocompleted, not "drafted for review". The whole
point of a ground-truth set is that it is an *independent* answer key: if the
labels come from a model, comparing the pipeline's verdicts against them measures
how consistent two model runs are, not whether either is correct. The number that
comes out would look exactly like an accuracy figure and mean nothing.

The two designs currently in the parent directory (`expense-portal.json`,
`claims-triage-ai.json`) are **synthetic stand-ins with labels authored inside this
repository**, put there so the harness had something to run against. They are
placeholders. Files produced from this template are meant to replace them.

## Where to put the filled-in file

Copy it to the **parent** directory, once per design, under a descriptive name:

```
cp template/design-labeling-template.json ../claims-platform-v2.json
```

Files in this `template/` subdirectory are deliberately invisible to the harness —
`load_ground_truth` globs `*.json` one level up and does not recurse. That is not
incidental: every `status` in the template is `""`, which is not a valid verdict, so
if the template sat in the parent directory the harness would refuse to run at all.

The same mechanism is the half-finished-work guard. A file with any blank `status`
fails validation with the check id named, so an incomplete set fails loudly instead
of quietly scoring against blanks:

```bash
cd backend && python scripts/accuracy_harness.py --check-labels
```

Run that after every labeling session. It makes no API calls and needs no
credential.

## The fields

### About the design (top level)

| Field | Fill in with |
| --- | --- |
| `id` | Short slug, no spaces. What `--designs` takes. e.g. `claims-platform-v2` |
| `title` | Human name, submitted as the review title |
| `provenance` | Where the design came from, and who owns the labels. Say if it is real client material — see the note on that below |
| `labeler` | Your name. Whoever made these judgements |
| `labeled_date` | `YYYY-MM-DD` you finished |
| `document` | Filename of the SoW, **relative to the JSON file**, e.g. `claims-platform-v2.sow.pdf` |
| `diagram` | Filename of the diagram, same rule. Leave `""` if there is none |
| `context` | The optional free-text "purpose and use case" a submitter would type. `""` if none |
| `design_is_ai_bearing` | `yes` or `no`. Not read by the harness — it is a note to the next person about which half of the pair this is |

At least one of `document` and `diagram` must be filled, because `POST /reviews`
refuses a submission with neither.

Put the design files themselves in the **parent** directory alongside the JSON. Any
accepted upload type works — a real `.pdf` or `.docx` SoW, a `.drawio` file, or a
`.png` of a diagram — and each goes through the same upload route the browser uses.
A `.drawio` diagram is parsed deterministically and makes no model call; a `.png`
exercises the vision path, which adds its own variance. Prefer `.drawio` unless you
are deliberately measuring vision.

### About each check (45 entries under `labels`)

Four fields are **pre-filled facts** copied from `rubric/rubric.json`, so the form
can be judged without opening the codebase. They are context, not judgements — do
not edit them:

| Field | |
| --- | --- |
| `framework` | `WAF-6` or `TRUST-7` |
| `pillar` | Which pillar the check belongs to |
| `description` | The check itself — what you are judging against |
| `default_severity` | The check's starting severity. Context only; the harness does not read it |

Five fields are yours, and all start empty:

| Field | Fill in with |
| --- | --- |
| `status` | **Required.** One of `pass`, `partial`, `fail`, `not_applicable` |
| `confidence` | **Required.** `clear` or `borderline` — about your LABEL, not the design |
| `why` | The phrase from the design that decided it. Quote it |
| `labeler` | Only if a different person labelled this check. Otherwise leave blank |
| `date` | Only if labelled on a different day. Otherwise leave blank |

#### `status` — the verdict

| | |
| --- | --- |
| `pass` | The design demonstrably satisfies the check |
| `partial` | It partly addresses it, or states an intent without the mechanism |
| `fail` | It does not address it, or addresses it in a way that defeats its purpose |
| `not_applicable` | The check cannot apply to this design's shape |

Two rules decide most borderline calls:

**Silence is not a pass.** If the design does not establish something the check
requires, that is `fail` or `partial`. The evaluate prompt says so explicitly, so a
label that reads silence as `pass` measures disagreement with the rubric rather than
pipeline error.

**`not_applicable` needs the design to make the check inapplicable, not merely
unmentioned.** "This system has no AI component" makes `tf_hallucination_control`
inapplicable. "This system does not discuss encryption" does *not* make
`sec_encryption_at_rest` inapplicable — it makes it `fail`.

Five checks catch people out, because they read as AI-specific and are not, or the
reverse. `docs/rubric_checklist.md` lists them; the one to watch is
`ss_data_residency`, which is **not** AI-scoped and applies to any design holding
regulated data.

#### `confidence` — about your label, not the design

`clear` when the design states something that decides the verdict. `borderline`
when a competent reviewer could defensibly land somewhere else.

The harness reports every figure twice: once with borderline labels included, once
with them excluded. A disagreement on a borderline label is at least as likely to be
a labelling problem as a pipeline problem, and that split is the only way to tell.

Mark them honestly. Marking everything `clear` does not raise the score — it just
removes the harness's ability to distinguish a wrong verdict from an arguable one.

#### `why` — the deciding evidence

Quote the phrase from the design that decided the verdict. The harness does not read
this field; write it anyway. It is what lets the next person audit a label instead of
re-deriving it, and it is what you will need when the pipeline disagrees with you and
you have to work out which of you is right. A label nobody can justify is not ground
truth.

For a `not_applicable`, the quote should be what makes the check inapplicable — "no
model or AI component is used in this system" — not the absence of something.

## Picking the two designs

Label **two** designs, and pick them for contrast rather than convenience.

**One AI-bearing.** A design with a real model or ML component, so all 19 TRUST-7
checks genuinely apply and none can be answered `not_applicable`.

**One non-AI.** A design with no model anywhere, which makes 18 of the 19 TRUST-7
checks legitimately `not_applicable` and six of its seven pillars *wholly*
inapplicable. (`ss_data_residency` is the one that still applies.)

The pair matters more than either file:

- **Without the non-AI design**, you cannot tell a correct `not_applicable` from a
  lucky one — and an evaluator that never says `not_applicable` can post a
  respectable overall accuracy while being wrong about an entire framework. The
  per-class `not_applicable` row in the report is what exposes that, and it needs
  instances to measure.
- **Without the AI-bearing design**, 19 of the 45 checks are never exercised at all.

Two further things worth getting right:

**Make at least one design mixed rather than uniformly bad.** A set where the answer
is almost always `fail` lets a degenerate always-fail evaluator score well. Precision
and recall only separate when there are genuine passes to get right.

**Do not tune the design to the rubric.** Label a design that already exists. Writing
one to hit particular checks measures the pipeline against your expectations rather
than against reality.

## Real client material

Anything placed in `fixtures/ground_truth/` is committed to this repository. Do not
put real client SoWs, names, or unpublished commercial detail there. If the only
designs worth labelling are real ones, sanitise them first — replace client and
person names, drop pricing and contract terms — and say in `provenance` that the
file is a sanitised derivative and what was removed.

## When you are done

```bash
cd backend
python scripts/accuracy_harness.py --check-labels     # validates, no API calls
python scripts/accuracy_harness.py --repeats 3        # the real run, real cost
```

`--check-labels` prints the label distribution per design. Read it before spending
anything: if one design's distribution is 40 `fail` and nothing else, the run will
not tell you much.
