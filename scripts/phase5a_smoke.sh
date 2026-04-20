#!/usr/bin/env bash
# ─── Phase 5a smoke test ──────────────────────────────────────────────────────
# Verifies:
#   1. Register new user, capture shortcut_token
#   2. GET /users/me with token → 200
#   3. GET /users/me with bad token → 401
#   4. POST /transactions/shortcut with new user's token → txn scoped to new user
#   5. Legacy user's pre-existing data is intact and still scoped to them
#      (queried via legacy token if LEGACY_SHORTCUT_TOKEN is set)
#   6. Cross-user read attempt via X-User-Id shim returns only that user's data
#   7. Rotate token → old token 401, new token 200
#
# Prereqs:
#   - docker compose up -d db api
#   - alembic upgrade head  (migration 0006 must have run successfully)
#   - jq installed
#
# Run: bash scripts/phase5a_smoke.sh
#
# Optional env: API (default http://localhost:8000),
#               LEGACY_SHORTCUT_TOKEN (enables step 5).

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

# Use a unique email per run so re-running doesn't 409.
SUFFIX=$(date +%s)
NEW_EMAIL="phase5a+${SUFFIX}@example.com"

# ── 1. Register a new user ───────────────────────────────────────────────────
step "1. POST /users/register"
REG=$(curl -sf "${API}/api/v1/users/register" "${hdr_json[@]}" -d @- <<EOF
{
  "email": "${NEW_EMAIL}",
  "full_name": "Phase 5a Test User",
  "country": "CR",
  "timezone": "America/Costa_Rica",
  "currency": "CRC",
  "locale": "es-CR"
}
EOF
)
NEW_USER_ID=$(echo "$REG" | jq -r .id)
NEW_TOKEN=$(echo "$REG" | jq -r .shortcut_token)
[ -n "$NEW_TOKEN" ] && [ "$NEW_TOKEN" != "null" ] || fail "no token returned"
[ "${#NEW_TOKEN}" -ge 40 ] || fail "token too short (got ${#NEW_TOKEN})"
pass "registered user_id=${NEW_USER_ID}, token len=${#NEW_TOKEN}"

# ── 2. /users/me with the new token ──────────────────────────────────────────
step "2. GET /users/me with new token"
ME=$(curl -sf "${API}/api/v1/users/me" -H "X-Shortcut-Token: ${NEW_TOKEN}")
ME_ID=$(echo "$ME" | jq -r .id)
[ "$ME_ID" = "$NEW_USER_ID" ] || fail "expected ${NEW_USER_ID}, got ${ME_ID}"
pass "/me returns the registered user"

# ── 3. /users/me with a bogus token → 401 ────────────────────────────────────
step "3. GET /users/me with bad token → 401"
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "${API}/api/v1/users/me" -H "X-Shortcut-Token: not-a-real-token-xyz")
[ "$CODE" = "401" ] || fail "expected 401, got ${CODE}"
pass "401 returned"

# ── 4. POST /transactions/shortcut → txn scoped to the new user ──────────────
step "4. POST /transactions/shortcut with new user's token"
SHORT_TXN=$(curl -sf "${API}/api/v1/transactions/shortcut" \
    "${hdr_json[@]}" -H "X-Shortcut-Token: ${NEW_TOKEN}" -d @- <<EOF
{
  "amount": 1234,
  "merchant": "Phase5a Test Merchant",
  "category": "test",
  "is_expense": true
}
EOF
)
SHORT_USER=$(echo "$SHORT_TXN" | jq -r .user_id)
[ "$SHORT_USER" = "$NEW_USER_ID" ] || fail "txn assigned to ${SHORT_USER}, expected ${NEW_USER_ID}"
pass "txn scoped to new user (id=$(echo "$SHORT_TXN" | jq -r .id))"

# Confirm the new user sees exactly that txn (and no legacy data)
NEW_LIST=$(curl -sf "${API}/api/v1/transactions?limit=200" \
    -H "X-Shortcut-Token: ${NEW_TOKEN}")
