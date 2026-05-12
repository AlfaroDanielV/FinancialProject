# Phase 6d B12 Test Guide

Status: READY_FOR_PRODUCTION_DOGFOODING
Owner: Daniel
Date: 2026-05-12

This guide is the step-by-step checklist for testing B12 in production. The
retrospective and friction log live in `docs/phase-6d-retrospective.md`.

B12 only passes when Daniel completes the real self-onboarding flow in
production without manual DB inserts, seed scripts, or private shortcuts.

---

## 1. Local Build Baseline

Run the SPA production build before testing production, so the frontend source
has at least one clean local build.

```bash
cd web
npm run build
```

Expected result from the current checkout:

```text
tsc -b && vite build
vite v5.4.21 building for production...
✓ 110 modules transformed.
dist/index.html                  0.46 kB │ gzip:   0.31 kB
dist/assets/index-*.css         11.31 kB │ gzip:   2.73 kB
dist/assets/index-*.js         354.35 kB │ gzip: 103.78 kB
✓ built in about 2s
```

The local shell may print read-only `envman` warnings before the build. Those
warnings are not a frontend build failure if Vite finishes successfully.

---

## 2. Focused Automated Checks

Run the B11 full-flow test before production dogfooding:

```bash
env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 \
  uv run pytest -p no:cacheprovider -q tests/test_phase_6d_b11_e2e.py
```

For the broader Phase 6d onboarding suite, run:

```bash
env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 \
  uv run pytest -p no:cacheprovider -q \
  tests/test_phase_6d_b2_endpoints.py \
  tests/test_phase_6d_b3_magic_link.py \
  tests/test_phase_6d_b6_debts.py \
  tests/test_phase_6d_b7_recurring_bills.py \
  tests/test_phase_6d_b8_lazy_detection.py \
  tests/test_phase_6d_b9_account_creation.py \
  tests/test_phase_6d_b10_welcome.py \
  tests/test_phase_6d_b11_e2e.py
```

Do not close B12 from automated tests alone. They are the baseline; the gate is
the real production run.

---

## 3. Production Preflight

Confirm these before opening Telegram:

- Latest B1-B11 backend and SPA builds are deployed.
- Production API health is green.
- Production Alembic migration is at head.
- Telegram bot is using the production webhook.
- `SPA_BASE_URL` points to the production SPA.
- `SPA_CORS_ORIGINS` includes the production SPA origin.
- `SESSION_COOKIE_SECURE=true` in production.
- `SESSION_COOKIE_DOMAIN` matches the shared API/SPA parent domain if a parent
  domain is configured.
- No production secret is pasted into this file or the retrospective.

Health smoke:

```bash
curl -fsS "$API_BASE_URL/health/ready"
```

---

## 4. Choose The Production User

Use one of these paths. Record the choice in
`docs/phase-6d-retrospective.md`.

### Option A: Clean test user

This is allowed because it uses the real public registration flow, not a DB
seed.

```bash
curl -fsS -X POST "$API_BASE_URL/api/v1/users/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "daniel+phase6d-dogfood@example.com",
    "full_name": "Daniel Phase 6d",
    "country": "CR",
    "timezone": "America/Costa_Rica",
    "currency": "CRC",
    "locale": "es-CR"
  }'
```

Save the returned `shortcut_token` securely. It is authentication material.

Then request a Telegram pairing code:

```bash
curl -fsS -X POST "$API_BASE_URL/api/v1/users/me/telegram/pairing-code" \
  -H "X-Shortcut-Token: $SHORTCUT_TOKEN"
```

In Telegram, send:

```text
/start <pairing-code>
```

### Option B: Daniel real user

Use this only if Daniel chooses to dogfood against his real production user.
Do not manually insert, delete, or seed onboarding rows. If the user needs a
reset, pause and document the reset decision before touching data.

---

## 5. Hybrid Path: Telegram To SPA To Telegram

### Step 1: Start in Telegram

Send:

```text
/start
```

Expected:

- Bot detects an empty or partial onboarding state.
- Message offers setup web and chat fallback.
- Button opens the setup SPA.
- Copy is natural CR Spanish and avoids generic AI language.

Record timestamp, device, browser context, and any friction in the
retrospective.

### Step 2: Open the magic link

Expected:

- SPA opens from the Telegram button.
- Link is consumed once.
- Landing loads after `/api/v1/auth/magic-link/exchange`.
- If the link is expired or already used, the recovery path says to return to
  Telegram and send `/setup`.

### Step 3: Create an account

In the SPA, create:

- Name: `BAC`
- Type: savings
- Currency: CRC
- Initial balance: 0

Expected:

- Form validation is specific.
- Submit succeeds.
- Landing/status marks accounts complete.

