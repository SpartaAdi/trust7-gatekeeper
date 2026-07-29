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
  tests/          278 tests
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
scripts/warm.sh   keeps the Render free tier awake during a demo
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
| `GET`/`HEAD` | `/health` | Liveness probe, Render's health check, and the warm-up ping target. The only ungated route. |

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

## Provider routing

Requests carry an explicit, ordered allow-list rather than OpenRouter's default
routing:

```json
"provider": {
  "order": ["coreweave", "decart", "inceptron"],
  "allow_fallbacks": false,
  "require_parameters": true
}
```

Every slug was read from `/api/v1/providers` and cross-checked against
`/api/v1/models/moonshotai/kimi-k2.6/endpoints` — not inferred from a display name.
All three serve kimi-k2.6, all three advertise `response_format` **and**
`structured_outputs`, and all three advertise 262,144 max completion tokens, which
clears the evaluate stage's 64,000 with room to spare.

`allow_fallbacks: false` means a request none of the three can serve **fails** rather
than routing elsewhere. That is deliberate — it is the only way to know the
allow-list is honoured, and it stops a review silently paying Moonshot-direct prices
or landing on a 16k-output endpoint. All three being down is an outage for us;
`OPENROUTER_ALLOW_FALLBACKS=1` trades that back without a code change.

Requesting a route is not the same as getting one, so `llm._record_route` reads the
serving provider off every response (OpenRouter returns it on the body) and logs it:

```
route call=evaluate:aws_waf provider=CoreWeave model=moonshotai/kimi-k2.6 finish=stop out_tokens=8811 21.4s
```

A provider outside the order **raises** `ProviderNotAllowed`, and so does a response
that reports no provider at all. It used to only log, on the reasoning that a paid
response is still usable — and that reasoning failed in practice: a run was served by
Phala, which is not in the order, the ERROR line went unread, and the review
completed looking exactly like a correctly pinned one. A route that ignored the lock
invalidates every cost and output-ceiling assumption built on it.

An unreported provider raises for the same reason: the point of the lock is
provability, and "no evidence of a violation" is not "evidence of compliance".
`OPENROUTER_ENFORCE_PROVIDER_LOCK=0` downgrades both back to a log line.

The exception message carries the OpenRouter request id, because that is the only
handle on their activity log — diagnosing a bad route without it is guesswork.

The label identifies which of the six pipeline calls a line belongs to;
`tests/test_pipeline_e2e.py` asserts every call site passes one.

### Deadlines

Every call carries a 120s client-side deadline (`OPENROUTER_TIMEOUT_SECONDS`). There
is no server-side deadline on a chat completion, so without one a hung upstream is
indistinguishable from a slow one — a real run stalled for 5,657 seconds and returned
malformed JSON.

Two details make the bound real rather than nominal. The SDK's own `max_retries` is
set to **0**, because it defaults to 2 and this module retries once itself: left
alone, one stalled call could burn 6 x timeout and turn a 120s ceiling back into an
hour. And because that removes the SDK's connection-level retry,
`_openrouter_create_with_retry` now retries timeouts and connection errors as well as
5xx — one retry, so at most two attempts and at most 2 x the deadline per call.

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

`OPENROUTER_MAX_COMPLETION_TOKENS` (default 128000) caps every request, and
`finish_reason: "length"` raises `TruncatedResponse` rather than surfacing as a
JSON decode error. That guard earned its place: a real run truncated the evaluate
stage at 32000/32000 tokens with `finish_reason: "length"`, so a framework's
findings never completed.

Evaluate now asks for 64000; no other stage changed. Three things about that
number, because the obvious diagnosis was wrong:

*Evaluate was already split per framework* — 26 checks for AWS WAF, then 19 for
TRUST-7 — so the budget was never being asked to cover all 45 at once. Splitting
it "into two calls" is what it has always done.

*Reasoning shares the same budget.* `reasoning: {effort: "high"}` is carved out of
`max_tokens`, and OpenRouter allocates roughly 80% of it to reasoning at that
effort — leaving only ~6k of the old 32000 for the JSON. That is why a request
large enough on paper still ran out, and it means the cost of this headroom is
mostly reasoning tokens, which bill as output.