NEW_TOTAL=$(echo "$NEW_LIST" | jq -r .total)
[ "$NEW_TOTAL" -ge 1 ] || fail "new user total=${NEW_TOTAL}, expected ≥1"
NEW_FOREIGN=$(echo "$NEW_LIST" | jq -r '[.items[] | select(.user_id != "'"${NEW_USER_ID}"'")] | length')
[ "$NEW_FOREIGN" = "0" ] || fail "new user sees ${NEW_FOREIGN} txns belonging to other users"
pass "new user list contains only their own txns"

# ── 5. Legacy user's data is intact and scoped to them ───────────────────────
if [ -n "${LEGACY_SHORTCUT_TOKEN:-}" ]; then
    step "5. Legacy user data still scoped via LEGACY_SHORTCUT_TOKEN"
    LEG_LIST=$(curl -sf "${API}/api/v1/transactions?limit=200" \
        -H "X-Shortcut-Token: ${LEGACY_SHORTCUT_TOKEN}")
    LEG_TOTAL=$(echo "$LEG_LIST" | jq -r .total)
    LEG_USER=$(echo "$LEG_LIST" | jq -r '.items[0].user_id // empty')
    if [ -n "$LEG_USER" ]; then
        [ "$LEG_USER" != "$NEW_USER_ID" ] || fail "legacy and new user collapsed to same id"
        LEG_FOREIGN=$(echo "$LEG_LIST" | jq -r '[.items[] | select(.user_id != "'"${LEG_USER}"'")] | length')
        [ "$LEG_FOREIGN" = "0" ] || fail "legacy user sees foreign txns"
        pass "legacy user sees ${LEG_TOTAL} of their own txns, none from new user"
    else
        pass "legacy user has zero pre-existing txns (nothing to validate)"
    fi
else
    step "5. (skipped — set LEGACY_SHORTCUT_TOKEN to validate legacy scoping)"
fi

# ── 6. X-User-Id dev shim returns only that user's data ──────────────────────
step "6. X-User-Id shim isolates per user"
SHIM_LIST=$(curl -sf "${API}/api/v1/transactions?limit=200" \
    -H "X-User-Id: ${NEW_USER_ID}")
SHIM_FOREIGN=$(echo "$SHIM_LIST" | jq -r '[.items[] | select(.user_id != "'"${NEW_USER_ID}"'")] | length')
[ "$SHIM_FOREIGN" = "0" ] || fail "shim leaks ${SHIM_FOREIGN} foreign txns"
pass "X-User-Id shim returns only user's own data"

# Bogus X-User-Id → 401 (well-formed UUID that doesn't exist)
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "${API}/api/v1/transactions?limit=1" \
    -H "X-User-Id: 00000000-0000-0000-0000-000000000000")
[ "$CODE" = "401" ] || fail "shim with unknown UUID expected 401, got ${CODE}"
pass "unknown X-User-Id → 401"

# ── 7. Rotate token: old → 401, new → 200 ────────────────────────────────────
step "7. Rotate shortcut_token"
ROT=$(curl -sf -X POST "${API}/api/v1/users/me/rotate-shortcut-token" \
    -H "X-Shortcut-Token: ${NEW_TOKEN}")
ROT_TOKEN=$(echo "$ROT" | jq -r .shortcut_token)
[ -n "$ROT_TOKEN" ] && [ "$ROT_TOKEN" != "$NEW_TOKEN" ] || fail "token did not change"

# Old token now invalid
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "${API}/api/v1/users/me" -H "X-Shortcut-Token: ${NEW_TOKEN}")
[ "$CODE" = "401" ] || fail "old token still works (got ${CODE})"
pass "old token → 401"

# New token works
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "${API}/api/v1/users/me" -H "X-Shortcut-Token: ${ROT_TOKEN}")
[ "$CODE" = "200" ] || fail "new token failed (got ${CODE})"
pass "new token → 200"

printf "\n\033[1;32m✓ Phase 5a smoke test passed.\033[0m\n"
