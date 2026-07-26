#!/usr/bin/env bash
# Post-deploy verification: health check, plus proof the stack contains no
# always-on infrastructure.
#
# Exits non-zero if the health check fails or a forbidden resource type is found.
set -euo pipefail

STACK_NAME="${STACK_NAME:-trust7-gatekeeper}"
REGION="${AWS_REGION:-ap-south-1}"

command -v aws >/dev/null || { echo "aws CLI not found." >&2; exit 1; }

API_URL="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text)"

echo "Stack:   $STACK_NAME ($REGION)"
echo "API URL: $API_URL"
echo

echo "--- health check ---"
HTTP_STATUS="$(curl -sS -o /tmp/t7health.json -w '%{http_code}' "$API_URL/health")"
cat /tmp/t7health.json
echo
if [ "$HTTP_STATUS" != "200" ]; then
  echo "FAIL: /health returned $HTTP_STATUS" >&2
  exit 1
fi
echo "OK: /health returned 200"
echo

echo "--- deployed resource types ---"
aws cloudformation list-stack-resources \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'StackResourceSummaries[].ResourceType' --output text |
  tr '\t' '\n' | sort | uniq -c
echo

echo "--- forbidden resource audit (VPC / NAT / EC2 / RDS) ---"
FORBIDDEN="$(aws cloudformation list-stack-resources \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'StackResourceSummaries[].ResourceType' --output text |
  tr '\t' '\n' | grep -Ei '::EC2::|::RDS::|NatGateway|::VPC' || true)"

if [ -n "$FORBIDDEN" ]; then
  echo "FAIL: forbidden resource types present:" >&2
  echo "$FORBIDDEN" >&2
  exit 1
fi
echo "OK: zero VPC, NAT Gateway, EC2, and RDS resources in the stack."
echo

echo "--- Lambda VPC attachment check ---"
# A VPC-attached function would need a NAT Gateway for egress, so verify the
# functions are genuinely outside any VPC.
for logical in ApiFunction WorkerFunction; do
  fn="$(aws cloudformation describe-stack-resource \
    --stack-name "$STACK_NAME" --region "$REGION" \
    --logical-resource-id "$logical" \
    --query 'StackResourceDetail.PhysicalResourceId' --output text)"
  subnets="$(aws lambda get-function-configuration \
    --function-name "$fn" --region "$REGION" \
    --query 'length(VpcConfig.SubnetIds || `[]`)' --output text)"
  if [ "$subnets" != "0" ]; then
    echo "FAIL: $logical is attached to a VPC ($subnets subnets)" >&2
    exit 1
  fi
  echo "OK: $logical has no VPC attachment"
done
echo

echo "--- resources carrying the project tag ---"
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=project,Values=trust7gatekeeper \
  --region "$REGION" \
  --query 'length(ResourceTagMappingList)' --output text |
  sed 's/^/tagged resources: /'
