# Trust7 Gatekeeper

An AI governance agent that reviews solution designs — SoW / solution documents
plus architecture diagrams — against two frameworks:

- **AWS Well-Architected Framework** — Operational Excellence, Security,
  Reliability, Performance Efficiency, Cost Optimization, Sustainability.
- **Minfy TRUST-7** — Trust foundations, Risk & resilience, Unit economics,
  Sovereignty & supply chain, Talent & adoption, Sustainability, AI governance.

The rubric stays general to both frameworks' principles; it is not tuned to any
single example design or client. Revised designs can be re-reviewed, and the
result shows a score delta against the prior review. Past reviews are listed on
the home page and reopen from stored data without re-analysis.

The executive summary at the top of a result is written by the remediate stage
rather than a fifth API call: that stage already holds the findings in context,
and it is handed the computed scores so it interprets them instead of recounting.

## Architecture

```
                 draw.io XML ──► deterministic parser (no LLM)
                                          │
   upload  ──►  local-data/uploads/       ├──►  ONE common component schema
                 image ──────► Claude vision parser
                                          │
                                          ▼
                                    normalize
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    │        multi-stage agent pipeline         │
                    │  1. classify components                   │
                    │  2. evaluate against rubric (rubric.json) │
                    │  3. prioritize findings                   │
                    │  4. generate remediation                  │
                    └─────────────────────┬─────────────────────┘
                                          │
                        structured JSON  ──►  local-data/reviews/
                                          │
                                          ▼
                                   dashboard UI
       History (home) ─► Step 1 Upload ─► Step 2 Analyzing ─► Step 3 Results

   Per-stage progress: each stage writes to local-data/status/<id>.json;
   the UI polls it, so the progress bar reflects real stage state.
```

The backend is a plain FastAPI app run under uvicorn — no serverless target and
no Lambda adapter. The review runs as a background task in-process, because a
full analysis takes minutes and no client will hold a request open that long.
Persistence is JSON files and uploaded blobs on the local filesystem. The LLM is
the Claude API accessed directly via the Anthropic API — not Bedrock —
pay-per-token with no provisioned throughput.

## Layout

```
backend/
  main.py         FastAPI app (uvicorn entrypoint)
  config.py       environment configuration
  storage.py      JSON + blob persistence under local-data/
  llm.py          the only module that calls the Anthropic SDK
  rubric.py       loads and flattens rubric.json
  scoring.py      pillar/framework scores and re-review deltas
  schema.py       Pydantic models — the common schema
  ingestion/      document, draw.io, and vision parsing; normalization
  agent/          the four pipeline stages, orchestration, injection guard
  tests/          41 tests
frontend/
  src/App.tsx     History (home) -> Upload -> Analyzing -> Results
  src/api.ts      the only module that calls the API
  src/types.ts    mirrors backend/schema.py — the wire shapes
  src/maturity.ts score -> Aware/Managed/Governed/Certified/Pioneering
  src/fileKind.ts dropped-file classification (SoW / diagram / ask)
  src/components/ StepTracker, DropZone, SeverityMark
  src/views/      HistoryView, UploadView, AnalyzingView, ResultsView
rubric/
  rubric.json     45 checks across 13 pillars
render.yaml       backend service definition
frontend/vercel.json  SPA routing
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/uploads` | Multipart file upload. Returns the key to reference it by. |
| `POST` | `/reviews` | Submits `document_key` / `diagram_key`. Returns `202` with a `review_id`. |
| `POST` | `/reviews/{id}/reanalyze` | Same, but scored against review `{id}` and returned with a score delta. |
| `GET` | `/reviews/{id}/status` | Per-stage progress, written by the pipeline as each stage runs. This is what the UI polls. |
| `GET` | `/reviews` | Past reviews, newest first, with pillar scores for the history heatmap. |
| `GET` | `/reviews/{id}` | The finished review as structured JSON. |
| `GET` | `/health` | Liveness probe, and Render's health check path. |

