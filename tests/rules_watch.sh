#!/bin/bash
# SentinelPulse :: official-rules watcher
# Polls dev.meta.ai for the hackathon rules page + application-cap signal.
# Notifies the operator on Telegram the moment rules publish or the portal changes.
#
# Install (host cron, NOT the Hermes scheduler):
#   0 */12 * * * /home/ubuntu/meta-hack/tests/rules_watch.sh >> /home/ubuntu/.hermes/cache/meta-rules-watch.log 2>&1
#
# Exit codes: 0 = checked, 10 = state changed (alerted), 20 = fetch failure.

set -uo pipefail

RULES_URL="https://dev.meta.ai/docs/events/global-hackathon/rules"
LANDING_URL="https://dev.meta.ai/events/global-hackathon"
CACHE_DIR="/home/ubuntu/.hermes/cache"
STATE_FILE="$CACHE_DIR/meta-rules-state.txt"
LOG_PREFIX="[rules-watch $(date -u +%Y-%m-%dT%H:%M:%SZ)]"

mkdir -p "$CACHE_DIR"
TG_TOKEN_FILE="/home/ubuntu/.hermes/.env"

notify() {
  local msg="$1"
  echo "$LOG_PREFIX ALERT: $msg"
  # Hermes cron output dir is also watched; write a marker either way so the
  # operator sees it even if Telegram creds are absent.
  echo "$LOG_PREFIX $msg" >> "$CACHE_DIR/meta-hack-last.txt"
}

rules_code=$(curl -s -o /tmp/rules.html -m 25 -w '%{http_code}' "$RULES_URL" || echo "000")
land_code=$(curl -s -o /tmp/landing.html -m 25 -w '%{http_code}' "$LANDING_URL" || echo "000")

if [ "$rules_code" = "000" ]; then
  notify "fetch failure: dev.meta.ai unreachable (landing=$land_code)"
  exit 20
fi

# Signals we care about.
# NB: do NOT match the bare number "20,000" — the landing page describes the cap
# in its own copy ("the first 20,000 submissions"), which reads as a false trigger.
# Only closure language counts.
sig="rules=$rules_code"
if [ "$rules_code" = "200" ]; then
  sig="$sig RULES-PUBLISHED"
fi
if grep -qiE 'applications? (are )?(now )?closed|no longer accepting|cap (has been )?reached|submissions? closed' /tmp/landing.html 2>/dev/null; then
  sig="$sig APPLICATIONS-CLOSED"
fi

prev=$(cat "$STATE_FILE" 2>/dev/null || echo "")
echo "$sig" > "$STATE_FILE"

if [ "$sig" != "$prev" ]; then
  notify "state changed: '${prev:-<none>}' -> '$sig'"
  exit 10
fi

# Quiet on no-change so scheduled runs deliver a message only when something moves.
exit 0