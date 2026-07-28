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
  report.py       PDF export (ReportLab)
  maturity.py     score -> band; mirrors frontend/src/maturity.ts
  ingestion/      document, draw.io, and vision parsing; normalization
  agent/          the four pipeline stages, orchestration, injection guard
  tests/          147 tests
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
| `GET` | `/reviews/{id}/report.pdf` | The review as a formatted PDF, rendered on demand. |
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

For this deployment that is:

| Variable | Value |
| --- | --- |
| `CORS_ALLOWED_ORIGIN` | `https://trust7-gatekeeper.vercel.app` |

**Where the origin comes from, highest precedence first.** Nothing is hardcoded
at the middleware — `main.py` passes `config.CORS_ALLOWED_ORIGIN` straight
through, and `config.py` resolves it as:

1. the `CORS_ALLOWED_ORIGIN` **environment variable** — how Render supplies it,
   and what wins in production;
2. `CORS_ALLOWED_ORIGIN` in `backend/.env` or the repo-root `.env` —
   `load_dotenv` is called *without* `override=True`, so a real environment
   variable always beats a file;
3. the development default `http://localhost:5173`.

So if the dashboard variable and a local `.env` disagree, **the dashboard wins.**
The effective origin and its source are logged at startup, and an unset variable
logs a warning — otherwise a missing dashboard value shows up only as an opaque
CORS error in someone's browser with nothing on the server to explain it.

Two consequences of exact matching worth knowing: a trailing slash is stripped
before use (a browser's `Origin` header never has one), and **Vercel preview
deployments will be blocked**, because they get their own hostnames
(`…-git-<branch>.vercel.app`) that are not this origin. Preview testing needs
that preview origin set instead, or a second Render service.

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
cd backend && pip install -r requirements-dev.txt && python -m pytest tests -q   # 147 tests
cd frontend && npm test                                                          # 26 tests
```

`requirements.txt` is runtime-only, so Render's build installs no test tooling;
`requirements-dev.txt` includes it and pulls the runtime set in.

`tests/test_pipeline_e2e.py` is the widest of these and runs in the default
invocation above — no marker, no separate command, so it cannot be skipped by
accident. It drives the real routes, the real pipeline, real background
execution, and real filesystem persistence, stubbing only `llm.complete_json`.
It is the only coverage `api/routes.py`, `agent/pipeline.py`, and
`ingestion/normalize.py` have, so treat a failure there as a broken app rather
than a broken test.

Both suites above stub the model, so neither spends tokens or proves the live API
accepts our request shape. That last check is a separate, explicit script:

```bash
cd backend && python scripts/real_api_e2e.py
```

It runs all six stages against the live API on a synthetic fixture — an invented
expense portal, deliberately not any real engagement — and prints every request
as sent, plus per-stage progress and token usage. It confirms four things: the
calls complete, the model resolves to `claude-sonnet-5`, the
`structured-outputs-2025-11-13` beta is accepted, and the responses validate
against schemas with `additionalProperties: false` on every object node. It exits
`2` without spending anything if no key is configured, and it treats a
fallback retry as a failure rather than a pass, since a retry means the first
request was rejected. The key is read from `backend/.env` or the environment and
is never printed — only a length and a SHA-256 prefix.

Backend tests cover the deterministic parts — draw.io parsing, scoring, delta
computation, and prompt-injection defences — without any model call.
`not_applicable` checks are excluded from the score rather than counted as
failures, so an irrelevant check neither helps nor hurts.

Frontend tests are deliberately shallow: each view renders with mocked API
responses and must not crash, plus one interaction test covering the upload path.
The test setup throws on any unmocked `fetch`, so a test that reaches the network
fails rather than hanging.

## PDF export

The Results page has a **Download Report** button behind
`GET /reviews/{id}/report.pdf`. The document is a Minfy-branded cover (navy
`#0A2540`, one flat orange `#E85D26` band — no gradient), the executive summary,
a scorecard covering all 13 pillars, findings grouped by severity with their
remediation text, and an appendix holding the uploaded diagram.

Pillar strength in the scorecard is encoded the same way as the history heatmap:
depth of a single navy tone, never hue. `report.swatch_alpha` is the shared
formula, and a test asserts the ramp is strictly monotonic in luminance — so the
scorecard survives greyscale printing and reads correctly with any colour vision
deficiency. A dashed cell means *not evaluated*, which is visually distinct from
a scored zero rather than merely a lighter shade of it.

**ReportLab, not WeasyPrint.** WeasyPrint renders fine in this container, so this
isn't a claim that it's broken — the reasons are narrower. It declares eight
runtime dependencies against ReportLab's two, and reaches libpango/libgobject
through cffi at render time; those shared libraries are not guaranteed on
Render's native Python runtime, where `apt-get` isn't available during a
free-tier build. ReportLab's failure mode would be a pip error at build time,
WeasyPrint's an ImportError the first time a user clicks Download in production.
It also consumes HTML, and no templating engine is installed — so it would mean
adding jinja2 too.

One consequence worth stating: every string in the report is model-generated
from attacker-controlled uploads, and ReportLab's `Paragraph` parses a markup
dialect. All of it goes through `report._t()`, which XML-escapes. A test injects
`<font color="purple">` into a finding title and asserts both that the tag
renders literally and that no purple fill reaches the page.

The report is rendered per request rather than cached: generation is well under a
second, and storing a second artefact would add an invalidation problem for no
gain. If the uploaded diagram has been reclaimed from Render's ephemeral disk,
the appendix says so and the rest of the report is still produced.

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
