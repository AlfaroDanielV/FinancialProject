#!/usr/bin/env bash
# ─── Phase 5b smoke test ──────────────────────────────────────────────────────
# Exercises the Telegram pipeline via POST /api/v1/telegram/_simulate. This
# endpoint is rejected outside ENV=development.
#
# We do NOT hit the live Anthropic API here. Each test uses `mock_extraction`
# to feed a pre-baked ExtractionResult into the pipeline so the run is
# deterministic, zero-cost, and doesn't require ANTHROPIC_API_KEY. The
# actual LLM path is covered by tests/test_llm_extractor.py fixtures.
#
# What's covered:
#   1. Register a user + get shortcut token
#   2. Issue a pairing code
#   3. /simulate with pairing_code → telegram_user_id gets bound
#   4. Inject an expense extraction → proposal + Sí/No/Editar buttons returned
#   5. Send "sí" → transaction committed, undo hint shown
#   6. /undo → transaction reversed
#   7. Inject a query_balance extraction → summary returned
#   8. Garbage text via mock with intent=unknown → help text returned
#
# Prereqs:
#   - docker compose up -d db api redis
#   - alembic upgrade head (migrations 0006 + 0007 applied)
#   - ENVIRONMENT=development in .env
#   - jq installed
#
# Run: bash scripts/phase5b_smoke.sh
#
# Optional env: API (default http://localhost:8000)

set -euo pipefail

API="${API:-http://localhost:8000}"

