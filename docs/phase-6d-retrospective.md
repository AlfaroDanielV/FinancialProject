# Phase 6d B12 — Daniel self-onboarding retrospective

STATUS: CLOSED_BY_OPERATOR_OVERRIDE
Opened: 2026-05-12
Owner: Daniel

This document is the B12 runbook and retrospective log. B12 is not a code-only
block: it closes only after Daniel completes self-onboarding in production
through the real Telegram + SPA flow, without manual DB inserts or seed scripts.

Step-by-step operator guide: `docs/phase-6d-b12-test-guide.md`.

If this file conflicts with `docs/phase-6d-decisions.md`, the decisions doc
wins.

---

## Closure Gate

Original B12 gate, kept for historical context:

- Daniel has accounts, incomes, debts, and recurring bills registered in
  production through the real self-onboarding flow.
- No manual DB inserts, seed scripts, or parallel shortcuts were used.
- The friction log below has at least 5 real observations. If there are fewer
  than 5, pause and explain why the run still counted as serious dogfooding.
- Any blocker is fixed or explicitly escalated before continuing to B13.
- UX fixes are capped to 1 day. Larger work becomes backlog for post-6d / 6e.

Current gate status:

- Production run completed: WAIVED_BY_OPERATOR
- Entities registered in production: NOT_RECORDED
- Frictions documented: 1 / 5
- Blockers open: NO KNOWN BLOCKERS FROM LOCAL TEST
- B12 close approved by Daniel: YES, via explicit B13 go-ahead on 2026-05-12

---

## Preflight

Run these checks before starting the dogfood session.

### 1. Code and CI baseline

- B11 E2E is green locally or in CI.
- The build deployed to production contains B1-B11.
- Alembic is at head in production.
- Telegram is in webhook mode in production.
- The SPA production URL is the same value configured as `SPA_BASE_URL`.
- Cookie settings match production hosting:
  - `SESSION_COOKIE_SECURE=true`
  - `SESSION_COOKIE_DOMAIN` matches the shared API/SPA parent domain, if used.
  - `SPA_CORS_ORIGINS` includes the production SPA origin.

### 2. Production smoke

Use the real production hosts. Do not paste secrets into this file.

```bash
curl -fsS "$API_BASE_URL/health/ready"
```

If using a clean test user, create it through the real API. This is allowed
because it is product registration, not a DB seed.

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

Store the returned `shortcut_token` somewhere safe. It is returned once.

Then get a Telegram pairing code:

```bash
curl -fsS -X POST "$API_BASE_URL/api/v1/users/me/telegram/pairing-code" \
  -H "X-Shortcut-Token: $SHORTCUT_TOKEN"
```

In Telegram, send:

```text
/start <pairing-code>
```

If using Daniel's real production user instead of a clean test user, record
that decision below and do not manually delete or insert onboarding rows unless
Daniel explicitly decides to reset his own production data.

Chosen user for B12:

- [ ] Clean test user
- [ ] Daniel real user
- User id:
- Telegram device:
- Mobile OS/browser:
- SPA production URL:
- API production URL:

---

## Dogfood Script

Record timestamps and screenshots for any friction. Do not pre-fill data in DB.

### Step 1 — Start from Telegram

Expected:

- `/start` recognizes the new/empty state.
- The copy offers setup web and chat fallback.
- The setup button opens a valid magic link.

Record:

- Timestamp:
- Device/browser:
- Result:
- Friction id(s):

### Step 2 — Magic-link exchange

Expected:

- Link opens the SPA.
- Token is consumed once.
- Expired/used token recovery is clear: return to Telegram and send `/setup`.

Record:

- Timestamp:
- Browser context: Telegram in-app browser / default browser / desktop
- Result:
- Friction id(s):

### Step 3 — Create account in SPA

Create at least one account, for example:

- Name: BAC
- Type: savings
- Currency: CRC
- Initial balance: 0

Expected:

- Form validation is clear.
- Submit succeeds.
- Landing/status updates after creation.

Record:

- Timestamp:
- Result:
- Friction id(s):

### Step 4 — Create income in SPA

Create:

- Salary base
- Aguinaldo linked to salary

Expected:

- Aguinaldo hides manual amount.
- Base salary link is obvious.
- Derived amount is understandable.

Record:

- Timestamp:
- Result:
- Friction id(s):

