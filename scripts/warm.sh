#!/usr/bin/env bash
# Keep the Render free-tier backend awake during a demo window.
#
# Render's free tier spins a service down after ~15 minutes idle, and the next
# request then pays a ~30-50s cold start. Worse for us, the instance's disk is
# ephemeral: a spin-down loses local-data/, so uploads, reviews and history
# disappear. Pinging inside the idle window avoids both.
#
# This is the run-it-yourself option. It stops when the terminal closes or the
# laptop sleeps, so for an unattended demo prefer an external pinger — see the
# "Keeping the backend awake" section of README.md.
#
#   ./scripts/warm.sh https://trust7-gatekeeper-api.onrender.com
#
# /health needs no credentials: it is the one route the demo token gate skips.
set -uo pipefail

URL="${1:-}"
INTERVAL="${2:-600}"   # seconds; 600 = 10 min, comfortably inside the 15 min window

if [[ -z "$URL" ]]; then
  echo "usage: $0 <base-url> [interval-seconds]" >&2
  exit 64
fi

echo "Warming ${URL}/health every ${INTERVAL}s. Ctrl-C to stop."

while true; do
  # --max-time so a cold start cannot wedge the loop; a failure is logged and
  # the loop continues, because a transient 502 during a redeploy is expected.
  # curl already prints 000 when it cannot connect, so no || fallback is needed;
  # the :- guard only covers curl being missing entirely.
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 "${URL}/health" 2>/dev/null)
  code=${code:-000}
  if [[ "$code" == "200" ]]; then
    printf '%s  awake (200)\n' "$(date '+%H:%M:%S')"
  else
    printf '%s  NOT AWAKE (HTTP %s) — check the Render dashboard\n' "$(date '+%H:%M:%S')" "$code"
  fi
  sleep "$INTERVAL"
done
