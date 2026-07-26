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

From the repository root:

```bash
cd infra
sam build
sam deploy --guided
```

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