### Step 5 — Create debt in SPA

Create a real or test debt using French amortization fields:

- Name/creditor
- Principal/current balance
- Annual interest rate
- Term months
- Payment day
- Start date
- Currency

Expected:

- Payment preview is understandable.
- Percent/fraction conversion is clear.
- February/payment-day warning is clear if applicable.

Record:

- Timestamp:
- Result:
- Friction id(s):

### Step 6 — Create recurring bill in SPA

Create at least one fixed bill, for example:

- Provider: ICE
- Category: servicios
- Frequency: monthly
- Amount
- Next/start date
- Optional linked account

Expected:

- Provider/category defaults reduce typing.
- Submit succeeds.
- Status reaches complete after all four entity families exist.

Record:

- Timestamp:
- Result:
- Friction id(s):

### Step 7 — Return to Telegram and log transaction

Send:

```text
gasté 5000 en el super con la BAC
```

Expected:

- Bot links the transaction to the BAC account.
- Lazy detection does not ask to create BAC again.
- Confirmation copy is clear.

Record:

- Timestamp:
- Result:
- Friction id(s):

### Step 8 — Lazy-only mini-flow

Using a second not-yet-created account hint, send:

```text
gasté 5000 con la BCR
```

Expected:

- Bot asks to create/link.
- `crear` enters the chat flow.
- Account is created without SPA.
- Original transaction proposal resumes against the new account.

Record:

- Timestamp:
- Result:
- Friction id(s):

### Step 9 — `/setup` recovery

Send `/setup` from Telegram after the first link has been used.

Expected:

- Bot returns a new link.
- The old used link stays invalid.
- New link opens the SPA.

Record:

- Timestamp:
- Result:
- Friction id(s):

---

## Friction Log

Severity:

- blocker: prevents completing self-onboarding.
- major: completion possible, but a beta user would likely fail or abandon.
- minor: confusing or slow, but recoverable.
- nit: polish issue.

| ID | Timestamp | Step | Severity | Friction | Screenshot / evidence | Fix or backlog |
|---|---|---|---|---|---|---|
| F01 | 2026-05-12 | Magic-link button in local Telegram polling | major | Telegram rejects inline keyboard URLs pointing to `http://localhost:5173` with `Wrong HTTP URL`. | Local traceback from aiogram `TelegramBadRequest`. | Use an HTTPS tunnel for local SPA testing and set `SPA_BASE_URL` to that public URL before generating `/setup` links. |
| F02 |  |  |  |  |  |  |
| F03 |  |  |  |  |  |  |
| F04 |  |  |  |  |  |  |
| F05 |  |  |  |  |  |  |

Additional observations:

| ID | Timestamp | Step | Severity | Friction | Screenshot / evidence | Fix or backlog |
|---|---|---|---|---|---|---|
| F06 |  |  |  |  |  |  |
| F07 |  |  |  |  |  |  |
| F08 |  |  |  |  |  |  |

---

## Patches Applied During B12

Cap: 1 day total. Do not turn B12 into Phase 6e.

| Friction ID | Patch summary | Files changed | Verification |
|---|---|---|---|
|  |  |  |  |

Backlog items:

| Friction ID | Backlog item | Target phase |
|---|---|---|
|  |  |  |

---

## Final B12 Decision

Filled during B13 by explicit operator override. Original B12 required a
production dogfood run; that evidence was not captured in this file. Daniel
accepted the local Telegram polling + HTTPS tunnel test as sufficient to move
to B13.

- B12 result: PASS_BY_OPERATOR_OVERRIDE
- Daniel approved closing B12: YES
- Date/time: 2026-05-12
- Production user used: NOT_RECORDED
- Entities confirmed in production:
  - Accounts: NOT_RECORDED
  - Incomes: NOT_RECORDED
  - Debts: NOT_RECORDED
  - Recurring bills: NOT_RECORDED
- Blockers remaining: none known from local polling/tunnel test
- Follow-up phase: Phase 6e
- B13 verification: `scripts/test_phase_6d.sh` passed on 2026-05-12
  (`59 passed`, SPA lint, SPA build)

Decision notes:

```text
Daniel explicitly asked to proceed with B13 after local Telegram polling
testing worked. This closes B12/B13 as an operator-approved override, not as a
fully evidenced production dogfood retrospective.
```
