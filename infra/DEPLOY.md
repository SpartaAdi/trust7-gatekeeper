# Deploying Trust7 Gatekeeper

The whole backend is one SAM stack: a Lambda function, an API Gateway HTTP API,
an S3 bucket, and two DynamoDB tables. There is no VPC, no NAT Gateway, no EC2,
and no RDS — nothing in the stack runs when no one is using it.

## Prerequisites

- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- AWS credentials with permission to create the resources above
- Python 3.13 (matching the Lambda runtime) if you want `sam build` to use a
  local interpreter instead of `--use-container`

## Deploy

Run these from your own machine, in order. Steps 1 and 2 must be run by a human:
the API key is read from a hidden prompt, and no automated agent should handle it.

### 1. Build

```bash
cd infra
sam build
```

`sam build` runs `backend/Makefile` for each function and `rubric/Makefile` for
the rubric layer. Every path inside those makefiles is relative to its own
directory — SAM copies each `CodeUri` into a scratch directory before running
`make`, so a `../` reference out of it does not resolve at build time.

### 2. Create the API key secret

```bash
./scripts/create-secret.sh          # prompts for the key; prints only the ARN
```

The key is read from a hidden prompt (never in shell history) and handed to the
AWS CLI as a file reference (never in the process list). The temp file is in
tmpfs where available and shredded on exit. Copy the printed ARN for step 3.

### 3. Deploy the stack

```bash
sam deploy --guided
```

Answer:

| Prompt | Answer |
| --- | --- |
| Stack Name | `trust7-gatekeeper` |
| AWS Region | `ap-south-1` |
| Parameter ProjectTag | `trust7gatekeeper` |
| Parameter AnthropicApiKeySecretArn | the ARN from step 2 |
| Parameter CorsAllowOrigins | `http://localhost:5173` for now; re-deploy with the Amplify URL once step 5 is done |
| Confirm changes before deploy | **Y** |
| Allow SAM CLI IAM role creation | **Y** |
| Disable rollback | **N** |
| Save arguments to configuration file | **Y** |

Your answers are written to `samconfig.toml`, so later deploys are just
`sam build && sam deploy`.

### 4. Cost guardrail

```bash
./scripts/create-budget.sh          # $25/month, alerts at 50/80/100%
```

AWS emails a subscription confirmation to the alert address — **alerts do not
fire until that link is clicked.**

The budget is account-wide, not scoped to the project tag. A tag-filtered budget
needs `user:project` activated as a cost allocation tag in Billing first, and it
only starts collecting data after activation (up to 24 hours), so a tag-scoped
budget on a fresh account silently reports zero. Once the tag is active, add
`"CostFilters": {"TagKeyValue": ["user:project$trust7gatekeeper"]}` to the budget
JSON in the script.

### 5. Frontend on Amplify Hosting

Amplify needs authorization to read the repository, which is an account-level
decision — pick one:

**a. Repo-connected (CI on every push).** Requires connecting GitHub to Amplify
once, via the console (`New app → Host web app → GitHub`) so the Amplify GitHub
App gets installed on `SpartaAdi/trust7-gatekeeper`. This cannot be done purely
from the CLI without a personal access token. Then set:

- Monorepo app root: `frontend`
- Build settings: as below
- Environment variable: `VITE_API_BASE_URL` = the `ApiUrl` output from step 3

```yaml
version: 1
applications:
  - appRoot: frontend
    frontend:
      phases:
        preBuild:
          commands:
            - npm ci
        build:
          commands:
            - npm run build
      artifacts:
        baseDirectory: dist
        files:
          - '**/*'
      cache:
        paths:
          - node_modules/**/*
```

**b. Manual deploy (no repo access needed).** Build locally and upload the
bundle — no GitHub authorization, but no CI either:

```bash
cd frontend
VITE_API_BASE_URL="<ApiUrl from step 3>" npm run build
cd dist && zip -r ../dist.zip . && cd ..
aws amplify create-app --name trust7-gatekeeper \
  --tags project=trust7gatekeeper --region ap-south-1
# then: create-branch, create-deployment, upload dist.zip to the returned URL,
#       start-deployment
```

`VITE_API_BASE_URL` is baked in at build time, so changing it means rebuilding.

**After the Amplify URL exists**, re-deploy the stack with it in
`CorsAllowOrigins`, or the browser will be blocked by CORS:

```bash
sam deploy --parameter-overrides \
  "CorsAllowOrigins=https://<branch>.<app-id>.amplifyapp.com"
```

### 6. Verify

```bash
./scripts/verify-deployment.sh
```

Checks `/health`, lists deployed resource types, asserts zero VPC / NAT Gateway /
EC2 / RDS resources, confirms neither Lambda is VPC-attached, and counts
project-tagged resources. Exits non-zero on any failure.

`--guided` prompts for a stack name, region, and the template parameters, then
writes your answers to `samconfig.toml` so later deploys are just:

```bash
sam build && sam deploy
```

Parameters you will be asked for:

| Parameter | Default | Notes |
| --- | --- | --- |
| `ProjectTag` | `trust7gatekeeper` | Applied as the `project` tag on every resource. Leave as-is. |
| `AnthropicApiKeySecretArn` | *(empty)* | ARN of a Secrets Manager secret holding the Anthropic API key. Never put the key itself in the template, in `samconfig.toml`, or in git. |
| `CorsAllowOrigins` | `http://localhost:5173` | Comma-separated origins allowed to call the API. Set this to the deployed frontend origin. |

Answer **yes** to "Allow SAM CLI IAM role creation" — the function needs an
execution role for its S3 and DynamoDB access.

When the deploy finishes, SAM prints the stack outputs, including `ApiUrl`.
Point the frontend at that URL.

## Verify

```bash
curl "$(aws cloudformation describe-stacks \
  --stack-name <your-stack-name> \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)/health"
```

Expect `{"status":"ok","service":"trust7-gatekeeper"}`.

## Cost shape

Everything in the stack is request-priced, so an idle stack costs approximately
nothing:

- **Lambda** — billed per invocation and GB-second. No provisioned concurrency.
- **API Gateway HTTP API** — billed per request.
- **DynamoDB** — both tables are `PAY_PER_REQUEST` (on-demand). No provisioned
  capacity. `review_status` rows carry a TTL so transient progress records
  expire on their own.
- **S3** — billed for what is stored. Uploads are the only thing that
  accumulates; delete old ones if storage grows.
- **CloudWatch Logs** — retained 30 days, then dropped automatically.

The one cost that is *not* in this stack is the Anthropic API. Calls are
pay-per-token against the Claude API directly (not Bedrock), with no provisioned
throughput, and are billed to your Anthropic account rather than to AWS.

## Tear down

```bash
cd infra
sam delete
```

This deletes the stack and prompts before removing the SAM-managed artifact
bucket.

Two things survive the delete and need attention:

1. **The uploads bucket must be emptied first.** CloudFormation cannot delete a
   non-empty bucket, and versioning is on, so object versions count too:

   ```bash
   aws s3api delete-objects --bucket <uploads-bucket-name> \
     --delete "$(aws s3api list-object-versions --bucket <uploads-bucket-name> \
       --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json)"
   ```

   Repeat for `DeleteMarkers` if any exist, then re-run `sam delete`.

2. **The Secrets Manager secret** holding the Anthropic API key is not part of
   this stack. Delete it separately if it is no longer needed, and rotate the
   key at the Anthropic console if it may have been exposed.

To confirm nothing is left behind, list everything still carrying the project
tag:

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=project,Values=trust7gatekeeper
```