*64000 is chosen to keep routing wide.* Of the 22 endpoints serving kimi-k2.6,
only DeepInfra (16,384) and Venice (65,536) declare a cap below 256,000, so a
request at or under 65,536 still reaches 15 of them. Going higher would drop to 13
for headroom no framework needs. The only provider this excludes is DeepInfra,
which could not have served the old 32000 either.

The ceiling sits at 128000 as headroom and changes no request today: the clamp
takes the minimum, so evaluate still sends its own 64000. It used to double as a
routing guard — at 64000 it could not pass a request large enough to narrow the
provider set — so that job moved to `OPENROUTER_ROUTING_SAFE_COMPLETION_TOKENS`
(65,536), and a test asserts no stage exceeds it.

The remaining lever, if evaluate ever truncates again, is capping reasoning
explicitly with `reasoning: {max_tokens: N}` so the JSON gets a guaranteed share
rather than a leftover one. Splitting evaluate per pillar (13 calls of 2-7 checks)
is the other option and would remove the dependence entirely, at the cost of more
requests per review.

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

### Keeping the backend awake

A ping inside the 15-minute idle window prevents the spin-down, which avoids the
cold start *and* keeps `local-data/` alive for the duration — so for a demo it is
worth doing. `/health` is the one route the demo token gate skips, so a pinger
needs no credentials, and it answers both `GET` and `HEAD` so any monitor works.

