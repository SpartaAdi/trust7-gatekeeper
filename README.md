# Trust7 Gatekeeper

An AI governance agent that reviews solution designs — SoW / solution documents
plus architecture diagrams — against two frameworks:

- **AWS Well-Architected Framework** — Operational Excellence, Security,
  Reliability, Performance Efficiency, Cost Optimization, Sustainability.
- **Minfy TRUST-7** — Trust foundations, Risk & resilience, Unit economics,
  Sovereignty & supply chain, Talent & adoption, Sustainability, AI governance.

The rubric stays general to both frameworks' principles; it is not tuned to any
single example design or client. Revised designs can be re-reviewed, and the
result shows a score delta against the prior review.

## Architecture

```
                 draw.io XML ──► deterministic parser (no LLM)
                                          │
   upload  ──►  S3 (uploads)              ├──►  ONE common component schema
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
                            structured JSON  ──►  DynamoDB `reviews`
                                          │
                                          ▼
                                   dashboard UI
                        Step 1 Upload ─► Step 2 Analyzing ─► Step 3 Results

   Per-stage progress: each pipeline stage writes to DynamoDB `review_status`;
   the UI polls it, so the progress bar reflects real stage state.
```

Everything runs serverless on AWS: Lambda behind an API Gateway HTTP API, S3 for
uploads, DynamoDB (on-demand) for reviews and progress. No EC2, no RDS, no NAT
Gateway, nothing always-on. The LLM is the Claude API accessed directly via the
Anthropic API — not Bedrock — pay-per-token with no provisioned throughput.

## Layout

```
backend/          FastAPI app, wrapped by Mangum for Lambda
  ingestion/      document + diagram intake and normalization
  agent/          the multi-stage review pipeline
  api/            routers (upload, review, status, results)
  main.py         Lambda entrypoint — `handler = Mangum(app)`
frontend/         Vite + React + Tailwind
rubric/           rubric.json — the framework-general scoring rubric
infra/            AWS SAM template and deployment notes
```

Backend pipeline and frontend flow are both implemented.

```
frontend/src/
  App.tsx              single-page flow: Upload -> Analyzing -> Results
  api.ts               the only module that calls the API
  types.ts             mirrors backend/schema.py — the wire shapes
  maturity.ts          score -> Aware/Managed/Governed/Certified/Pioneering
  components/          StepTracker, FilePicker
  views/               UploadView, AnalyzingView, ResultsView
```

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/uploads` | Returns a presigned S3 URL; the browser `PUT`s the file directly, so uploads aren't capped by Lambda's 6 MB payload limit. |
| `POST` | `/reviews` | Submits `document_key` / `diagram_key`. Returns `202` with a `review_id`. |
| `POST` | `/reviews/{id}/reanalyze` | Same, but scored against review `{id}` and returned with a score delta. |
| `GET` | `/reviews/{id}/status` | Per-stage progress, written by the pipeline as each stage runs. This is what the UI polls. |
| `GET` | `/reviews/{id}` | The finished review as structured JSON — scores, findings, remediation, and the score delta if this was a re-review. |
| `GET` | `/health` | Liveness probe. |

The review runs in a second Lambda invoked asynchronously: a full analysis takes
minutes, well past API Gateway's 30-second request ceiling.

Two cost controls are built into the model calls. The rubric is byte-identical on
every review and sits behind a prompt-cache breakpoint, so repeat reviews read it
from cache rather than paying for it again. And effort is set per stage — the
evaluation stage decides the score and gets `high`, while classification and
remediation run at `medium`.

## Tests

```bash
cd backend
python -m pytest tests -q
```

The deterministic parts — draw.io parsing, scoring, and delta computation — are
covered without any model call. `not_applicable` checks are excluded from the
score rather than counted as failures, so an irrelevant check neither helps nor
hurts; the tests pin that behaviour along with severity weighting and both
directions of the score delta.

## Setup

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env    # then fill in ANTHROPIC_API_KEY
uvicorn main:app --reload
```

`GET http://127.0.0.1:8000/health` should return
`{"status":"ok","service":"trust7-gatekeeper"}`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Serves on http://localhost:5173, which is the default allowed CORS origin. Copy
`frontend/.env.example` to `frontend/.env` and set `VITE_API_BASE_URL` to point
at a deployed stack; it defaults to the local backend.

`npm run build` typechecks before bundling, so a shape mismatch between
`src/types.ts` and the API fails the build rather than rendering `undefined`.

### Infrastructure

```bash
cd infra
sam build && sam deploy --guided
```

See [infra/DEPLOY.md](infra/DEPLOY.md) for parameters and verification.

## Configuration

Copy `.env.example` to `.env`. `ANTHROPIC_API_KEY` is the only value required
for local work. On AWS, the deployed function reads its bucket and table names
from environment variables set by the SAM stack, and the Anthropic key comes
from a Secrets Manager secret whose ARN is passed as a stack parameter. The key
itself never goes into the template, `samconfig.toml`, or git.

## Cost & Teardown

Every AWS resource in this project is request-priced — Lambda, HTTP API,
on-demand DynamoDB, S3 — so an idle deployment costs approximately nothing.
Claude API usage is pay-per-token and billed separately by Anthropic. All
resources carry the tag `project=trust7gatekeeper`, so spend and leftovers can
be tracked with a single tag filter.

Full cost breakdown, the teardown procedure (`sam delete`), and the two things
that survive it — the uploads bucket's object versions and the Anthropic API key
secret — are documented in **[infra/DEPLOY.md](infra/DEPLOY.md)**.
