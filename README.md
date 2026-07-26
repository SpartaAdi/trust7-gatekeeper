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

Only structure and config exist so far; the business logic is not implemented.

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

Serves on http://localhost:5173, which is the default allowed CORS origin.

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
