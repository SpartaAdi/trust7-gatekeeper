"""The one-way AI-applicability gate.

## What it does

Marks an AI-conditional rubric check `not_applicable` when the deterministic
`AiDetection` record says the design has no AI/ML component in it. Nothing else.

## Why it exists

There was no gate at all. Whether the 18 AI-conditional TRUST-7 checks applied was
an unconstrained per-check judgement by the evaluate model: the AI-detection record
was computed before evaluate and never passed to it, and the evaluate prompt carried
no AI-specific instruction — only a generic "use `not_applicable` sparingly", which
pushes the wrong way.

It cost 46 points on a real run. Design B — a payments API whose document states it
"does not utilize any foundation models, neural networks, or generative capabilities"
— had all 18 checks returned as `pass` in one of three otherwise-identical runs.
Scored through `scoring.score`, that is **89.3 instead of 42.9** overall and
**92.9 instead of 0.0** on TRUST-7. The design was reported as satisfying eighteen
AI-governance checks it has no AI to govern, and the error ran in the flattering
direction on the number a reviewer reads first.

Note this was not sampling drift in the ordinary sense: evaluate already runs at
`temperature=0`. Its INPUT varied, because `_render_classification` feeds it the
classify stage's output and classify sampled freely. Classify is greedy now too, but
that narrows the variance rather than removing it, which is why the constraint
belongs in code.

## ONE-WAY — the whole design of this module

The gate has exactly one power: it can turn a check INTO `not_applicable`. It can
never do the reverse, and it can never touch a verdict the model reached on evidence.

Four guards, each independently sufficient to leave a finding alone:

1. **The check must be declared `ai_conditional` in the rubric.** An explicit,
   reviewable list — not a keyword match. `ss_data_residency` is not on it.
2. **The detection verdict must be `absent` or `denied`.** `present`, `likely`,
   `contradicted` and `not_run` all leave every finding exactly as evaluated.
   `likely` is deliberately excluded: a suggestive phrase is not grounds for
   silencing eighteen checks.
3. **The finding must not already be `not_applicable`.** Then there is nothing to
   do, and no override is recorded.
4. **The finding must not be `pass`, `fail` or `partial` *with evidence*.** If the
   model reached a verdict and cited something for it, that is a judgement on the
   design and the gate defers to it. Only an unevidenced verdict — the shape of a
   guess on a check that structurally cannot apply — is overridden.

Guard 4 is what makes this narrower than "override on absent/denied", and it is the
reason the gate cannot cause the mirror-image failure. A design that genuinely has AI
cannot be silenced by it: detection would have to say `absent`, which on a design
naming Bedrock or an LLM it does not.

## What guard 4 costs, stated rather than hidden

Guard 4 also bounds what the gate can fix, and the bound is worth being explicit
about: if evaluate returns `pass` on an AI-conditional check *and writes evidence for
it*, the gate defers, even on a `denied` verdict. Fabricated evidence and real
evidence are the same string to this module.

Whether the 46-point run would have been caught therefore depends on whether those
eighteen `pass` findings carried evidence text, and that is not recoverable: the
accuracy harness retains statuses only, not evidence, so the run cannot be
re-examined after the fact. The gate is not claimed to have prevented it.

What is done about the residual: a `pass`/`partial` on an AI-conditional check while
detection says `absent`/`denied` is a genuine contradiction — the design says it has
no AI and the model says the AI controls are satisfied. The gate defers to it, per the
one-way rule, but logs it at WARNING so a recurrence is diagnosable rather than
invisible. Deciding what a *reviewer* should see in that case is a separate question
from what this gate may overwrite, and is not settled here.

## Its relationship to scoring

`scoring.py` still never reads `AiDetection`, and a test asserts that at the source
level. What changed is narrower and worth stating exactly: scoring reads
`not_applicable`, and `not_applicable` is now sometimes SET here — after evaluate,
before scoring. The arithmetic is unchanged and still reproducible from the rubric
and the statuses; what produces one of those statuses has gained a deterministic
input. That is a real crossing of the old rule, not a technicality, and the old rule
was too broad rather than wrong.
"""

