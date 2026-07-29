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
- **Image uploads** — parsed via Claude vision.

### Re-review loop

Must support re-analyzing a revised design and showing a **score delta** vs the
prior review.

### UI

- Clean and minimal. No generic AI-template look — no purple gradients, no
  default nested cards.
- Homepage shows a clear step tracker: **Step 1 Upload -> Step 2 Analyzing ->
  Step 3 Results**.
- Real per-stage progress during analysis, not a generic spinner.
