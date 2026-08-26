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

- Local filesystem: JSON and uploaded files under `./local-data/`. No database,
  no object store, no cloud SDK.
- **It is EPHEMERAL, not persisted.** Render's own docs: "Free web services cannot
  attach a persistent disk", and "any changes to your web service's filesystem are
  lost every time the service redeploys, restarts, or spins down" — which a free
  instance does after 15 minutes without traffic. Reviews written before a
  spin-down are gone, and a mid-run restart 404s the review being polled. Do not
  build anything that assumes a review survives, and do not describe this storage
  as durable.

### Architecture pattern

```
ingest -> normalize -> multi-stage agent pipeline -> structured JSON -> dashboard UI
```

Agent pipeline stages, as named in `schema.STAGES`:

1. **screen** — the relevance gate. One small call, before the expensive stages,
   deciding whether this is a solution design at all.
2. **classify** components
3. **evaluate** against rubric — TWO calls, one per framework
4. **prioritize** findings
5. **remediate** — generates guidance AND the executive summary

Six model calls on a first-pass review, plus one vision call when a diagram is
read. Each of classify and remediate can add one bounded retry.

### Diagram input

Three input paths that must converge on **ONE common schema**:

- **draw.io XML** — parsed deterministically, no LLM call.
- **Image uploads** — parsed via the configured vision model (currently
  `moonshotai/kimi-k2.6` through OpenRouter, not Claude).
- **A diagram embedded inside a PDF** — only when a document is given and a
  diagram is NOT. `ingestion/embedded.py` picks at most one image by pixel area
  plus a diagram keyword on its own page, then sends it down the SAME
  `parse_diagram` path as an uploaded image, so it arrives as
  `DiagramSource.IMAGE` and every consumer needs no new code. An explicit diagram
  upload always wins; this never overrides or merges.

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
   Open findings grouped by severity, worst first; each carries its effort phase
   as a per-item tag. Passed and not-applicable checks stay behind their own
   collapsed toggle.
4. **For your stated use case** — rendered only when context was submitted and a
   recommendation could be grounded in a phrase it actually contains.

Open findings each start **collapsed** behind their severity heading, as does the
passing / not-applicable group. A full review is 45 checks and an expanded page
opens on a wall of text.

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

### Open Questions

A dismissible panel over the results page, not a route — `App.tsx`'s four phases
feed the step tracker and this is a task performed ON a finished review, not a
fifth step. It lists every open finding from BOTH frameworks grouped by pillar,
takes one optional typed or dictated answer each plus a general note, collates
them into an editable block, and submits it through the EXISTING re-review
endpoint as ordinary feedback. Resolved findings vanish by falling out of the
open filter — there is no answered-flag to keep in sync.

`MAX_FEEDBACK_CHARS` is **16000**, raised from 4000 for this: each collated entry
costs ~135 characters of scaffolding before the answer, and 43 answers overran
4000 by 10,396. The view refuses to submit over the cap rather than truncating.
