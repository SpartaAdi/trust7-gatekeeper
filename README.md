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
Persistence is JSON files and uploaded blobs on the local filesystem. Model calls
go through OpenRouter to `moonshotai/kimi-k2.6` by default, pay-per-token with no
provisioned throughput; `LLM_PROVIDER=anthropic` switches back to the Claude API
direct (not Bedrock) without a code change. See **LLM provider** below.

## Layout

```
backend/
  main.py         FastAPI app (uvicorn entrypoint)
  config.py       environment configuration
  storage.py      JSON + blob persistence under local-data/
  llm.py          the only module that calls an LLM provider
  rubric.py       loads and flattens rubric.json
  scoring.py      pillar/framework scores and re-review deltas
  schema.py       Pydantic models — the common schema
  report.py       PDF export (ReportLab)
  maturity.py     score -> band; mirrors frontend/src/maturity.ts
  ingestion/      document, draw.io, and vision parsing; normalization
  agent/          the four pipeline stages, orchestration, injection guard
  tests/          224 tests
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
| `GET` | `/health` | Liveness probe, and Render's health check path. The only ungated route. |

## Demo access gate

Every route except `/health` requires a shared token in an `X-Demo-Token` header,
set as `DEMO_ACCESS_TOKEN`. This is a one-day gate for demo day, not an auth
system: one static token, no users, no sessions, no roles. It exists so the public
Render URL cannot be used by whoever finds it to read past reviews or spend our
API budget.

It **fails closed**. With `DEMO_ACCESS_TOKEN` unset every gated route returns 401,
which is loud and fixable in seconds; failing open would mean a forgotten
dashboard variable silently leaves the API wide open, which is the exact thing the
gate is for. `/health` stays reachable regardless, or Render would mark the
service unhealthy and restart it forever.

Two details that decide whether a gate works or quietly breaks the app. The
middleware is registered **before** CORS, so CORS ends up outermost and a 401 still
carries `Access-Control-Allow-Origin` — otherwise the browser reports an opaque
CORS failure and the user never learns the token was wrong. And comparison uses
`secrets.compare_digest`, so a wrong token does not leak its length or prefix
through timing.

The frontend prompts for the token on first load and holds it in `sessionStorage`,
so it dies with the tab rather than persisting on a shared machine. Any 401 drops
the stored token and returns to the prompt, so a stale token re-prompts instead of
looping.

## LLM provider

One switch, `LLM_PROVIDER`, chosen so a bad day is an environment change and a
restart rather than a revert commit:

| Value | Client | Model | Key |
| --- | --- | --- | --- |
| `openrouter` (default) | OpenAI-compatible, `https://openrouter.ai/api/v1` | `moonshotai/kimi-k2.6` | `OPENROUTER_API_KEY` |
| `anthropic` | Anthropic SDK | `claude-sonnet-5` | `ANTHROPIC_API_KEY` |

Callers speak one internal dialect and `llm.py` translates per provider, so
`agent/stages.py` and `ingestion/vision.py` are provider-agnostic and the
Anthropic path stays tested rather than rotting.

`moonshotai/kimi-k2.6` was picked against the live model list, not a blog post: it
is the current general Moonshot model with **both** image input and structured
outputs, at 262k context and roughly a tenth of Sonnet's per-token cost. Note that
`moonshotai/kimi-k2` — the obvious-looking slug — is text-only with no structured
output, and would have silently broken the vision path and schema enforcement.

**Three things about OpenRouter that shape the implementation.**

*Support is per endpoint, not per model.* Of the 22 providers serving kimi-k2.6,
at least one reports no `structured_outputs` and another no `response_format`, so
default routing can land somewhere the schema is ignored. Every request therefore
sets `provider: {require_parameters: true}`. `OPENROUTER_IGNORE_PROVIDERS` is the
escape hatch for excluding a specific provider without a deploy.

*A schema is a request, not a promise.* OpenRouter's own docs say some providers
"guarantee schema-conforming output, while others translate your schema or treat
it as a strong hint". So `llm.enforce_schema` validates every response, on both
providers, and it is the real guarantee. Additive deviations — keys forbidden by
`additionalProperties: false` — are pruned and logged, because dropping them
loses nothing we asked for and failing an already-paid-for review would be worse.
Substantive ones — a missing required field, a wrong type, a value outside an enum
— raise. A findings list with an invented status is worse than no findings list.

*Effort had to be re-homed.* kimi-k2.6 exposes no `reasoning_effort`, so the
per-stage tuning moved to OpenRouter's unified `reasoning: {effort}`, which takes
the same vocabulary and maps it to the nearest level the endpoint supports. The
intent survives: evaluate decides the score and runs at `high`, everything else at
`medium`. `exclude: true` keeps reasoning text out of the response, since we never
read it and reasoning tokens bill as output tokens.

Caching still applies but is no longer ours to place: Moonshot caching via
OpenRouter is automatic and takes no breakpoints, so the explicit `cache_control`
on the rubric prefix is dropped on this path. The prefix is still byte-identical
between reviews, which is what implicit caching keys on.

### The output-token squeeze

`OPENROUTER_MAX_COMPLETION_TOKENS` (default 32000) caps every request, and
`finish_reason: "length"` raises `TruncatedResponse` rather than surfacing as a
JSON decode error.

**This is genuinely tight and worth fixing properly.** The evaluate stage asks for
32,000 output tokens, while the lowest-capability endpoint serving this model
advertises 16,384 — and reasoning tokens count against the same budget. Most
endpoints offer 262,144, so routing normally avoids the problem, but the margin
depends on routing rather than on design. The real fix is to split evaluate from
one call per framework (26 and 19 checks) into one per pillar (13 calls of 2–7
checks), which would drop each request to a few thousand output tokens and remove
the dependence entirely. That is a pipeline change and is deliberately not in this
one; until then, `TruncatedResponse` names the cause and `OPENROUTER_IGNORE_PROVIDERS`
can exclude a low-cap provider.

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

A provider key is required — `OPENROUTER_API_KEY` by default — and the pipeline
fails at the classify stage without it, with the error surfacing in the UI rather
than silently.

## Deploying

**Order matters: Render first, then Vercel, then one setting back on Render.**
Each side needs the other's URL, so it is a two-step with a follow-up.

### 1. Backend on Render

Point Render at this repo; `render.yaml` defines the service (free tier, Python
3.13, health check on `/health`). Set two environment variables in the dashboard
— both are marked `sync: false` so they are never read from the repo:

| Variable | Value |
| --- | --- |
| `OPENROUTER_API_KEY` | your key, for the default provider. Never committed; `.env` is gitignored. |
| `ANTHROPIC_API_KEY` | only needed if you set `LLM_PROVIDER=anthropic`. |
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
cd backend && pip install -r requirements-dev.txt && python -m pytest tests -q   # 224 tests
cd frontend && npm test                                                          # 37 tests
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
calls complete, the model resolves to the provider's expected default, structured
output is accepted (`response_format` on OpenRouter, the beta header on
Anthropic), and the responses validate
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