### Step 4: Create incomes

Create a base salary:

- Name: `Salario`
- Type: salary
- Currency: CRC
- Amount: any realistic test amount
- Frequency: monthly
- Next payment date: a future payroll date

Then create aguinaldo linked to that salary:

- Name: `Aguinaldo`
- Type: aguinaldo
- Frequency: annual
- Base salary: `Salario`
- Next payment date: December payroll date

Expected:

- Aguinaldo hides manual amount.
- Salary linking is clear.
- The derived amount is understandable.

### Step 5: Create a debt

Create a mortgage, personal loan, or realistic test debt:

- Name: `Hipoteca BAC`
- Creditor: `BAC`
- Principal/current balance
- Annual interest rate shown as percent in the UI
- Term in months or years, depending on the UI control
- Payment day
- Start date
- Currency
- Optional linked account

Expected:

- Live monthly payment preview appears.
- Percent-to-fraction conversion is clear.
- Payment-day warnings are clear when applicable.
- Submit succeeds.

### Step 6: Create a recurring bill

Create at least one fixed bill:

- Provider: `ICE`
- Category: servicios
- Frequency: monthly
- Amount
- Next/start date
- Optional linked account: `BAC`

Expected:

- Provider/category defaults reduce typing.
- Submit succeeds.
- Landing/status reaches complete after all four entity families exist.

### Step 7: Return to Telegram

Send:

```text
gasté 5000 en el super con la BAC
```

Expected:

- Bot associates the transaction proposal with the existing `BAC` account.
- Lazy detection does not ask to create `BAC` again.
- Confirmation copy is clear.

---

## 6. Lazy-Only Path

Use a second account hint that does not exist yet:

```text
gasté 5000 con la BCR
```

Expected:

- Bot says it does not have a `BCR` account.
- Bot offers creating it in chat or opening the SPA.

Reply:

```text
crear
```

Then answer the mini-flow:

```text
ahorros
CRC
0
sí
```

Expected:

- Account name is prefilled from `BCR`.
- Flow stays under the 4-turn target after `crear`.
- Invalid answers are corrected without advancing state.
- Account is created.
- Original transaction proposal resumes against the new account.

Also test cancellation in a fresh run:

```text
/cancel
```

Expected:

- Redis state is cleared.
- Bot explains that the account creation was cancelled.
- User can restart with `/setup` or another transaction message.

---

## 7. Magic-Link Recovery

After using the first setup link, send:

```text
/setup
```

Expected:

- Bot returns a new magic link.
- The old used link remains invalid.
- The new link opens the SPA.

If a link expires during manual testing, do not bypass it. Send `/setup` and
record whether recovery was clear.

---

## 8. Optional Production Status Check

After the hybrid path, this endpoint should show complete onboarding for the
test user:

```bash
curl -fsS "$API_BASE_URL/api/v1/onboarding/status" \
  -H "X-Shortcut-Token: $SHORTCUT_TOKEN"
```

Expected shape:

```json
{
  "has_accounts": true,
  "has_incomes": true,
  "has_debts": true,
  "has_recurring_bills": true,
  "completeness_score": 1.0
}
```

Counts may vary depending on how many items were created.

---

## 9. Evidence To Capture

Fill `docs/phase-6d-retrospective.md` during the run:

- Production user choice.
- Telegram device and browser context.
- SPA production URL and API production URL.
- At least 5 friction rows, unless Daniel explicitly documents why fewer is
  still a serious dogfood run.
- Screenshot or concrete evidence for every blocker, major issue, or confusing
  recovery state.
- Patch or backlog decision for every friction.

Severity rules:

- blocker: prevents completing self-onboarding.
- major: completion possible, but a beta user would likely fail or abandon.
- minor: confusing or slow, but recoverable.
- nit: polish issue.

---

## 10. Pass / Fail Criteria

B12 passes only if:

- Accounts, incomes, debts, and recurring bills were created in production
  through the real flow.
- The Telegram to SPA to Telegram path works.
- The lazy-only account creation path works.
- `/setup` recovery works.
- At least 5 frictions are documented or the exception is explicitly justified.
- No blocker remains open.
- Daniel approves closing B12 in the retrospective.

Stop and do not move to B13 if:

- Magic-link auth fails in production.
- SPA cannot create any required entity family.
- Lazy-only account creation cannot resume the original transaction.
- There is a blocker without an agreed fix or escalation.

---

## 11. After The Run

Update:

- `docs/phase-6d-retrospective.md`
- `CLAUDE.md`
- `docs/phase-6d-decisions.md`
- Obsidian context files requested by Daniel, if B12 status changes

Do not delete a clean test user during the run. Decide cleanup separately after
B12 is accepted.
