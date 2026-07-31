#!/usr/bin/env bash
#
# Run ONE real review against a deployed Trust7 Gatekeeper service and print what
# came back — including the three data-fidelity numbers.
#
# Written for verifying the Docker-runtime service, whose reason to exist is that
# Tesseract is installed in it. So this uploads a PNG diagram, deliberately: the OCR
# coverage proxy is only ever computed on the IMAGE path, and a `.drawio` upload
# returns `ocr_proxy: null` — which is correct, and looks exactly like Tesseract
# being broken. A .drawio would verify the wrong thing.
#
#   TRUST7_DEMO_TOKEN, exported first, is the demo gate's shared token. Read it in
#   without putting it in your shell history or in the process list:
#
#     read -rs -p 'Demo token: ' TRUST7_DEMO_TOKEN && export TRUST7_DEMO_TOKEN
#     ./scripts/verify_docker_service.sh
#
#   Against a different service, or a local container:
#
#     ./scripts/verify_docker_service.sh https://some-other-service.onrender.com
#     ./scripts/verify_docker_service.sh http://127.0.0.1:8000
#
# READ-ONLY with respect to the code: it uploads two synthetic fixtures and creates
# one review. It touches no other review and no other service. It DOES spend real
# OpenRouter tokens — six model calls, two of them the evaluate stage's 64,000-token
# request — because there is no way to verify a real deployment without them.
#
# Exit: 0 the review completed | 1 something failed | 2 no token | 3 service unreachable

set -uo pipefail

BASE_URL="${1:-https://trust7-gatekeeper-backend-docker.onrender.com}"
BASE_URL="${BASE_URL%/}"

TOKEN_HEADER="X-Demo-Token"          # mirrors config.DEMO_TOKEN_HEADER
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOW="$REPO/fixtures/verification/order-intake-sow.md"
DIAGRAM="$REPO/fixtures/verification/order-intake-architecture.png"

# Render's free tier spins down when idle and a cold start has been measured at
# ~23s on this service, so every timeout here is generous on purpose. The review
# itself is polled rather than waited on, because observed end-to-end latency has
# ranged from well under a minute to far longer depending on provider load.
CONNECT_TIMEOUT=30
REQUEST_TIMEOUT=180
POLL_INTERVAL=5
POLL_LIMIT=360                        # 30 minutes at 5s

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\n!! %s\n' "$1" >&2; }

# python3 rather than jq: this is a Python repo, so python3 is certain to be here
# and jq is not. The formatting lives in a companion .py file rather than in
# `python3 -c` strings, because the report needs double quotes inside f-string
# expressions and those cannot be escaped through the shell before Python 3.12.
FMT="$REPO/scripts/verify_docker_service.py"
[[ -f "$FMT" ]] || { fail "Missing helper: $FMT"; exit 1; }

json() { python3 "$FMT" field "$@"; }

api() {
  # $1 method, $2 path, rest passed to curl. Body on stdout, status on stderr-free
  # last line via -w so the caller can split it.
  local method="$1" path="$2"; shift 2
  curl -sS -X "$method" \
    --connect-timeout "$CONNECT_TIMEOUT" --max-time "$REQUEST_TIMEOUT" \
    -H "$TOKEN_HEADER: $TRUST7_DEMO_TOKEN" \
    -w $'\n%{http_code}' \
    "$@" "$BASE_URL$path"
}

split_status() { STATUS="${1##*$'\n'}"; BODY="${1%$'\n'*}"; }

# --------------------------------------------------------------------------- #
bold "0. Preflight"

if [[ -z "${TRUST7_DEMO_TOKEN:-}" ]]; then
  fail "TRUST7_DEMO_TOKEN is not set."
  cat >&2 <<'EOF'

Set it without leaving it in your shell history:

    read -rs -p 'Demo token: ' TRUST7_DEMO_TOKEN && export TRUST7_DEMO_TOKEN

It is the DEMO_ACCESS_TOKEN environment variable set on the service in the Render
dashboard (Environment tab). Nothing in this repository contains it.
EOF
  exit 2
fi

for file in "$SOW" "$DIAGRAM"; do
  [[ -f "$file" ]] || { fail "Missing fixture: $file"; exit 1; }
done

printf '  target        %s\n' "$BASE_URL"
printf '  token         set (%d chars, value never printed)\n' "${#TRUST7_DEMO_TOKEN}"
printf '  SoW           %s (%s bytes)\n' "$(basename "$SOW")" "$(wc -c <"$SOW" | tr -d ' ')"
printf '  diagram       %s (%s bytes, PNG — exercises vision AND the OCR proxy)\n' \
  "$(basename "$DIAGRAM")" "$(wc -c <"$DIAGRAM" | tr -d ' ')"

# /health is the one ungated route, so this separates "service is down" from
# "token is wrong" before anything is uploaded.
printf '\n  GET /health ... '
HEALTH=$(curl -sS --connect-timeout "$CONNECT_TIMEOUT" --max-time "$REQUEST_TIMEOUT" \
  -w $'\n%{http_code}' "$BASE_URL/health" 2>&1) || { fail "unreachable: $HEALTH"; exit 3; }
split_status "$HEALTH"
if [[ "$STATUS" != "200" ]]; then fail "HTTP $STATUS from /health: $BODY"; exit 3; fi
printf 'HTTP 200 %s\n' "$BODY"