if [ -f "$(dirname "$0")/../.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$(dirname "$0")/../.env"
    set +a
fi

hdr_json=(-H "Content-Type: application/json")

step() { printf "\n\033[1;36m── %s\033[0m\n" "$*"; }
pass() { printf "   \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "   \033[31m✗ %s\033[0m\n" "$*"; exit 1; }

SUFFIX=$(date +%s)
EMAIL="phase5b+${SUFFIX}@example.com"
TG_ID=$((RANDOM + 100000000))

# ── 1. Register user ─────────────────────────────────────────────────────────
step "1. Register user"
REG=$(curl -sf "${API}/api/v1/users/register" "${hdr_json[@]}" -d @- <<EOF
{
  "email": "${EMAIL}",
  "full_name": "Phase 5b",
  "country": "CR",
  "timezone": "America/Costa_Rica",
  "currency": "CRC",
  "locale": "es-CR"
}
EOF
)
USER_ID=$(echo "$REG" | jq -r .id)
TOKEN=$(echo "$REG" | jq -r .shortcut_token)
pass "user_id=${USER_ID}"

# ── 2. Issue pairing code ────────────────────────────────────────────────────
step "2. POST /users/me/telegram/pairing-code"
CODE_RESP=$(curl -sf -X POST "${API}/api/v1/users/me/telegram/pairing-code" \
    -H "X-Shortcut-Token: ${TOKEN}")
CODE=$(echo "$CODE_RESP" | jq -r .code)
[ -n "$CODE" ] && [ ${#CODE} -eq 6 ] || fail "code length wrong: '${CODE}'"
pass "code=${CODE}"

# ── 3. Pair via _simulate ────────────────────────────────────────────────────
step "3. _simulate with pairing_code"
PAIR=$(curl -sf -X POST "${API}/api/v1/telegram/_simulate" "${hdr_json[@]}" -d @- <<EOF
{
  "telegram_user_id": ${TG_ID},
  "text": "/start ${CODE}",
  "first_name": "Dani",
  "pairing_code": "${CODE}"
}
EOF
)
echo "$PAIR" | jq -r .text | sed 's/^/   /'
echo "$PAIR" | jq -r .text | grep -qi "listo" || fail "pairing reply did not confirm"
pass "paired"

# ── 4. Inject expense extraction → expect proposal ──────────────────────────
step "4. _simulate expense (mock_extraction)"
PROPOSE=$(curl -sf -X POST "${API}/api/v1/telegram/_simulate" "${hdr_json[@]}" -d @- <<EOF
{
  "telegram_user_id": ${TG_ID},
  "text": "gasté 5000 en el super",
  "mock_extraction": {
    "intent": "log_expense",
    "amount": 5000,
    "currency": "CRC",
    "merchant": "Supermercado",
    "category_hint": "supermercado",
    "account_hint": null,
    "occurred_at_hint": null,
    "query_window": null,
    "confidence": 0.92,
    "raw_notes": null
  }
}
EOF
)
BTN_COUNT=$(echo "$PROPOSE" | jq '.buttons | length')
[ "$BTN_COUNT" = "3" ] || fail "expected 3 buttons, got ${BTN_COUNT}"
echo "$PROPOSE" | jq -r .text | sed 's/^/   /'
echo "$PROPOSE" | jq -r .text | grep -qi "confirmo" || fail "proposal missing '¿Confirmo?'"
pass "proposal returned with confirmation buttons"

# ── 5. Confirm with "sí" → commit ───────────────────────────────────────────
step '5. _simulate "sí" → commit'
CONFIRM=$(curl -sf -X POST "${API}/api/v1/telegram/_simulate" "${hdr_json[@]}" -d @- <<EOF
{
  "telegram_user_id": ${TG_ID},
  "text": "sí"
}
EOF
)
echo "$CONFIRM" | jq -r .text | sed 's/^/   /'
echo "$CONFIRM" | jq -r .text | grep -qi "guardado" || fail "commit reply missing 'Guardado'"
pass "committed"

# Sanity: transaction exists in DB via REST
TXNS=$(curl -sf "${API}/api/v1/transactions?limit=5" \
    -H "X-Shortcut-Token: ${TOKEN}")
LATEST_SOURCE=$(echo "$TXNS" | jq -r '.items[0].source')
LATEST_AMOUNT=$(echo "$TXNS" | jq -r '.items[0].amount')
[ "$LATEST_SOURCE" = "telegram" ] || fail "latest source='${LATEST_SOURCE}', expected 'telegram'"
[ "$LATEST_AMOUNT" = "-5000.00" ] || [ "$LATEST_AMOUNT" = "-5000" ] || \
    fail "latest amount='${LATEST_AMOUNT}', expected -5000"
pass "txn persisted with source=telegram, amount=${LATEST_AMOUNT}"

# ── 6. /undo → reversal ──────────────────────────────────────────────────────
step "6. _simulate /undo → reversal"
UNDO=$(curl -sf -X POST "${API}/api/v1/telegram/_simulate" "${hdr_json[@]}" -d @- <<EOF
{
  "telegram_user_id": ${TG_ID},
  "text": "/undo"
}
EOF
)
echo "$UNDO" | jq -r .text | sed 's/^/   /'
echo "$UNDO" | jq -r .text | grep -qi "deshice" || fail "undo reply did not say 'Deshice'"

POST_UNDO=$(curl -sf "${API}/api/v1/transactions?limit=5" \
    -H "X-Shortcut-Token: ${TOKEN}")
POST_UNDO_TOTAL=$(echo "$POST_UNDO" | jq -r .total)
[ "$POST_UNDO_TOTAL" = "0" ] || fail "expected 0 txns after undo, got ${POST_UNDO_TOTAL}"
pass "reversal complete, 0 txns remain"

# ── 7. Balance query ────────────────────────────────────────────────────────
step "7. _simulate balance query (mock_extraction)"
BAL=$(curl -sf -X POST "${API}/api/v1/telegram/_simulate" "${hdr_json[@]}" -d @- <<EOF
{
  "telegram_user_id": ${TG_ID},
  "text": "cuanto gaste esta semana",
  "mock_extraction": {
    "intent": "query_balance",
    "amount": null,
    "currency": null,
    "merchant": null,
    "category_hint": null,
    "account_hint": null,
    "occurred_at_hint": null,
    "query_window": "this_week",
    "confidence": 0.95,
    "raw_notes": null
  }
}
EOF
)
echo "$BAL" | jq -r .text | sed 's/^/   /'
# After undo there are no transactions → expect the empty-period message.
echo "$BAL" | jq -r .text | grep -qiE "movimientos|resumen" || fail "balance reply format unexpected"
pass "balance query returned"

# ── 8. Garbage → help ───────────────────────────────────────────────────────
step "8. _simulate unknown intent → help reply"
HELP=$(curl -sf -X POST "${API}/api/v1/telegram/_simulate" "${hdr_json[@]}" -d @- <<EOF
{
  "telegram_user_id": ${TG_ID},
  "text": "asdfasdf",
  "mock_extraction": {
    "intent": "unknown",
    "amount": null,
    "currency": null,
    "merchant": null,
    "category_hint": null,
    "account_hint": null,
    "occurred_at_hint": null,
    "query_window": null,
    "confidence": 0.2,
    "raw_notes": null
  }
}
EOF
)
echo "$HELP" | jq -r .text | sed 's/^/   /'
echo "$HELP" | jq -r .text | grep -qi "puedo ayudarte" || fail "help text missing"
pass "help returned"

printf "\n\033[1;32m✓ Phase 5b smoke test passed.\033[0m\n"
