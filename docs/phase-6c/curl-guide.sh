#!/usr/bin/env bash
# Phase 6c — curl walking tour
# ----------------------------------------------------------------------
# This is the closing-gate script for Phase 6c. Daniel runs it
# end-to-end against PRODUCTION after the 7-day shadow window per
# docs/phase-6c/shadow-validation.md, and confirms every section is
# green by visual inspection.
#
# Differences from scripts/test_phase_6c.sh:
#   - No assertions. Pretty-prints each response so the operator can
#     read it and decide.
#   - Includes a memory-aware question against /api/v1/queries/test to
#     demonstrate the dispatcher actually using get_user_context once
#     INSIGHTS_DISPATCHER_ENABLED=true. (The tool call shows up in the
#     `tools_used` array.)
#   - Cleans up the throw-away user at the end.
#
# Pre-requisites:
#   - API + bot deployed; flag flipped to INSIGHTS_DISPATCHER_ENABLED=true
#     in Container Apps env-config.
#   - INSIGHTS_EXTRACTOR_ENABLED=true so the extractor populated some
#     stated_* insights during the shadow window.
#   - jq + python3 + curl on PATH.
#   - (For cleanup) DATABASE_URL set to the prod connection string
#     OR run the cleanup block manually.
#
# Run with:
#   API_BASE=https://<your-api-host> ./docs/phase-6c/curl-guide.sh

set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
DATABASE_URL="${DATABASE_URL:-}"

bold() { printf '\033[1;36m%s\033[0m\n' "$*"; }
note() { printf '\033[2;37m  %s\033[0m\n' "$*"; }
green() { printf '\033[32m  ✔ %s\033[0m\n' "$*"; }
section() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }


# ── 1. Register a throw-away user ──────────────────────────────────────────
section "1. Register a throw-away user"

EMAIL="phase6c-tour-$(python3 -c 'import secrets; print(secrets.token_hex(6))')@test.example"
note "Calling POST /api/v1/users/register with email=$EMAIL"

REG=$(curl -fsS -X POST "$API_BASE/api/v1/users/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"full_name\":\"Phase 6c Tour\"}")

USER_ID=$(echo "$REG" | jq -r '.id')
TOKEN=$(echo "$REG" | jq -r '.shortcut_token')
AUTH_HEADER="X-Shortcut-Token: $TOKEN"
echo "$REG" | jq '{id, email, full_name}'
green "user $USER_ID registered"


# ── 2. Trigger the per-user nightly recompute ─────────────────────────────
section "2. POST /api/v1/admin/insights/run-nightly"

note "This runs compute_all + persist + gap detection for the caller."
note "On a brand-new user with no transactions, you'll see 0 created."

curl -fsS -X POST "$API_BASE/api/v1/admin/insights/run-nightly" \
    -H "$AUTH_HEADER" | jq


# ── 3. Export the user's full memory ──────────────────────────────────────
section "3. GET /api/v1/users/me/insights/export"

note "JSON dump. Includes active + expired-not-purged. Streams over 2000 rows."

EXPORT=$(curl -fsS "$API_BASE/api/v1/users/me/insights/export" \
    -H "$AUTH_HEADER")

# Pretty-print the envelope without the (potentially long) insights array.
echo "$EXPORT" | jq '{user_id, exported_at, format_version, request_id, include_expired, count}'
INSIGHT_COUNT=$(echo "$EXPORT" | jq -r '.count')
note "($INSIGHT_COUNT insights in the body)"


# ── 4. Memory-aware question against the query dispatcher ────────────────
section "4. POST /api/v1/queries/test (memory-aware question)"

note "When INSIGHTS_DISPATCHER_ENABLED=true, the response's tools_used"
note "should contain get_user_context for personalized questions."
note ""
note "We send a comparative question. On a brand-new user with no"
note "memory, the dispatcher may answer without calling the tool —"
note "that's fine for this tour. The point is to verify the tool"
note "is REGISTERED and the prompt mentions it."

QRESP=$(curl -fsS -X POST "$API_BASE/api/v1/queries/test" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\": 0, \"query\": \"qué pensás de mis gastos vs mis metas\"}" \
    || echo '{"reply":"(query failed; see logs)","tools_used":[]}')

echo "$QRESP" | jq '{reply, dispatch_id, iterations, tools_used: (.tools_used | map(.name)), tokens}'


# ── 5. Privacy: delete everything ──────────────────────────────────────────
section "5. DELETE /api/v1/users/me/insights"

note "Hard-delete every insight for this user. Returns the count."

curl -fsS -X DELETE "$API_BASE/api/v1/users/me/insights" \
    -H "$AUTH_HEADER" | jq


# ── 6. Re-export to confirm zero state ────────────────────────────────────
section "6. Re-export — should show count=0"

curl -fsS "$API_BASE/api/v1/users/me/insights/export" \
    -H "$AUTH_HEADER" | jq '{count, request_id}'


# ── 7. Bot commands (documentation only) ──────────────────────────────────
section "7. Bot commands (try these in Telegram, not curl)"

cat <<'EOF'

  /memoria              List active insights grouped by category.
  /olvidar              Menu to delete by group or all.
  /olvidar todo         Two-confirmation flow to wipe everything.
  /editar_memoria       Pick an editable insight, reply with a
                        correction. Haiku parses; row is persisted
                        with user_locked=true (sacred).
  /recalcular_memoria   Trigger this user's nightly recompute now.
                        1/hour cooldown.

  Each tap or message produces a typed audit row in
  user_insights_audit. Tail it with:

    SELECT action, actor, payload->>'insight_type', created_at
    FROM user_insights_audit
    WHERE user_id = '<daniel-user-id>'
    ORDER BY created_at DESC LIMIT 30;

EOF


# ── 8. Cleanup ────────────────────────────────────────────────────────────
section "8. Cleanup"

if [ -n "$DATABASE_URL" ]; then
  note "Removing throw-away user $USER_ID via DATABASE_URL..."
  python3 - <<PY
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async def main():
    engine = create_async_engine("$DATABASE_URL")
    async with AsyncSession(engine) as s:
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
  green "user $USER_ID removed"
else
  note "DATABASE_URL not set — leaving user $USER_ID in the database."
  note "To remove later:"
  cat <<EOF
    DELETE FROM user_insights_audit  WHERE user_id = '$USER_ID';
    DELETE FROM user_insights        WHERE user_id = '$USER_ID';
    DELETE FROM llm_query_dispatches WHERE user_id = '$USER_ID';
    DELETE FROM notification_rules   WHERE user_id = '$USER_ID';
    DELETE FROM users                WHERE id      = '$USER_ID';
EOF
fi


bold ""
bold "Phase 6c walking tour complete."
bold "If every section above looked right, the phase is closable."
bold "Confirm INSIGHTS_DISPATCHER_ENABLED=true is set in Azure"
bold "Container Apps env-config and you're done with 6c. 🎉"