# --------------------------------------------------------------------------- #
bold "1. Upload the two fixtures  (POST /uploads)"

upload() {
  local path="$1" response
  response=$(api POST /uploads -F "file=@$path") || { fail "upload failed"; return 1; }
  split_status "$response"
  if [[ "$STATUS" != "200" ]]; then
    fail "HTTP $STATUS uploading $(basename "$path"): $BODY"
    return 1
  fi
  printf '%s' "$BODY"
}

SOW_BODY=$(upload "$SOW") || exit 1
SOW_KEY=$(printf '%s' "$SOW_BODY" | json key)
printf '  %-34s -> %s\n' "$(basename "$SOW")" "$SOW_KEY"

DIAGRAM_BODY=$(upload "$DIAGRAM") || exit 1
DIAGRAM_KEY=$(printf '%s' "$DIAGRAM_BODY" | json key)
printf '  %-34s -> %s\n' "$(basename "$DIAGRAM")" "$DIAGRAM_KEY"

[[ -n "$SOW_KEY" && -n "$DIAGRAM_KEY" ]] || { fail "an upload returned no key"; exit 1; }

# --------------------------------------------------------------------------- #
bold "2. Start the review  (POST /reviews)"

REQUEST=$(python3 "$FMT" request "$SOW_KEY" "$DIAGRAM_KEY")

RESPONSE=$(api POST /reviews -H "Content-Type: application/json" -d "$REQUEST") || exit 1
split_status "$RESPONSE"
if [[ "$STATUS" != "202" ]]; then fail "HTTP $STATUS from /reviews: $BODY"; exit 1; fi

REVIEW_ID=$(printf '%s' "$BODY" | json review_id)
[[ -n "$REVIEW_ID" ]] || { fail "no review_id returned: $BODY"; exit 1; }
printf '  HTTP 202  review_id %s\n' "$REVIEW_ID"
printf '  result    %s/reviews/%s\n' "$BASE_URL" "$REVIEW_ID"

# --------------------------------------------------------------------------- #
bold "3. Poll the pipeline  (GET /reviews/{id}/status)"
printf '  Six model calls run here. No ETA is printed on purpose — observed latency\n'
printf '  on this provider has ranged from seconds to tens of minutes.\n\n'

STARTED=$(date +%s)
SEEN=""
STATE=""
for ((i = 0; i < POLL_LIMIT; i++)); do
  RESPONSE=$(api GET "/reviews/$REVIEW_ID/status") || { sleep "$POLL_INTERVAL"; continue; }
  split_status "$RESPONSE"
  [[ "$STATUS" == "200" ]] || { sleep "$POLL_INTERVAL"; continue; }

  # One line per NEW stage transition, so the log is a record rather than a redraw.
  LINES=$(printf '%s' "$BODY" | python3 "$FMT" stages)
  while IFS='|' read -r state name detail; do
    [[ "$state" == "STATE" ]] && { STATE="$name"; FINAL_DETAIL="$detail"; continue; }
    key="$state|$name|$detail"
    case "$SEEN" in *"$key"*) continue ;; esac
    SEEN="$SEEN$key"
    mark='·'
    case "$state" in
      done) mark='OK' ;; running) mark='..' ;; error) mark='XX' ;;
      rejected) mark='NO' ;; cancelled) mark='--' ;;
    esac
    printf '  [%4ds] %-2s %-11s %s\n' "$(( $(date +%s) - STARTED ))" "$mark" "$name" "$detail"
  done <<<"$LINES"

  case "$STATE" in
    complete|error|cancelled|rejected) break ;;
  esac
  sleep "$POLL_INTERVAL"
done

ELAPSED=$(( $(date +%s) - STARTED ))
printf '\n  final state: %s   after %ds\n' "${STATE:-unknown}" "$ELAPSED"

if [[ "$STATE" != "complete" ]]; then
  fail "the review did not complete (state=${STATE:-timed out}). ${FINAL_DETAIL:-}"
  printf 'Status:  %s/reviews/%s/status\n' "$BASE_URL" "$REVIEW_ID" >&2
  exit 1
fi

# --------------------------------------------------------------------------- #
bold "4. The result  (GET /reviews/{id})"

RESPONSE=$(api GET "/reviews/$REVIEW_ID") || exit 1
split_status "$RESPONSE"
if [[ "$STATUS" != "200" ]]; then fail "HTTP $STATUS fetching the review: $BODY"; exit 1; fi

printf '%s' "$BODY" | python3 "$FMT" report || {
  fail "could not format the result; the raw body follows"
  printf '%s\n' "$BODY"
  exit 1
}

bold "Done"
printf '  review     %s/reviews/%s\n' "$BASE_URL" "$REVIEW_ID"
printf '  PDF        %s/reviews/%s/report.pdf\n' "$BASE_URL" "$REVIEW_ID"
printf '  versions   %s/reviews/%s/versions\n' "$BASE_URL" "$REVIEW_ID"
printf '\n  The OCR-proxy line above is the one that tells you whether the Docker\n'
printf '  image did its job. "~N%% ESTIMATED" means tesseract ran. "NOT MEASURED"\n'
printf '  means the binary is absent, which is the native runtime, not this one.\n'
