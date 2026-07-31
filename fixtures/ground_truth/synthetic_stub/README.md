# Synthetic stand-in designs — NOT ground truth

These two designs and their labels were **authored inside this repository**, before
any tester-supplied labelled set existed. They were never a measurement of anything;
they existed to make the harness runnable and to fix the file format.

Real hand-labelled ground truth now lives in the parent directory. These moved down
here so `accuracy_harness.py` cannot reach them: it globs `*.json` in whatever
directory it is pointed at, non-recursively, so a normal run scores only the real
designs. A run that mixed invented labels with the tester's would report one blended
precision figure and look like a single number.

**Do not add them back to the parent directory**, and do not treat any accuracy
number computed from them as an accuracy number.

## What they are still good for

Both ship a `.drawio` diagram, which the real designs do not — those are documents
only. That makes these the right fixtures for tests that need a diagram to exist:

| used by | for |
|---|---|
| `backend/tests/test_accuracy_harness.py` | the plumbing — that `--repeats 3` runs three independent reviews, that a report renders, that a failed run is reported rather than scored. Passes this directory explicitly. |
| `backend/tests/test_data_fidelity.py` | structural coverage on real draw.io files, which must read 100%. |
| `backend/tests/test_ai_detection.py` | a design supplying BOTH a diagram and a document, so the evidence record can be checked for citing both. |

Running the harness against them deliberately takes an explicit path:

```bash
python scripts/accuracy_harness.py \
  --ground-truth ../fixtures/ground_truth/synthetic_stub --check-labels
```

| file | shape |
|---|---|
| `expense-portal` | no AI component; 18 of 19 TRUST-7 checks `not_applicable` |
| `claims-triage-ai` | AI-bearing; every TRUST-7 check applies |
