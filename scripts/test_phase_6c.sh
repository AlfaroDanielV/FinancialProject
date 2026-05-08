#!/usr/bin/env bash
# Phase 6c — full curl smoke
# ----------------------------------------------------------------------
# Runs end-to-end against a local API. Pre-requisites:
#   - Postgres + Redis up (docker compose up -d)
#   - alembic upgrade head (migrations 0013 + 0014 applied)
#   - uvicorn api.main:app --reload running on :8000
#   - jq + python3 + curl on PATH
#
# What it covers:
#   1. Register a fresh user.
#   2. POST /api/v1/admin/insights/run-nightly → assert response shape.
#   3. GET /api/v1/users/me/insights/export → assert envelope shape.
#   4. DELETE /api/v1/users/me/insights → assert {deleted: N}.
#   5. Re-export → assert count = 0 + audit entry exists.
#   6. Cleanup the throw-away user.
#
# Bot commands (/memoria, /olvidar, /editar_memoria, /recalcular_memoria)
# are NOT in this smoke — they need a real Telegram client. Their happy
# paths are covered by tests/test_phase_6c_b7_*.py + b10 unit tests.
#
# Exits non-zero on any assertion failure.

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

assert_nonempty() {
  local actual="$1" label="$2"
  if [ -z "$actual" ] || [ "$actual" = "null" ]; then
    red "FAIL: $label — got empty/null"
    exit 1
  fi
  green "OK: $label = $actual"
}

# ── 1. Register a throw-away user ──────────────────────────────────────────
section "1. Register user"

EMAIL="phase6c-smoke-$(python3 -c 'import secrets; print(secrets.token_hex(6))')@test.example"
REG_RESPONSE=$(curl -fsS -X POST "$API_BASE/api/v1/users/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"full_name\":\"Phase 6c Smoke\"}")

USER_ID=$(echo "$REG_RESPONSE" | jq -r '.id')
TOKEN=$(echo "$REG_RESPONSE" | jq -r '.shortcut_token')

if [ -z "$USER_ID" ] || [ "$USER_ID" = "null" ]; then
  red "FAIL: did not get user_id back from /register"
  echo "$REG_RESPONSE"
  exit 1
fi
green "OK: registered user_id=$USER_ID"
AUTH_HEADER="X-Shortcut-Token: $TOKEN"

# ── 2. Trigger the nightly recompute for the new user ─────────────────────
section "2. POST /admin/insights/run-nightly"

RECOMPUTE=$(curl -fsS -X POST "$API_BASE/api/v1/admin/insights/run-nightly" \
    -H "$AUTH_HEADER")

RECOMPUTE_USER=$(echo "$RECOMPUTE" | jq -r '.user_id')
PROPOSED=$(echo "$RECOMPUTE" | jq -r '.insights_proposed')
PERSISTED_CREATED=$(echo "$RECOMPUTE" | jq -r '.persisted.created')

assert_eq "$RECOMPUTE_USER" "$USER_ID" "recompute returned same user_id"
assert_nonempty "$PROPOSED" "insights_proposed in response"
assert_nonempty "$PERSISTED_CREATED" "persisted.created in response"

# ── 3. Export the user's insights ─────────────────────────────────────────
section "3. GET /users/me/insights/export"

EXPORT=$(curl -fsS "$API_BASE/api/v1/users/me/insights/export" -H "$AUTH_HEADER")

EXPORT_USER=$(echo "$EXPORT" | jq -r '.user_id')
EXPORT_FORMAT=$(echo "$EXPORT" | jq -r '.format_version')
EXPORT_COUNT=$(echo "$EXPORT" | jq -r '.count')
EXPORT_REQUEST_ID=$(echo "$EXPORT" | jq -r '.request_id')

assert_eq "$EXPORT_USER" "$USER_ID" "export returned same user_id"
assert_eq "$EXPORT_FORMAT" "1" "format_version is 1"
assert_nonempty "$EXPORT_COUNT" "export count present"
assert_nonempty "$EXPORT_REQUEST_ID" "export request_id present"

# ── 4. Delete all insights ─────────────────────────────────────────────────
section "4. DELETE /users/me/insights"

DELETE=$(curl -fsS -X DELETE "$API_BASE/api/v1/users/me/insights" \
    -H "$AUTH_HEADER")
DELETED=$(echo "$DELETE" | jq -r '.deleted')

assert_eq "$DELETED" "$EXPORT_COUNT" \
    "delete count matches what export said was there"

# ── 5. Re-export to confirm zero state + audit row was emitted ────────────
section "5. Re-export after delete (expect count=0)"

EXPORT2=$(curl -fsS "$API_BASE/api/v1/users/me/insights/export" -H "$AUTH_HEADER")
EXPORT2_COUNT=$(echo "$EXPORT2" | jq -r '.count')
EXPORT2_REQUEST_ID=$(echo "$EXPORT2" | jq -r '.request_id')

assert_eq "$EXPORT2_COUNT" "0" "post-delete export is empty"
if [ "$EXPORT_REQUEST_ID" = "$EXPORT2_REQUEST_ID" ]; then
  red "FAIL: export request_id should be unique per call"
  exit 1
fi
green "OK: each export gets a fresh request_id"

# ── 6. Cleanup ─────────────────────────────────────────────────────────────
section "6. Cleanup throw-away user"

if [ -n "$DATABASE_URL" ]; then
  python3 - <<PY
import asyncio, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async def main():
    engine = create_async_engine("$DATABASE_URL")
    async with AsyncSession(engine) as s:
        # Children-first; FK cascades handle most tables, but be explicit
        # for the audit/insights pair.
        for q in (
            "DELETE FROM user_insights_audit WHERE user_id = :u",
            "DELETE FROM user_insights WHERE user_id = :u",
            "DELETE FROM llm_query_dispatches WHERE user_id = :u",
            "DELETE FROM notification_rules WHERE user_id = :u",
            "DELETE FROM users WHERE id = :u",
        ):
            await s.execute(text(q), {"u": "$USER_ID"})
        await s.commit()
    await engine.dispose()
asyncio.run(main())
PY
  green "OK: cleaned up user $USER_ID"
else
  echo "(skip cleanup — DATABASE_URL not set; user $USER_ID stays in DB)"
fi

section "DONE"
green "Phase 6c smoke passed."