**Recommended for an unattended demo — an external free pinger.** Nothing to run
and it survives your laptop closing. On [cron-job.org](https://cron-job.org) or
[UptimeRobot](https://uptimerobot.com), create one monitor:

| Setting | Value |
| --- | --- |
| URL | `https://<your-service>.onrender.com/health` |
| Interval | 5 or 10 minutes (must be under 15) |
| Method | `GET` or `HEAD` — both return 200 |

**For a window you are sitting through — run it locally.** One line, no signup:

```bash
while true; do curl -sS -o /dev/null -w "%{http_code} $(date +%H:%M:%S)\n" \
  https://<your-service>.onrender.com/health; sleep 600; done
```

Or the committed version, which logs legibly and keeps going through a failed
ping rather than exiting:

```bash
./scripts/warm.sh https://<your-service>.onrender.com
```

Two caveats. Free instance-hours are ~750/month across the account, and holding
one service up continuously spends ~720 of them — fine for a day, not as a
permanent arrangement, so turn the pinger off afterwards. And warming prevents
idle spin-down, not redeploys or platform restarts; if either happens,
`local-data/` is still lost.

## Brand and theming

Every colour and both type families live in `frontend/src/index.css` under
`@theme`; no component hardcodes a hex value. `backend/report.py` holds the PDF's
twin of each token, named in a comment beside it and asserted equal by
`tests/test_report.py`.

**Accessibility first, brand identity second** — that is the order the palette was
solved in. Three anchors are fixed, sampled from the live minfytech.com stylesheet:

| Token | Value | Role |
| --- | --- | --- |
| `minfy-navy` | `#1b263b` | header bar, dark sections, ink |
| `minfy-indigo` | `#1420be` | primary accent — buttons, active states, focus ring |
| `minfy-blue` | `#1c55bb` | hover state for primary actions |
| `minfy-yellow` | `#fdc921` | the logo mark's second colour, mark only |

Everything else is derived so that it clears WCAG AA — 4.5:1 body text, 3.0:1 large
text and meaningful graphics — on the **worst** surface it is used against, not the
average. That distinction is the whole exercise: `ink-muted` used to pass on the
mint block (4.56:1) while failing on the sky block (4.09:1).

| Token | Value | Worst measured pair |
| --- | --- | --- |
| `ink` | `#1b263b` | 13.79:1 on `pastel-sky` |
| `ink-muted` | `#47525e` | 7.25:1 on `pastel-sky` |
| `ink-faint` | `#606675` | 5.23:1 on `pastel-sky` |
| `sev-high` / `sev-medium` / `sev-low` | `#b3261e` / `#9b5600` / `#616874` | 5.03:1 on own 8% tint |
| `verdict-pass` | `#1e6b45` | 5.76:1 on own 8% tint |
| `pastel-sky` / `mint` / `tan` / `cream` / `teal` | `#eef5fe` / `#e5faed` / `#faf4ed` / `#fbf8de` / `#e7f8f7` | backgrounds |
| `surface` / `surface-sunken` / `hairline` | `#ffffff` / `#f6f7fd` / `#ccd2dc` | — |

The pastels are lighter than the raw site samples. That is the deliberate trade: the
sampled sky failed under the muted text the scorecard puts on it, and legibility
outranks fidelity to a swatch. The blocks read as a subtler tint as a result.

The ink tiers stay a hierarchy, not three barely-compliant greys — 15.1 / 8.0 / 5.8
on white, each step at least 30% apart, asserted by a test. AA is the floor here,
not the goal.

### The audit

```bash
python3 scripts/contrast_audit.py              # full table
python3 scripts/contrast_audit.py --fail-only  # just the misses
```

91 pairs, covering every real foreground/background combination the web app and the
PDF render. Two rules make it an audit rather than a list of nominal values: tokens
are **parsed** out of `index.css` and `report.py` rather than restated, and
translucent colours are **composited** onto the surface actually behind them
(`indigo/40`, `white/50`, `ink/15`, `sev-high/8`). `backend/tests/test_contrast.py`
runs the same check as a test, so a token change that breaks AA fails the suite.

This exists because a miss shipped. `ACCENT_ON_DARK` was picked by eye to fix an
unreadable PDF cover, looked right, and measured 4.12:1 — passing for the 13pt band
it was judged against and failing for the 8.5pt eyebrow next to it. The suite only
knew the hex had changed.

Two pairs pass with under 15% margin and are pinned in that test so a third shows up
as a failure rather than accumulating: `sev-medium` on its own 8% tint (5.03:1) and
the header's `text-white/50` inactive tab (4.85:1). Both need a component change
rather than a token one.

### Typography

A serif display face over a sans body, matching the site's Financier Display / Lato
pairing. Financier is commercially licensed, so it is named first in the stack and a
system serif renders in practice — **no webfont is fetched**, by design. The two
largest steps of the type scale (`.t-display`, `.t-title`) are the serif ones;
everything else is sans. `.t-tab` is the small-caps treatment used by the header nav
and the step tracker.

### The logo mark

`frontend/src/components/MinfyMark.tsx` is a REDRAW, not the supplied asset — the
logo arrived as an image in conversation and never landed on disk, so there was no
file to embed. The mark is pure geometry and redraws exactly; the "minfy" wordmark is
a custom typeface and is deliberately not reproduced, since hand-drawn letterforms
would be an imitation of the brand rather than the brand. `report._draw_minfy_mark`
draws the same geometry on the PDF cover. To swap in the real asset, replace those
two — nothing else references the shape.

## Tests

```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest tests -q   # 278 tests
cd frontend && npm test                                                          # 76 tests
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

## Copy fix-it prompt

A completed review with open findings offers a **Copy fix-it prompt** button beside
Download Report. It puts one plain-text block on the clipboard — the preamble plus a
numbered list of remediations — to paste into any image-capable assistant alongside
the diagram.

Pure display assembly of data already on the page: no request, no new field, nothing
derived that is not rendered a few hundred pixels above it.

`buildFixItPrompt` calls `selectTopActions`, the same function Top Action Items uses,
rather than re-filtering. That is the point: the prompt and the on-screen list cannot
disagree about which findings qualify, how many, or which one wins a pillar. Two
truncation rules over one dataset is a bug waiting for someone to notice the page
said ten and the clipboard said eight.

Remediation text is copied verbatim, falling back to the title exactly as the list
does — nothing is rephrased client-side. The text names no assistant, since whoever
pastes it may be using any of them.

## PDF export

The Results page has a **Download Report** button behind
`GET /reviews/{id}/report.pdf`. The document is a Minfy-branded cover (navy
`#1b263b`, the logo mark, one flat indigo `#1420be` band — no gradient), the
executive summary,
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
