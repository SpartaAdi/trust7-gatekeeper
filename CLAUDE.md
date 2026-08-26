# Trust7 Gatekeeper

## Project

Trust7 Gatekeeper is an AI governance agent that reviews solution designs (SoW /
solution documents + architecture diagrams) against two frameworks:

1. **AWS Well-Architected Framework** — 6 pillars: Operational Excellence,
   Security, Reliability, Performance Efficiency, Cost Optimization,
   Sustainability.
2. **Minfy TRUST-7** — 7 pillars: Trust foundations, Risk & resilience, Unit
   economics, Sovereignty & supply chain, Talent & adoption, Sustainability,
   AI governance.

The rubric must stay **GENERAL** to both frameworks' principles — never
hard-coded to any single example design or client.

### How the counts may be described

45 checks: **26 WAF, 19 TRUST-7**. Read from `rubric/rubric.json`, not asserted.

- The 26 map to AWS's own Well-Architected questions — the ones a design-time
  document review can answer. The rest of AWS's 57 need observed runtime
  behaviour (real latency, incident history, live cost data). Describe that as
  what the 26 *are*, never as the rule they were *selected by*.
- The 19 operationalize EGIRA's seven qualitative maturity pillars into
  auditable design-time questions. Minfy's published TRUST-7 model defines five
  maturity levels per pillar, **not** a discrete checklist — so there is no
  official TRUST-7 count to match or fall short of. Never write copy implying
  either.

## Constraints

Do not deviate from these without asking.

### Deployment

- **Frontend**: Vercel. **Backend**: Render (web service, free tier).
- No AWS. No serverless target, no Lambda adapter — a plain FastAPI app run
  under uvicorn.

### LLM

- **OpenRouter → `moonshotai/kimi-k2.6`** is the current, locked provider, pinned
  to `coreweave,decart,inceptron` with `allow_fallbacks: false`. Not AWS Bedrock.
- `OPENROUTER_API_KEY` is required, set as a dashboard environment variable and
  never committed. `ANTHROPIC_API_KEY` is **not used by the live pipeline** — the
  Anthropic-direct code is vestigial, not an active alternative; do not build
  against it.
- Cost control is a top priority: no provisioned throughput, pay-per-token only.

### Storage

- Local filesystem: JSON and uploaded files under `./local-data/`, persisted on
  Render's disk. No database, no object store, no cloud SDK.

### Architecture pattern

```
ingest -> normalize -> multi-stage agent pipeline -> structured JSON -> dashboard UI
```

Agent pipeline stages:

1. classify components
2. evaluate against rubric
3. prioritize findings
4. generate remediation

### Diagram input

Two input paths that must converge on **ONE common schema**:

- **draw.io XML** — parsed deterministically, no LLM call.
- **Image uploads** — parsed via the configured vision model (currently
  `moonshotai/kimi-k2.6` through OpenRouter, not Claude).

### Re-review loop

Must support re-analyzing a revised design and showing a **score delta** vs the
prior review.

### UI

- Clean and minimal. No generic AI-template look — no purple gradients, no
  default nested cards.
- Homepage shows a clear step tracker: **Step 1 Upload -> Step 2 Analyzing ->
  Step 3 Results**.
- Real per-stage progress during analysis, not a generic spinner.
- **No ETA, countdown, or percentage-complete promise.** Observed latency has
  spanned 14s to 44 minutes on the same provider, so any figure claiming to know
  when a run ends is wrong most of the time. The elapsed clock and a static
  typical range are the only duration figures shown.

### Results page

Four sections, in this order. The order is the point: what it means, how it
scored, what to do about it, then advice.

1. **Executive summary** — short prose, no bullets.
2. **Assessment · pillar maturity** — bulleted assessment, a "Fix these first"
   callout, then the WAF-6 and TRUST-7 heatmaps. Each pillar card explains its
   score from evidence already stored — no extra model call.
3. **Detailed findings** — the action list AND the record, in one section.
   Open findings grouped by severity, worst first, **expanded by default**; each
   carries its effort phase as a per-item tag. Passed and not-applicable checks
   stay behind their own collapsed toggle.
4. **For your stated use case** — rendered only when context was submitted and a
   recommendation could be grounded in a phrase it actually contains.

There was a fifth, an "Action roadmap" grouped by effort phase. It held the same
open findings as section 3 under a different heading, and a reader had to
reconcile two lists to answer one question. Effort survives as a per-finding tag
rather than a grouping axis. **Do not reintroduce a second list of open
findings** — that mistake has now been made twice, first as a flat top-ten
shortlist and then as the roadmap.

Grounding: where a remediation has `remediation_grounded_in`, the quote is shown
under it, monospace and quoted, labelled "Grounded in the source". That label is
deliberately narrow — the quote was found in the design source; nothing checked
whether the remediation is correct. Never word it as verified or accurate.
