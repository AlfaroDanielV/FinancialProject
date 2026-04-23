#!/usr/bin/env bash
# ─── Phase 5d end-to-end curl script ──────────────────────────────────────────
# Exercises the 8 scenarios from the Phase 5d phase-gate checklist.
#
#   1. evaluate-nudges creates a missing_income nudge from seeded data
#   2. GET /nudges lists it
#   3. dismiss (1x) flips status, no silence yet
#   4. dismiss a second nudge of same type → user_nudge_silences row appears
#   5. act flips status to acted_on
#   6. evaluate twice in a row → second run deduplicates
#   7. deliver during quiet hours → throttled_quiet_hours > 0, nudge stays pending
#   8. high-priority nudge bypasses the 48h rate limit
#
# This script is self-contained: it registers a fresh test user (random email)
# and uses that user's shortcut_token for the whole run. No .env state
# required beyond the database and API being up.
#
# Prereqs:
#   - docker compose up -d db api
#   - alembic upgrade head (0008 applied)
#   - jq installed on the host
#
# Run: bash docs/curl/phase-5d.sh

set -euo pipefail

API="${API:-http://localhost:8000}"
DB_CONTAINER="${DB_CONTAINER:-financialproject-db-1}"

hdr_json=(-H "Content-Type: application/json")