from __future__ import annotations

import logging

import rubric
from schema import AiDetection, Finding

log = logging.getLogger(__name__)

#: Verdicts that license the gate. Only these two.
#:
#: `absent` means the patterns ran and matched nothing. `denied` means the design
#: also says outright that it has no AI — weaker evidence on its own, since a claim
#: inside submitted material is not proof of itself, but it never appears without
#: `absent`-like silence on the positive side (a denial plus real evidence reads
#: `contradicted`, which is excluded).
#:
#: `likely` is NOT here on purpose. It fires on a suggestive phrase — a
#: "personalisation service" that might be a rules engine — and that is nowhere near
#: enough to mark eighteen governance checks inapplicable.
GATING_VERDICTS = frozenset({"absent", "denied"})

#: Statuses the gate will overwrite. Deliberately not `pass`/`fail`/`partial`: those
#: are verdicts, and a verdict backed by evidence is deferred to (see guard 4).
_EVIDENCE_BEARING = frozenset({"pass", "fail", "partial"})


def applicable_check_ids() -> frozenset[str]:
    """The AI-conditional checks, from the rubric's own per-check declaration."""
    return frozenset(c.check_id for c in rubric.all_checks() if c.ai_conditional)


def apply(findings: list[Finding], detection: AiDetection) -> list[str]:
    """Gate the AI-conditional checks in place. Returns the check_ids overridden.

    Mutates `findings` because it runs between evaluate and scoring on the same list
    the pipeline already holds; returning the ids rather than a new list keeps the
    caller's other uses of that list valid and gives it something to log.

    A no-op — returning `[]` and touching nothing — whenever the verdict is not
    `absent`/`denied`. That is the common case on an AI-bearing design and it must
    cost nothing.
    """
    if detection.verdict not in GATING_VERDICTS:
        return []

    conditional = applicable_check_ids()
    overridden: list[str] = []
    deferred: list[str] = []

    for finding in findings:
        if finding.check_id not in conditional:
            continue
        if finding.status == "not_applicable":
            # The model already got there. Nothing to override, and recording it as
            # an override would overstate what the gate did.
            continue
        if finding.status in _EVIDENCE_BEARING and finding.evidence.strip():
            # A verdict with a citation behind it. Whether or not we agree, the model
            # looked at the design and said something about it — deferring is the
            # difference between a gate and an override.
            #
            # Logged, though: "this design has no AI" and "its AI controls are
            # satisfied" cannot both be true, and the deferral is the one place that
            # contradiction is observable. Without this line it leaves no trace at
            # all, which is exactly how the 46-point run came to be undiagnosable.
            #
            # `fail` is not logged: "no model governance is documented" is a coherent
            # thing to say about a design with no model, it costs points rather than
            # awarding them, and logging it would bury the flattering case that
            # matters in noise from the harmless one.
            if finding.status != "fail":
                deferred.append(finding.check_id)
            continue

        finding.status = "not_applicable"
        finding.evidence = (
            f"Marked not applicable: no AI/ML component was detected in this design, "
            f"and this check only applies to designs that have one. "
            f"{detection.rationale}"
        )
        overridden.append(finding.check_id)

    if overridden:
        log.info(
            "ai gate: marked %d of %d AI-conditional checks not_applicable "
            "(detection verdict=%s): %s",
            len(overridden), len(conditional), detection.verdict,
            ", ".join(overridden),
        )
    if deferred:
        log.warning(
            "ai gate: detection says verdict=%s, but evaluate returned an evidenced "
            "pass/partial on %d of %d AI-conditional checks. Left untouched (the gate "
            "is one-way and does not overwrite evidence), so these scored as "
            "satisfied AI controls on a design with no AI detected: %s",
            detection.verdict, len(deferred), len(conditional), ", ".join(deferred),
        )
    return overridden
