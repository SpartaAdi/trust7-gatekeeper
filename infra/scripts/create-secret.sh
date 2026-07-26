#!/usr/bin/env bash
# Creates the Anthropic API key secret without exposing the key.
#
# The key is read from a hidden prompt, so it never enters shell history, and is
# passed to the AWS CLI via a file reference rather than an argument, so it never
# appears in the process list (`ps` shows a command's arguments to other users on
# the same host). The temp file lives in tmpfs where available and is shredded on
# exit, including on error.
#
# Prints only the secret's ARN. Never the key.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
SECRET_NAME="${SECRET_NAME:-trust7gatekeeper/anthropic-api-key}"

command -v aws >/dev/null || { echo "aws CLI not found." >&2; exit 1; }

# Prefer tmpfs so the key never touches disk.
if [ -d /dev/shm ] && [ -w /dev/shm ]; then
  KEY_FILE="$(mktemp /dev/shm/t7key.XXXXXXXX)"
else
  KEY_FILE="$(mktemp)"
  echo "Note: /dev/shm unavailable; using $KEY_FILE on disk (shredded on exit)." >&2
fi
cleanup() { shred -u "$KEY_FILE" 2>/dev/null || rm -f "$KEY_FILE"; }
trap cleanup EXIT INT TERM

printf 'Anthropic API key (input hidden, not echoed): ' >&2
IFS= read -rs API_KEY
printf '\n' >&2

if [ -z "${API_KEY:-}" ]; then
  echo "No key entered; nothing created." >&2
  exit 1
fi

printf '%s' "$API_KEY" >"$KEY_FILE"
unset API_KEY

if aws secretsmanager describe-secret \
  --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "Secret $SECRET_NAME already exists — storing a new version." >&2
  aws secretsmanager put-secret-value \
    --secret-id "$SECRET_NAME" \
    --secret-string "file://$KEY_FILE" \
    --region "$REGION" \
    --query ARN --output text
else
  aws secretsmanager create-secret \
    --name "$SECRET_NAME" \
    --description "Anthropic API key for Trust7 Gatekeeper" \
    --secret-string "file://$KEY_FILE" \
    --tags Key=project,Value=trust7gatekeeper \
    --region "$REGION" \
    --query ARN --output text
fi
