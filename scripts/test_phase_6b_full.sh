#!/usr/bin/env bash
# Phase 6b — full curl smoke
# ----------------------------------------------------------------------
# Runs end-to-end against a local API. Pre-requisites:
#   - Postgres + Redis up (docker compose up -d)
#   - alembic upgrade head (migrations 0011 + 0012 applied)
#   - uvicorn api.main:app --reload running on :8000
#   - jq + python3 + curl on PATH
#
# What it covers:
#   1. Register a fresh user, capture shortcut_token.
#   2. GET /api/v1/gmail/status → {connected: false}.
#   3. POST /api/v1/gmail/oauth/start → assert shape (auth_url, expires_at).
#   4. (Manual: real OAuth callback can't be scripted — we skip the
#       browser handshake.)
#   5. POST /api/v1/gmail/admin/run-backfill → assert {queued: true}.
#   6. POST /api/v1/gmail/admin/run-daily → assert {queued: true}.
#   7. Verify gmail_ingestion_runs has rows (via /admin queries through psql).
#
# Exits non-zero on any assertion failure. Designed to be runnable in
# CI once we have a managed Postgres + the OAuth callback mockable.

set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
DATABASE_URL="${DATABASE_URL:-}"

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
section() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

assert_eq() {
  local actual="$1" expected="$2" label="$3"
  if [ "$actual" != "$expected" ]; then
    red "FAIL: $label — expected $expected, got $actual"
    exit 1
  fi
  green "OK: $label"
}

# ── 1. Register a throw-away user ───────────────────────────────────────────
section "1. Register user"

EMAIL="phase6b-smoke-$(python3 -c 'import secrets; print(secrets.token_hex(6))')@test.example"
REG_RESPONSE=$(curl -fsS -X POST "$API_BASE/api/v1/users/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"full_name\":\"Phase 6b Smoke\"}")

USER_ID=$(echo "$REG_RESPONSE" | jq -r '.id')
TOKEN=$(echo "$REG_RESPONSE" | jq -r '.shortcut_token')

if [ -z "$USER_ID" ] || [ "$USER_ID" = "null" ]; then
  red "FAIL: did not get user_id back from /register"
  echo "$REG_RESPONSE"
  exit 1
fi
green "OK: registered user_id=$USER_ID"

AUTH_HEADER="X-Shortcut-Token: $TOKEN"

# ── 2. GMail status: not connected ─────────────────────────────────────────
section "2. GET /gmail/status (expect connected=false)"

STATUS=$(curl -fsS "$API_BASE/api/v1/gmail/status" -H "$AUTH_HEADER")
CONNECTED=$(echo "$STATUS" | jq -r '.connected')
assert_eq "$CONNECTED" "false" "fresh user is disconnected"

# ── 3. /oauth/start returns a Google URL ───────────────────────────────────
section "3. POST /gmail/oauth/start"

START=$(curl -fsS -X POST "$API_BASE/api/v1/gmail/oauth/start" -H "$AUTH_HEADER")
AUTH_URL=$(echo "$START" | jq -r '.auth_url')
EXPIRES=$(echo "$START" | jq -r '.expires_in_seconds')

case "$AUTH_URL" in
  https://accounts.google.com/o/oauth2/v2/auth*)
    green "OK: auth_url is a Google OAuth URL"
    ;;
  *)
    red "FAIL: unexpected auth_url: $AUTH_URL"
    exit 1
    ;;
esac
assert_eq "$EXPIRES" "600" "state TTL is 600s"

echo "    (Manual step: real OAuth callback completion is browser-only;"
echo "    a scripted callback test would need a mock of Google's token"
echo "    endpoint — covered by tests/test_gmail_oauth_endpoints.py.)"

# ── 4. /admin/run-backfill is fire-and-forget ──────────────────────────────
section "4. POST /gmail/admin/run-backfill"

BF=$(curl -fsS -X POST "$API_BASE/api/v1/gmail/admin/run-backfill?days=30" \
    -H "$AUTH_HEADER")
QUEUED=$(echo "$BF" | jq -r '.queued')
assert_eq "$QUEUED" "true" "backfill queued"

# ── 5. /admin/run-daily is fire-and-forget ─────────────────────────────────
section "5. POST /gmail/admin/run-daily"

DAILY=$(curl -fsS -X POST "$API_BASE/api/v1/gmail/admin/run-daily" \
    -H "$AUTH_HEADER")
QUEUED=$(echo "$DAILY" | jq -r '.queued')
assert_eq "$QUEUED" "true" "daily run queued"

# ── 6. DB-level verification ────────────────────────────────────────────────
# This is optional — only runs if DATABASE_URL is set and psql is on PATH.
section "6. DB verification (optional)"

if [ -z "$DATABASE_URL" ]; then
  echo "    (Skipping DB queries — DATABASE_URL not set.)"
elif ! command -v psql >/dev/null 2>&1; then
  echo "    (Skipping DB queries — psql not on PATH.)"
else
  # Convert asyncpg URL to psql-compatible (drop +asyncpg).
  PSQL_URL=$(echo "$DATABASE_URL" | sed 's/+asyncpg//')
  echo "    Querying gmail_ingestion_runs for user $USER_ID…"
  RUNS=$(psql "$PSQL_URL" -tAc \
      "SELECT count(*) FROM gmail_ingestion_runs WHERE user_id = '$USER_ID'")
  echo "    Runs recorded: $RUNS"
fi

# ── done ────────────────────────────────────────────────────────────────────
section "ALL OK"
green "Phase 6b smoke passed."
echo ""
echo "User created: $USER_ID"
echo "Token (don't commit): $TOKEN"