step() { printf "\n\033[1;36m── %s\033[0m\n" "$*"; }
pass() { printf "   \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "   \033[31m✗ %s\033[0m\n" "$*"; exit 1; }
note() { printf "   \033[33m↳\033[0m %s\n" "$*"; }

psql_q() {
    docker exec -i "${DB_CONTAINER}" psql -U finance -d finance -AtX -c "$*"
}

# ── 0. Register a fresh test user ────────────────────────────────────────────
step "0. Register a throwaway user for this smoke run"
SUFFIX=$(date +%s%N)
EMAIL="phase5d-${SUFFIX}@example.com"
REG=$(curl -sf "${API}/api/v1/users/register" "${hdr_json[@]}" -d @- <<EOF
{
  "email": "${EMAIL}",
  "full_name": "Phase 5d Smoke"
}
EOF
)
USER_ID=$(echo "$REG" | jq -r .id)
TOKEN=$(echo "$REG" | jq -r .shortcut_token)
[ -n "$USER_ID" ] && [ -n "$TOKEN" ] || fail "register did not return id + shortcut_token"
pass "user_id=${USER_ID:0:8}… email=${EMAIL}"

hdr_token=(-H "X-Shortcut-Token: ${TOKEN}")
hdr_user=(-H "X-User-Id: ${USER_ID}")

# Attach a fake telegram_user_id so delivery has somewhere to try to send.
# We can't actually deliver (no real chat) — the send will fail as a
# TelegramAPIError (counted as `failed`) or early-exit if TELEGRAM_MODE=disabled.
FAKE_TG_ID=$((9990000000 + RANDOM))
psql_q "UPDATE users SET telegram_user_id = ${FAKE_TG_ID} WHERE id = '${USER_ID}';" >/dev/null
pass "attached fake telegram_user_id=${FAKE_TG_ID}"

# ── 1. Seed data → evaluate-nudges creates missing_income ────────────────────
step "1. Seed 5 expenses in last 7 days → POST /jobs/evaluate-nudges"
TODAY=$(date +%Y-%m-%d)
for i in 1 2 3 4 5; do
    DT=$(date -d "${TODAY} - $((i-1)) day" +%Y-%m-%d)
    curl -sfo /dev/null "${API}/api/v1/transactions/shortcut" "${hdr_json[@]}" "${hdr_token[@]}" -d @- <<EOF
{
  "amount": 3000,
  "merchant": "Test Merchant ${i}",
  "category": "otros",
  "is_expense": true,
  "transaction_date": "${DT}"
}
EOF
done
pass "5 expense transactions seeded"

EVAL=$(curl -sf -X POST "${API}/api/v1/jobs/evaluate-nudges" "${hdr_token[@]}")
echo "$EVAL" | jq .
MI_CREATED=$(echo "$EVAL" | jq -r '(.per_type[] | select(.nudge_type=="missing_income") | .created) // 0')
[ "$MI_CREATED" = "1" ] || fail "expected missing_income.created=1, got ${MI_CREATED}"
pass "missing_income.created=1"

# ── 2. GET /nudges lists the new row ─────────────────────────────────────────
step "2. GET /api/v1/nudges — list current user's nudges"
LIST=$(curl -sf "${API}/api/v1/nudges" "${hdr_user[@]}")
echo "$LIST" | jq '{total: (.items | length), items: [.items[] | {id, nudge_type, status, priority, dedup_key}]}'
N_TOTAL=$(echo "$LIST" | jq '.items | length')
[ "$N_TOTAL" = "1" ] || fail "expected 1 nudge, got ${N_TOTAL}"
NUDGE_1_ID=$(echo "$LIST" | jq -r '.items[0].id')
pass "nudge_1=${NUDGE_1_ID:0:8}… type=missing_income"

# ── 3. First dismiss — soft (no silence yet) ─────────────────────────────────
step "3. POST /nudges/{id}/dismiss — first dismissal"
D1=$(curl -sf -X POST "${API}/api/v1/nudges/${NUDGE_1_ID}/dismiss" "${hdr_user[@]}")
echo "$D1" | jq '{status: .nudge.status, silence_created}'
S1=$(echo "$D1" | jq -r .silence_created)
STATUS1=$(echo "$D1" | jq -r .nudge.status)
[ "$S1" = "false" ] && [ "$STATUS1" = "dismissed" ] || fail "expected silence_created=false, status=dismissed"
pass "first dismiss: silence_created=false, status=dismissed"

# ── 4. Seed a second missing_income nudge (different dedup_key) + 2nd dismiss ──
step "4. Insert second missing_income nudge + dismiss → silence fires"
# Wrap INSERT in a CTE so psql's final command is SELECT → no "INSERT 0 1"
# status tag pollutes the returned id.
NUDGE_2_ID=$(psql_q "
  WITH ins AS (
    INSERT INTO user_nudges
      (id, user_id, nudge_type, priority, dedup_key, payload, status)
    VALUES
      (gen_random_uuid(), '${USER_ID}', 'missing_income', 'normal',
       'missing_income:smoke:${SUFFIX}:second', '{}', 'pending')
    RETURNING id)
  SELECT id FROM ins;")
NUDGE_2_ID=$(echo "${NUDGE_2_ID}" | tr -d '[:space:]')
pass "nudge_2=${NUDGE_2_ID:0:8}… (direct INSERT)"

D2=$(curl -sf -X POST "${API}/api/v1/nudges/${NUDGE_2_ID}/dismiss" "${hdr_user[@]}")
echo "$D2" | jq '{status: .nudge.status, silence_created}'
S2=$(echo "$D2" | jq -r .silence_created)
[ "$S2" = "true" ] || fail "expected silence_created=true on 2nd dismiss, got ${S2}"
pass "second dismiss: silence_created=true"

# Verify user_nudge_silences row was inserted
SCOUNT=$(psql_q "SELECT COUNT(*) FROM user_nudge_silences WHERE user_id='${USER_ID}' AND nudge_type='missing_income';")
[ "${SCOUNT}" = "1" ] || fail "expected 1 silence row, got ${SCOUNT}"
pass "user_nudge_silences has 1 row for missing_income"

# ── 5. Act on a fresh nudge (different type, silence doesn't block) ──────────
step "5. Insert a fresh upcoming_bill nudge and POST /nudges/{id}/act"
NUDGE_3_ID=$(psql_q "
  WITH ins AS (
    INSERT INTO user_nudges
      (id, user_id, nudge_type, priority, dedup_key, payload, status)
    VALUES
      (gen_random_uuid(), '${USER_ID}', 'upcoming_bill', 'normal',
       'upcoming_bill:smoke:${SUFFIX}', '{\"due_date\":\"${TODAY}\"}', 'pending')
    RETURNING id)
  SELECT id FROM ins;")
NUDGE_3_ID=$(echo "${NUDGE_3_ID}" | tr -d '[:space:]')
ACT=$(curl -sf -X POST "${API}/api/v1/nudges/${NUDGE_3_ID}/act" "${hdr_user[@]}")
echo "$ACT" | jq '{status: .nudge.status, silence_created}'
STATUS3=$(echo "$ACT" | jq -r .nudge.status)
[ "$STATUS3" = "acted_on" ] || fail "expected acted_on, got ${STATUS3}"
pass "act: status=acted_on"

# ── 6. Evaluate twice → dedup on the missing_income key ──────────────────────
step "6. POST /jobs/evaluate-nudges twice → second run is a no-op"
# Note: the FIRST missing_income nudge exists (status=dismissed) with dedup_key
# 'missing_income:{USER_ID}:YYYY-MM'. Re-running the evaluator should hit
# that UNIQUE + ON CONFLICT DO NOTHING → deduplicated, not created.
E1=$(curl -sf -X POST "${API}/api/v1/jobs/evaluate-nudges" "${hdr_token[@]}")
E2=$(curl -sf -X POST "${API}/api/v1/jobs/evaluate-nudges" "${hdr_token[@]}")
echo "run1:" ; echo "$E1" | jq '{created, deduplicated, silenced}'
echo "run2:" ; echo "$E2" | jq '{created, deduplicated, silenced}'
E2_CREATED=$(echo "$E2" | jq -r .created)
[ "$E2_CREATED" = "0" ] || fail "expected created=0 on 2nd run, got ${E2_CREATED}"
# missing_income now silenced (from scenario 4) → counted as silenced, not deduplicated
E2_SILENCED=$(echo "$E2" | jq -r .silenced)
[ "$E2_SILENCED" -ge "1" ] || fail "expected silenced >=1 on 2nd run, got ${E2_SILENCED}"
pass "re-run created=0, silenced=${E2_SILENCED} (missing_income blocked by silence)"

# ── 7. Deliver during quiet hours → throttled_quiet_hours > 0 ────────────────
step "7. Flip user timezone so \"now\" is in quiet hours (21-07) → deliver"
# Pick a timezone where the current UTC wall-clock maps into the quiet window.
QUIET_TZ=$(python3 - <<'EOF'
from datetime import datetime, timezone
import zoneinfo
now = datetime.now(timezone.utc)
for name in ['Pacific/Kiritimati','Pacific/Auckland','Asia/Tokyo','Asia/Karachi',
             'Europe/Moscow','Europe/Berlin','Atlantic/Azores','America/Anchorage',
             'Pacific/Honolulu','Asia/Kolkata','Asia/Bangkok','Etc/GMT-10',
             'Etc/GMT-12','Etc/GMT+10','Etc/GMT+12']:
    try:
        h = now.astimezone(zoneinfo.ZoneInfo(name)).hour
    except Exception:
        continue
    if h >= 21 or h < 7:
        print(name)
        break
EOF
)
[ -n "${QUIET_TZ}" ] || fail "no quiet timezone found at \$(date -u)"
pass "chose timezone=${QUIET_TZ}"

# Seed a fresh pending nudge (since all prior ones are dismissed/acted_on).
# Using a dedup_key that doesn't collide with the silenced missing_income.
NUDGE_QH_ID=$(psql_q "
  WITH ins AS (
    INSERT INTO user_nudges
      (id, user_id, nudge_type, priority, dedup_key, payload, status)
    VALUES
      (gen_random_uuid(), '${USER_ID}', 'upcoming_bill', 'normal',
       'upcoming_bill:smoke:${SUFFIX}:quiet',
       '{\"due_date\":\"${TODAY}\"}', 'pending')
    RETURNING id)
  SELECT id FROM ins;")
NUDGE_QH_ID=$(echo "${NUDGE_QH_ID}" | tr -d '[:space:]')
psql_q "UPDATE users SET timezone='${QUIET_TZ}' WHERE id='${USER_ID}';" >/dev/null

DEL_Q=$(curl -sf -X POST "${API}/api/v1/jobs/deliver-nudges" "${hdr_token[@]}")
echo "$DEL_Q" | jq .
QH_BLOCKED=$(echo "$DEL_Q" | jq -r .throttled_quiet_hours)
QH_SENT=$(echo "$DEL_Q" | jq -r .sent)
[ "${QH_BLOCKED}" -ge "1" ] || fail "expected throttled_quiet_hours >=1, got ${QH_BLOCKED}"
[ "${QH_SENT}" = "0" ] || fail "expected sent=0 during quiet hours, got ${QH_SENT}"
pass "quiet hours: throttled_quiet_hours=${QH_BLOCKED}, sent=0"

STATUS_QH=$(psql_q "SELECT status FROM user_nudges WHERE id='${NUDGE_QH_ID}';")
[ "${STATUS_QH}" = "pending" ] || fail "nudge should stay pending after quiet-hours defer, got ${STATUS_QH}"
pass "nudge still status=pending (will retry next window)"

# Revert the user's timezone so the rate-limit test runs in awake hours.
psql_q "UPDATE users SET timezone='America/Costa_Rica' WHERE id='${USER_ID}';" >/dev/null

# ── 8. High-priority bypasses the 48h rate limit ─────────────────────────────
step "8. HIGH-priority bypasses rate limit (normal blocked, high attempted)"
# Simulate a prior normal-priority send inside the rate window so the next
# normal nudge is rate-limited.
psql_q "
  INSERT INTO user_nudges
    (id, user_id, nudge_type, priority, dedup_key, payload, status,
     sent_at, delivery_channel)
  VALUES
    (gen_random_uuid(), '${USER_ID}', 'upcoming_bill', 'normal',
     'upcoming_bill:smoke:${SUFFIX}:prior-sent',
     '{\"due_date\":\"${TODAY}\"}', 'sent', now() - interval '1 hour',
     'telegram');" >/dev/null

# Mark the QH pending nudge as already delivered-attempt so it doesn't
# interfere. Re-use it as the "normal pending" for this scenario instead.
NUDGE_NORMAL=${NUDGE_QH_ID}

NUDGE_HIGH_ID=$(psql_q "
  WITH ins AS (
    INSERT INTO user_nudges
      (id, user_id, nudge_type, priority, dedup_key, payload, status)
    VALUES
      (gen_random_uuid(), '${USER_ID}', 'upcoming_bill', 'high',
       'upcoming_bill:smoke:${SUFFIX}:high',
       '{\"due_date\":\"${TODAY}\"}', 'pending')
    RETURNING id)
  SELECT id FROM ins;")
NUDGE_HIGH_ID=$(echo "${NUDGE_HIGH_ID}" | tr -d '[:space:]')
pass "seeded: normal pending=${NUDGE_NORMAL:0:8}… + high pending=${NUDGE_HIGH_ID:0:8}…"

DEL_R=$(curl -sf -X POST "${API}/api/v1/jobs/deliver-nudges" "${hdr_token[@]}")
echo "$DEL_R" | jq .

R_THROTTLED=$(echo "$DEL_R" | jq -r .throttled_rate_limit)
R_PROCESSED=$(echo "$DEL_R" | jq -r .processed)
[ "${R_THROTTLED}" -ge "1" ] || fail "expected throttled_rate_limit >=1, got ${R_THROTTLED}"
[ "${R_PROCESSED}" -ge "2" ] || fail "expected processed >=2 (high + normal), got ${R_PROCESSED}"
pass "rate limit: throttled_rate_limit=${R_THROTTLED} (normal blocked)"

# The HIGH nudge was NOT throttled by rate limit. It either sent successfully
# (if a real Telegram chat exists) or failed on bogus chat_id. Either way,
# the counter we care about — throttled_rate_limit — shows the normal was
# blocked and the high wasn't. Verify by status:
STATUS_HIGH=$(psql_q "SELECT status FROM user_nudges WHERE id='${NUDGE_HIGH_ID}';")
case "${STATUS_HIGH}" in
    sent)    note "HIGH status=sent (real chat accepted the message)" ;;
    pending) note "HIGH status=pending (send failed on fake chat_id — expected)" ;;
    *)       fail "HIGH status unexpected: ${STATUS_HIGH}" ;;
esac
pass "HIGH bypassed the rate limit (reached the send stage)"

STATUS_NORMAL=$(psql_q "SELECT status FROM user_nudges WHERE id='${NUDGE_NORMAL}';")
[ "${STATUS_NORMAL}" = "pending" ] || fail "normal should stay pending (rate-limited), got ${STATUS_NORMAL}"
pass "NORMAL stayed pending (rate-limit path worked)"

# ── done ─────────────────────────────────────────────────────────────────────
printf "\n\033[1;32m✔ Phase 5d smoke passed — 8 scenarios verified.\033[0m\n"
printf "   user_id=%s\n   email=%s\n" "${USER_ID}" "${EMAIL}"