Two cost controls are built into the model calls. The rubric is byte-identical on
every review and sits behind a prompt-cache breakpoint, so repeat reviews read it
from cache rather than paying for it again. And effort is set per stage — the
evaluation stage decides the score and gets `high`, while classification and
remediation run at `medium`.

## Running locally

Backend:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env      # then set ANTHROPIC_API_KEY
uvicorn main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

`ANTHROPIC_API_KEY` is required — the pipeline fails at the classify stage
without it, and the error surfaces in the UI rather than silently.

## Deploying

**Order matters: Render first, then Vercel, then one setting back on Render.**
Each side needs the other's URL, so it is a two-step with a follow-up.

### 1. Backend on Render

Point Render at this repo; `render.yaml` defines the service (free tier, Python
3.13, health check on `/health`). Set two environment variables in the dashboard
— both are marked `sync: false` so they are never read from the repo:

| Variable | Value |
| --- | --- |
| `ANTHROPIC_API_KEY` | your key. Never committed; `.env` is gitignored. |
| `CORS_ALLOWED_ORIGIN` | the Vercel URL from step 2. Set a placeholder now, correct it in step 3. |

Note the service URL, e.g. `https://trust7-gatekeeper-api.onrender.com`.

### 2. Frontend on Vercel

Import the repo, set the root directory to `frontend`. `vercel.json` handles Vite
SPA routing so a deep link doesn't 404. Set one environment variable:

| Variable | Value |
| --- | --- |
| `VITE_API_BASE_URL` | the Render URL from step 1 |

This is read at **build** time, so changing it requires a redeploy, not just a
restart.

### 3. Close the CORS loop

Set `CORS_ALLOWED_ORIGIN` on Render to the exact Vercel origin (scheme + host, no
trailing slash) and let it redeploy. Until this is done the browser blocks every
request. There is no wildcard fallback — a wildcard would let any site call the
API on a user's behalf.

### Free tier caveats

**`local-data/` does not survive a restart on Render's free tier.** The disk is
ephemeral, and free services spin down after 15 minutes idle, so uploads,
reviews, and progress records are lost on the next wake. Consequences:

- A review submitted just before a spin-down may never finish; its status file
  disappears with it.
- Re-review deltas need the prior review to still exist, so they only work within
  a single uptime window.
- The first request after a spin-down takes ~30 seconds to cold start.

For history that outlives a restart, attach a Render **persistent disk** mounted
at `./local-data` (a paid plan) — no code change needed, `LOCAL_DATA_DIR` already
points there.

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest tests -q   # 41 tests
cd frontend && npm test                                                          # 23 tests
```

`requirements.txt` is runtime-only, so Render's build installs no test tooling;
`requirements-dev.txt` includes it and pulls the runtime set in.

Backend tests cover the deterministic parts — draw.io parsing, scoring, delta
computation, and prompt-injection defences — without any model call.
`not_applicable` checks are excluded from the score rather than counted as
failures, so an irrelevant check neither helps nor hurts.

Frontend tests are deliberately shallow: each view renders with mocked API
responses and must not crash, plus one interaction test covering the upload path.
The test setup throws on any unmocked `fetch`, so a test that reaches the network
fails rather than hanging.

## Prompt-injection posture

Everything reviewed is attacker-controlled — the SoW, draw.io node labels, and
text drawn inside a diagram image all reach the prompt. Uploaded content is
fenced in `<untrusted_uploaded_content>` tags that the system prompt names as
data, forged closing tags are neutralized, and every stage carries a guard
paragraph stating the fenced region has no authority however it self-identifies.

The structural defences matter more than the prompt text, because they hold even
if the model is fully compromised: an unrecognized `check_id` is dropped, a
missing one is recorded as unmet, and status and severity are enum-constrained.
So the worst realistic outcome — the model returning nothing for the checks a
design fails — scores zero rather than deleting those checks from the
denominator.
