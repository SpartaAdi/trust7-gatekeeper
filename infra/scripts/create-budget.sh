#!/usr/bin/env bash
# Creates a monthly cost budget with alerts at 50%, 80%, and 100% of actual spend.
#
# The budget is account-wide on purpose. A tag-filtered budget
# (user:project$trust7gatekeeper) only works once that cost allocation tag has
# been activated in Billing, which can take up to 24 hours to start collecting —
# so a tag-scoped budget would silently report nothing on a fresh account. See
# the note in DEPLOY.md for switching it later.
#
# The Budgets API is global and lives in us-east-1 regardless of stack region.
set -euo pipefail

AMOUNT="${BUDGET_AMOUNT:-25}"
EMAIL="${BUDGET_EMAIL:-aditya.singh@minfytech.com}"
BUDGET_NAME="${BUDGET_NAME:-trust7-gatekeeper-monthly}"

command -v aws >/dev/null || { echo "aws CLI not found." >&2; exit 1; }
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

cat >"$WORK_DIR/budget.json" <<JSON
{
  "BudgetName": "$BUDGET_NAME",
  "BudgetLimit": { "Amount": "$AMOUNT", "Unit": "USD" },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
JSON

# One notification per threshold, all on actual (not forecast) spend.
{
  printf '['
  for threshold in 50 80 100; do
    [ "$threshold" = 50 ] || printf ','
    cat <<JSON
{
  "Notification": {
    "NotificationType": "ACTUAL",
    "ComparisonOperator": "GREATER_THAN",
    "Threshold": $threshold,
    "ThresholdType": "PERCENTAGE"
  },
  "Subscribers": [
    { "SubscriptionType": "EMAIL", "Address": "$EMAIL" }
  ]
}
JSON
  done
  printf ']'
} >"$WORK_DIR/notifications.json"

aws budgets create-budget \
  --account-id "$ACCOUNT_ID" \
  --budget "file://$WORK_DIR/budget.json" \
  --notifications-with-subscribers "file://$WORK_DIR/notifications.json" \
  --region us-east-1

echo "Budget '$BUDGET_NAME' created: \$$AMOUNT/month, alerts at 50/80/100% to $EMAIL"
echo "Note: AWS sends a subscription confirmation to that address — alerts do not"
echo "fire until it is confirmed."
