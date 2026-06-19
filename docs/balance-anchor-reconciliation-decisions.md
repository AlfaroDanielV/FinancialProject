# Balance Anchor & Reconciliation — Decisions Doc (PLANNING ONLY)

Status: **IMPLEMENTED 2026-06-19 on `dev` — operator on-device sign-off
pending.** Build plan A0–A8 executed: migration `0035` is live on dev; the
104-test consolidated regression (every balance consumer) + the byte-locked
cashflow regression are green; mobile `tsc` clean. The gate rulings below were
honored; four additional forks surfaced during the build and were ruled by the
operator (see "Implementation forks (ruled during build)"). Block 7 is the
executed plan.

### Implementation forks (ruled during build, 2026-06-19)

| Fork | Ruling | Why it surfaced |
|------|--------|-----------------|
| **A2 dedup action** | **Flag, don't skip** — the cross-email duplicate creates the 2nd shadow row with `is_duplicate=True` (never silently skipped); both visible in review. | Auto-skip is invisible under shadow review → a false positive would silently drop a real charge. |
| **A2 "pago recibido" guard (Gate G)** | **Lean on the shadow gate; defer recognition** — no negative-guard code; card-payment→transfer-leg recognition defers to the SINPE/counterparty workstream. | The Gmail email extractor (`ExtractedEmailTransaction`) has no structured parties, so `transfer_direction` can't run on it; and shadow rows can't pollute the balance until approved. |
| **A6 create-time anchor** | **No anchor at create** — new accounts stay anchorless (reconstruct from the entered "Saldo inicial"; same-day captures count); nudge fires only for `migrated` accounts; re-anchor on demand. | An anchor at `eff=today` + strict `>` would exclude **same-day captures** from a fresh account's balance until tomorrow/re-anchor — a daily papercut + broad test breakage. |
| **A7 ajuste in reports** | **Exclude from income/expense** via the `AJUSTE_CATEGORY` marker (NULL-safe `is_distinct_from`) in the 4 `summary.py` aggregators. | B1 settled balance-exclusion (date) but not report-exclusion; a reconciliation is not P&L income/expense. |

### Ratified gate rulings (2026-06-18)

| Gate | Ruling | Note |
|------|--------|------|
| **A** time granularity | **A1** — strict `transaction_date > anchor.effective_date`, DATE-level | Accepted cost: a same-day-after-read expense is missed (balance reads slightly **high**, self-heals at re-anchor). High-and-healing ≫ a double-subtract that mimics the original drift. |
| **B** re-anchor mechanics | **B1** — new anchor = real; ajuste **informational, not summed** | Composes with A1 for free: **date the ajuste on the anchor's `effective_date`** so strict-`>` auto-excludes it. **No `is_summed` flag.** Style it distinctly in activity views so it doesn't read as real spend. |
| **C** anchor storage | **C1** — append-only `account_anchors` table | Roll-up reads latest-anchor-per-account via `DISTINCT ON (account_id) … ORDER BY account_id, effective_date DESC, created_at DESC` (or window fn) — **never** a correlated subquery (N+1). |
| **D** cross-ccy roll-up | **D3** (overrides my D1) — CRC-led, USD shown apart | Do **not** build "Disponible" on the hardcoded ₡500 (`fx.py:23`) — a reconciliation product can't reconcile a fabricated number. Show «₡X disponible (+ $Y en cuentas en dólares)»; each currency reconciles exactly against its accounts. Single-number conversion waits for the live BCCR rate, not a placeholder. |
| **E** straddle | **E** — shared onboarding cut + forward-only per-leg | **Lock the cross-currency transfer test.** |
| **F** undated email | **F-opt-1, no-null variant** | Hold as a pending "needs date" entry in `gmail_messages_seen`; **never insert a `transaction_date IS NULL` ledger row** (nulls weaken an invariant every date reader leans on). Skip+notify is the acceptable lighter fallback if the pending-review UX is too much this sprint. |
| **G** "pago recibido" | **Reject suppress** — negative-guard + counterparty identity | It's the **credit leg of a checking→card transfer**, not a receipt; suppressing loses the card-paydown signal (card debt reads too high). Direction = counterparty identity, **not keywords** (same bug class as the SINPE fix — **one mechanism**). This plan scopes to the **negative guard only** (these emails must not become income/expense rows polluting the post-anchor sum; hold-for-review when unmatched); full transfer reconstruction defers to the counterparty/SINPE workstream. |

**Build-plan defect fixed (operator catch):** the original A4/A5 was **not**
behavior-preserving — a migrated anchor at `created_at::date` plus A1's strict
`>` silently drops every txn dated on/before creation day (same-day rows + any
backfilled pre-creation history) the moment A5 flips, reintroducing the exact
"my balance changed" symptom. **Fix:** the migrated anchor's `effective_date =
MIN(transaction_date) − 1 day` per account (fall back to `created_at::date` when
the account has no txns). Then every existing row is strictly after it and A5
reproduces `initial_balance + Σ` byte-for-byte. Folded into Block 2 + Block 7 A4.

Repo head at time of writing: branch `dev`, `alembic` head
`0034_envelope_members` (next free migration number = **0035**).

Canonical companions: `~/Finance_project/.../05_Decisions/` (to receive a
`Decision - Balance Anchor & Reconciliation` note once approved);
`08_Code-Context/AGENT_CONTEXT.md` rule #6 (day-level granularity) and the
"read-time projection" / "single source of truth" rules this work is downstream
of.

> ⚠️ The root `.gitignore` blocks `docs/**` (logged operational lesson). This
> file exists on disk and will need `git add -f` if/when it is committed — not
> done here (out of scope).

---

## 0. The inversion in one paragraph

Today an account balance is **reconstructed bottom-up**:
`initial_balance + Σ(every captured transaction)`. Because email/bank ingestion
never captures *every* transaction, the reconstruction drifts from the real
bank balance, and onboarding forces the user to hunt for the missing item. We
invert to **standard bank reconciliation**: the user's real bank balance is the
**anchor** (source of truth); transactions are explanatory. Balance is projected
forward from the latest anchor:

```
balance(account) = latest_anchor.value + Σ( txns of that account that fall AFTER the anchor )
```

"AFTER the anchor" is the load-bearing phrase, and §"Decision Gate A" is about
what "after" can even mean given the current schema.

---

## 1. Hypothesis validation (H1–H7)

Each row: **verdict**, evidence (`file:line`), blast radius.

### H1 — More than one code path computes an account balance / "disponible"

**CONFIRMED (and worse than suspected — four paths, two conventions).**

| # | Path | `file:line` | Convention |
|---|------|-------------|------------|
| 1 | `compute_account_balances` (per-account `current` + `month_start`) | `api/services/accounts.py:90` (math `:124–164`) | `initial_balance + Σ(status='confirmed', archived=false)` per account |
| 2 | `_balance_total` (whole-user total) | `api/services/dashboard/summary.py:105` | `Σ initial_balance(active,non-arch) + Σ amount(confirmed,non-arch)` |
| 3 | `_balance_split` → `available`/`savings` (home "DISPONIBLE") | `api/services/dashboard/summary.py:125` | same as #2, JOIN to bucket by `account_type` |
| 4 | `get_account_balance` (chat/native "¿cuánto tengo?") | `app/queries/tools/accounts.py:39` (math `:51–103`) | `Σ amount` **only** — **no `initial_balance`, no `status` filter, no `archived` filter** |

Paths #1–#3 agree on the `initial_balance + Σ confirmed` convention. Path #4 is
**materially divergent**: it omits `initial_balance`, and it counts **shadow**
(unapproved Gmail) and **archived** rows. Consumers of #1:
`api/routers/accounts.py:51`, `api/services/transfers.py:111` (funds guard),
`api/services/goals.py:82` (funds guard), `api/services/credit_cards.py:60`
(card "Debés ₡X / Disponible").

**Blast radius of collapsing to one path:** medium. #2/#3 can become a thin
roll-up over #1; #4 must be re-pointed at #1 (it is the user-facing chat/native
balance answer and is the most wrong). Once the anchor lands, *all four* must
read `latest_anchor + Σ post-anchor`.

### H2 — A balance path reads a monthly "este mes" delta instead of a balance

**REFUTED as literally stated; a different active divergence confirmed in its
place.** No balance-producing path surfaces a month-windowed delta *as the
balance*:

- The home "DISPONIBLE" (`mobile/src/screens/Dashboard.tsx:382–411,449`) reads
  `summary.available_balance`, which is produced by `_balance_split` — and
  `_balance_split` takes **no period/window** (`summary.py:125`). It is fetched
  through a `period=month_current` query (`Dashboard.tsx:385–386`) **only to
  share the React-Query cache**; the `available_balance` value is period-
  independent (all-time). So the headline is an all-time balance, not a monthly
  delta.
- `compute_account_balances.month_start` *is* a month-anchored figure
  (`accounts.py:128–139`), but it is used to render a **labeled month diff**
  (`current − month_start`) on the account detail, not as "the balance."

The real divergence (the thing H2 was probably remembering): **path #4
`get_account_balance` omits `initial_balance`** (`accounts.py` chat tool
`:51–62`). If a user onboards by entering their real balance as
`accounts.initial_balance` (exactly the proposed anchor onboarding), the chat
answer to "¿cuánto tengo?" would **ignore that number** and report only the sum
of captured movements — disagreeing with the home screen. **Verdict: H2 refuted
literally; #4's missing-`initial_balance` bug confirmed and is the user-visible
"chat says the wrong balance" symptom.** ➜ folded into the H1 collapse.

### H3 — `occurred_at` not reliably body-sourced in the parsers; header fallback exists

**CONFIRMED (the fallback is worse than the header).** Two facts:

1. There are **not** three per-bank regex parsers. There is **one LLM body
   extractor** for all banks: `api/services/extraction/email_extractor.py`. Its
   prompt (`:174–175`) asks for `transaction_date` from the **body**, `null` if
   absent. The bank-specific code (`account_guess.py`, `sample_analyzer.py`) is
   for *which account*, not the date. (Block 3 therefore has **one** place to
   fix, not three.)
2. The scanner reads only `Subject`/`From` headers (`scanner.py:716–717`); it
   does **not** read the `Date` header or Gmail `internalDate`, and passes the
   body-extracted `candidate` straight to the reconciler.
3. The reconciler writes
   `transaction_date = candidate.transaction_date or date.today()`
   (`api/services/gmail/reconciler.py:279`). When the body has no date, it
   stamps **`date.today()` = the ingestion/scan date.**

So an undated old payment gets stamped "today" → lands **after** any anchor →
**double-counts** exactly as locked decision #7 warns. There is no header
*date* fallback to remove (none is read); the fix is to **kill the
`date.today()` fallback** and decide what an undated email becomes.

**Blast radius:** small/contained — single line at `reconciler.py:279`, plus a
review-UX decision for undated rows (they are already `shadow`, so they do not
hit the ledger until approved — see Block 3 / Gate). 

### H4 — Transfer legs can be created/applied independently with no shared timestamp

**REFUTED for the canonical path; the straddle risk it implies is real but lives
at the anchor boundary, not at leg creation.** `create_transfer_with_transactions`
(`api/services/transfers.py:33`) creates **both** legs atomically in one call,
sharing a single `occurred_at` (`:126`) → both legs get
`transaction_date=occurred_at.date()` (`:169`, `:182`), one `flush`. No code
path creates a single leg on its own; chat "pagué la tarjeta" and the REST
endpoint both route here (`api/routers/transfers.py:18`). So two legs **always**
share a date.

The genuine concern decision #8 is about is the **anchor straddle**: a transfer
whose single shared date falls *after* one account's anchor but *before* the
other's, so it moves balance on one side only. That is introduced by the anchor,
and is handled in Block 4. Also noted: a **cross-currency** transfer's two legs
carry *different* amounts in *different* currencies (`debited` vs `applied`,
`:101–106`), so the `compute_account_balances` docstring claim that transfer
legs "net out to zero across the user" (`accounts.py:102–103`) is true **only**
for same-currency transfers — relevant to H5.

**Blast radius:** low for H4 itself; the straddle rule (Block 4) is enforced
implicitly once the balance formula is per-account + forward-only.

### H5 — USD amounts reach the balance path without CRC conversion

**CONFIRMED for the cross-account roll-ups; N/A for the per-account path.**

- Per-account (#1 `compute_account_balances`) sums `Transaction.amount` for one
  account with no currency conversion — **correct**, because the convention is
  one currency per account (`accounts.py:99–104`; transfers store each leg in
  its account's currency `transfers.py:160–184`; reassign rewrites currency to
  the destination's, per CLAUDE.md). So a single account's balance is
  single-currency and clean.
- Cross-account roll-ups (#2 `_balance_total` `summary.py:113–122`, #3
  `_balance_split` `summary.py:155–177`, and #4's `total_balance`
  `accounts.py:81–101`) **numerically add ₡ and $** with **no `fx.convert`
  anywhere near them.** `api/services/fx.py::convert` (`:26`,
  `FALLBACK_USD_TO_CRC = 500` `:23`) is only called by envelope spend, reassign,
  and transfers — **never** by a balance path. So a user with a CRC checking + a
  USD account sees a "disponible" that adds dollars to colones 1:1.

**Blast radius:** medium. The anchor invariant is per-account (currency-clean),
so the anchor itself is unaffected. The *roll-up* is where fx must be applied —
see Decision Gate D. (Out-of-scope note: full multi-currency strategy is
explicitly out of scope; this is only "locate the boundary" — it is **absent**.)

### H6 — No transaction-level dedup for one payment arriving as two emails

**CONFIRMED for the two-different-emails case.** Gmail dedup has two guards, and
neither catches it:

- `_check_duplicate_gmail` (`reconciler.py:163`) matches on the **same**
  `gmail_message_id` only (backed by `UNIQUE(user_id, gmail_message_id)`,
  migration 0011). Two different emails = two different ids → not caught.
- `_find_existing_match` (`reconciler.py:91`) only considers rows with
  **`gmail_message_id IS NULL`** (`:130`) — i.e. manual/telegram/shortcut rows.
  It explicitly will **not** match a second Gmail email against the first Gmail
  row ("we don't re-merge gmail rows", `:106`). So the bank-debit email and the
  card-payment-received email for one purchase become **two separate `shadow`
  rows**, and approving both double-counts.
- The newer at-capture detector `api/services/dedup/duplicate_detector.py`
  (`find_likely_duplicate :85`) is **not** wired into the Gmail path (CLAUDE.md:
  "Gmail keeps its reconciler dedup, not re-hooked"), and even if it were it
  filters `status == 'confirmed'` (`:104`, `_is_flaggable_expense :77`) so it
  would skip `shadow` rows anyway.

**Blast radius:** medium. A cross-email dedup key + placement is net-new (Block
5). It interacts with the anchor: a double-counted pair inflates the post-anchor
sum and re-creates the very drift the anchor is meant to remove, so this is a
**true prerequisite**, not a nice-to-have.

### H7 — No place to attach an anchor today; needs an Alembic migration

**CONFIRMED.** `api/models/account.py` has `id, user_id, name, account_type,
currency, initial_balance (Numeric(14,2), :28), is_active, archived, created_at`
— **no anchor column, no anchor table.** Adding the anchor requires a migration
(next number **0035**). `initial_balance` is effectively *already* "the anchor
at `created_at`" — the anchor model generalizes it to a (value, timestamp) that
can be **re-stated** (re-anchored). See Block 2 for the storage shape and the
`Transaction.amount` float-vs-Numeric interaction.

**Blast radius:** schema add (low-risk, additive) + the migration of existing
`initial_balance` into the anchor representation (Gate C).

---

## Block 1 — Balance path inventory & kill/keep

| Path | `file:line` | Inputs | Called from | Reads balance or delta? | CRC/USD | Decision |
|------|-------------|--------|-------------|--------------------------|---------|----------|
| `compute_account_balances` | `accounts.py:90` | `initial_balance`, confirmed non-archived txns, per account | accounts router, transfers funds-guard, goals funds-guard, credit_cards | **balance** (`current`) + month_start delta | per-account single-ccy (clean) | **KEEP → becomes the single invariant.** Rewrite body to `latest_anchor.value + Σ(post-anchor txns)`; `month_start` stays a derived helper. |
| `_balance_total` | `summary.py:105` | initial + Σ across all active accounts | dashboard summary | balance (all-time total) | **adds ₡+$ naively** | **KILL → re-express as Σ `compute_account_balances` + `fx.convert` to display ccy.** (Or keep `balance_total` for back-compat but source it from #1.) |
| `_balance_split` | `summary.py:125` | same, bucketed by `account_type` | dashboard summary (home DISPONIBLE) | balance (available/savings) | **adds ₡+$ naively** | **KEEP shape, re-source** from per-account anchored balances + fx; savings-exclusion logic unchanged (Phase 7h). |
| `get_account_balance` | `app/queries/tools/accounts.py:39` | `Σ amount` only | query dispatcher (chat/native "¿cuánto tengo?") | balance — **but omits `initial_balance`, counts shadow+archived** | **adds ₡+$ naively in `total_balance`** | **KILL the bespoke query → call `compute_account_balances`.** This is the most-wrong, most-user-visible path. |

**Conclusion:** one invariant — `compute_account_balances` — becomes the only
function that turns rows into an account balance. The two dashboard roll-ups and
the chat tool become *callers* of it (the roll-ups add an fx step). The
month-diff helper survives as a derived display value, not a second balance.

**Done-when:** ✅ every balance-producing path enumerated with `file:line` and a
kill/keep decision (above).

---

## Block 2 — Anchor schema & migration plan

### Recommended shape (Decision Gate C — confirm)

**Append-only `account_anchors` table** (new), not columns on `accounts`:

```
account_anchors
  id              UUID PK
  user_id         UUID FK users(id)            NOT NULL      -- query scoping / RLS-ready
  account_id      UUID FK accounts(id) ON DELETE CASCADE NOT NULL
  value           NUMERIC(14,2)                NOT NULL      -- matches accounts.initial_balance precision
  currency        VARCHAR(3)                   NOT NULL      -- = account.currency at anchor time
  effective_date  DATE                         NOT NULL      -- day-level economic "as of" (see Gate A)
  created_at      TIMESTAMPTZ DEFAULT now()    NOT NULL      -- deterministic tiebreaker + audit
  source          VARCHAR(20)                  NOT NULL      -- 'onboarding' | 'reanchor' | 'migrated'
  note            TEXT                         NULL
  -- index: (account_id, effective_date DESC, created_at DESC)  → "latest anchor" lookup
```

"Latest anchor" = the row with the greatest `(effective_date, created_at)` for
the account. Append-only is chosen because (a) decision #6 says re-anchor writes
"a **new** anchor row," (b) decision #1 says "never store a **mutated** running
balance," and (c) it matches the Phase 7e append-only ledger philosophy
(`advice_events`, `*_snapshots`, `user_consents`). Re-anchor history is then a
first-class audit trail ("¿cuándo y por qué cambió mi saldo base?").

**Alternative (Gate C2, not recommended):** two columns on `accounts`
(`anchor_value`, `anchor_at`) overwritten on re-anchor — simpler, but loses
history and mutates in place (mildly against #1).

### Migration 0035 outline (additive, no destructive change)

1. `CREATE TABLE account_anchors (...)` + the lookup index.
2. **Backfill (behavior-preserving — operator-corrected):** one
   `source='migrated'` anchor per account: `value = accounts.initial_balance`,
   `currency = accounts.currency`, `created_at = accounts.created_at`, and
   **`effective_date = MIN(transaction_date) − 1 day` for that account, falling
   back to `accounts.created_at::date` when the account has no transactions.**
   This guarantees every existing txn is strictly after the anchor (A1's strict
   `>`), so the new formula reproduces today's `initial_balance + Σ all` numbers
   **byte-for-byte** until the user actively re-anchors. (An earlier draft used
   `created_at::date`, which would have silently dropped same-day and
   pre-creation-dated rows on cutover — the exact "my balance changed" symptom.)
3. **`accounts.initial_balance` is kept** (not dropped) this migration — it
   becomes the seed for the migrated anchor and a safety net. A later cleanup
   migration may drop it once all readers use anchors. (Same incrementalism as
   the `is_active`/`archived` mirror tech-debt.)

> The backfill is what lets Block 1's path-collapse ship without changing any
> displayed number until a real re-anchor happens — important for a safe rollout.

### `Transaction.amount` float-vs-`Numeric` interaction — recommendation: **stays deferred**

`Transaction.amount` is annotated `Mapped[float]` but the DB column is
`Numeric(12,2)` (`api/models/transaction.py:53`); `accounts.initial_balance`
and the proposed `account_anchors.value` are `Numeric(14,2)` /
`Mapped[Decimal]`. The balance math runs **server-side** (`func.sum`) and is
cast to `Decimal` in Python (`accounts.py:151,161`) — the float *annotation*
never participates in the sum. So the anchor math is exact in `Decimal` **without**
touching the amount-type tech debt. **Recommendation: do not fix the amount
type in this work** (it is orthogonal and high-blast-radius); keep the anchor +
balance math in `Decimal` end-to-end, and keep the existing `Decimal(... or 0)`
casts. One guardrail to add when implementing: assert anchor `value` is read/
written as `Decimal`, never `float`.

**Done-when:** ✅ schema + migration outline + amount-type recommendation written.

---

## Block 3 — `occurred_at` provenance (H3)

**Current behavior (single source, not three):**

| Stage | `file:line` | Date source today |
|-------|-------------|-------------------|
| Body extraction (all banks) | `email_extractor.py:174–175`, schema `:70` | LLM reads `transaction_date` from the **email body**; `null` if absent. ✅ correct source. |
| Scanner | `scanner.py:716–717` | Reads `Subject`/`From` only; supplies **no** date to the reconciler. |
| Reconciler insert | `reconciler.py:279` | `candidate.transaction_date or date.today()` — **`date.today()` fallback is the bug.** |
| Chat/manual capture | `telegram_dispatcher.py:444,463` | `_resolve_occurred_at(hint, today)` → a `date`, resolved server-side from the user's phrase (not header). ✅ already body/user-sourced. |

**Required change (the only one):** remove the `or date.today()` fallback at
`reconciler.py:279`. An undated email must **not** be stamped with the ingestion
date, because once approved it would land after the anchor and double-count
(decision #7). Decision #7 also forbids the email **`Date` header** as a fallback
(received ≠ transaction date), so when the body has no date the compliant
options are:

- **B3-opt-1 (recommended):** create the `shadow` row with `transaction_date =
  NULL`-equivalent held state and **block approval until the user supplies the
  date** in the native review screen (Gmail rows are already `shadow`, so they
  never hit the ledger un-reviewed — this just adds a "fecha requerida" gate to
  one row). Requires `transaction_date` to accept a pending/unknown state for
  shadow rows OR a sentinel + a review-time required field. (NB: the column is
  `NOT NULL` today — `transaction.py:59` — so this needs either a nullable-for-
  shadow allowance or a review-required UX that fills it before insert.)
- **B3-opt-2:** **skip** the email entirely (record `outcome='skipped',
  reason='no_date'` in `gmail_messages_seen`, retryable) — no ledger row at all.
  Simplest; the cost is a silently-missed transaction the user might want.

This is a **Gate** (see Decision Gate F) because opt-1 touches the `NOT NULL`
constraint / review UX and opt-2 trades completeness for simplicity. The
`date.today()` removal itself is not optional.

**Done-when:** ✅ per-stage current behavior + the exact required change
documented, tied back to H3.

---

## Block 4 — Internal-transfer straddle

**Decision (per locked #8, "pick one and justify"): a single shared onboarding
cut timestamp for the first anchor, plus forward-only per-leg inclusion
thereafter.** Concretely:

- **Onboarding writes all of a user's first anchors with ONE shared
  `effective_date` (the onboarding cut).** Every account starts on the same
  reconciliation line. This makes the onboarding straddle *impossible*: there is
  no account whose anchor predates another's at first cut.
- **Ongoing, no special transfer code is needed.** A transfer's two legs share
  one date (proven in H4: `transfers.py:126,169,182`). Each leg is included in
  its own account's balance **iff** it is after *that account's* latest anchor —
  which is exactly what the per-account formula does for free. If the user later
  re-anchors only account A, a transfer leg dated before A's new anchor falls out
  of A's sum (correct — A's real balance already reflects it), while the B leg
  still counts in B (correct — B was not re-stated).

**Failure mode it prevents:** the half-applied transfer — where a card payment
or account-to-account move lowers one balance but the other side sits before its
anchor and never rises (or vice-versa), silently breaking the "transfers net to
zero" property and re-introducing drift.

**Why not "both legs must be post-anchor":** that rule would *drop* a legitimate
transfer whenever one side was re-anchored after the transfer date, losing a
real movement on the still-current side. The shared-cut + per-leg-forward rule
keeps each side correct independently, which is the whole point of
per-account anchoring.

**Enforcement point:** (1) the onboarding flow assigns the shared
`effective_date` to all first anchors (Block 6); (2) `compute_account_balances`
applies the per-account `txn after that account's latest anchor` filter — the
straddle is handled implicitly there, **no per-transfer special-case**. A test
must lock the cross-currency case (legs differ in amount/ccy) so a straddled
cross-ccy transfer can't desync a per-account sum.

**Done-when:** ✅ one rule chosen + justified + enforcement point identified.

---

## Block 5 — Cross-email dedup rule (H6)

**Goal:** one real-world payment that arrives as two different emails (e.g. BAC
debit notification + "pago recibido"/card-payment-received) must not become two
counted rows.

**Candidate dedup key (deterministic — the LLM never decides a dupe, per the
existing `duplicate_detector` doctrine):**

```
same user_id
AND same currency
AND |amount| within ±0.01            (exact for colones; absorbs float noise on USD)
AND transaction_date within ±N days  (N=1 to start — see collision note)
AND signed-amount sign matches       (both expenses, or both income)
AND NOT (transfer leg | goal flow)
[ merchant / last4 similarity = BOOSTER, never a gate ]
```

This deliberately mirrors the locked heuristic already shipped in
`duplicate_detector.py:42–122` ("monto+fecha, comercio refuerza"). **last4**
(`ExtractedEmailTransaction.last4`, `email_extractor.py:71`) is a strong booster
for card emails and should break ties when both emails carry it.

**Collision risks:**

- Two genuinely distinct same-amount, same-day purchases (e.g. two ₡5 000 taxis)
  → a ±1-day, amount-exact window *will* collide. Mitigation: like the existing
  detector, **flag, don't auto-merge** — the dupe becomes a review/keep-or-drop
  decision, never a silent delete. last4/merchant boosters reduce false pairs.
- Debit vs card-payment-received can have **opposite signs** (a charge vs a
  payment-confirmation) or different merchant text. The key must match on the
  *economic* event; recommend matching **expense-to-expense** only for v1 and
  treating the "payment received" confirmation class as a known non-event
  (it often shouldn't create a transaction at all — it's a receipt). This needs
  one operator clarification (Gate G).

**Placement in the ingestion flow:** inside `reconcile()`
(`reconciler.py:182`), as a **new step between** `_check_duplicate_gmail`
(`:223`) and `_find_existing_match` (`:236`) — call it `_find_existing_gmail_
sibling`, scoped to rows where `gmail_message_id IS NOT NULL` and `status IN
('shadow','confirmed')`, applying the key above. On a hit → return a new outcome
`DUPLICATE_GMAIL_SIBLING` (don't create a second shadow row; annotate the
existing one). This is the minimal, contained change and keeps Gmail dedup in
the one place it already lives.

> Interaction with the anchor: this is a **prerequisite** to anchoring being
> trustworthy — an un-deduped sibling pair inflates the post-anchor sum and
> re-creates drift. Sequence Block 5 before/with the formula collapse.

**Done-when:** ✅ dedup key + placement + collision analysis written.

---

## Block 6 — Onboarding & re-anchor flows

User-facing copy is **es-CR voseo**; flow logic is English. Surface follows the
Phase 6d/6f decisions: **chat-first**, with the structured numeric entry living
in the native app where a precise number per account is needed (consistent with
"conversational creation, structured forms only for field-complexity" —
[[Decision - Conversational Creation Over Forms]]). Telegram remains capture/
backup; the anchor numeric entry is native + chat-confirmable.

### Onboarding flow (one number per account → first anchor)

```
For each account the user has (or as they create it):
  1. Ask for the real, current balance from their banking app.
  2. Store it as an account_anchors row:
       value = entered number
       currency = account.currency
       effective_date = shared onboarding cut (Block 4)   ← same for all accounts
       source = 'onboarding'
  3. No backfill, no history reconstruction. From the cut forward, captured
     txns explain movement; the anchor is the truth for the number itself.
```

Copy (chat / native):
- Prompt: **«Abrí tu app del banco y decime el saldo que te aparece HOY en
  «{cuenta}». Ese número es tu punto de partida — de aquí en adelante yo te
  llevo la cuenta.»**
- Confirm: **«Listo: «{cuenta}» arranca en {monto}. Lo que gastés o entre desde
  hoy lo voy sumando o restando a partir de ahí.»**
- (Multiple accounts) **«Hacé lo mismo con cada cuenta y las dejamos todas
  cuadradas al día de hoy.»**

### Re-anchor flow (heal drift → new anchor + labeled ajuste)

```
Trigger: user says the shown balance is wrong ("tengo {real}, no {shown}").
  1. Read projected = compute_account_balances(account).current
  2. delta = real − projected
  3. Write a NEW account_anchors row: value=real, effective_date=today,
     source='reanchor'.
  4. Write a labeled "ajuste de reconciliación" record for `delta`.
     >>> WHETHER THAT AJUSTE COUNTS toward the balance is Decision Gate B <<<
  5. Result: the displayed balance equals `real` immediately; the correction is
     a single number, never a hunt.
```

Copy:
- Ask: **«¿Cuánto te aparece en «{cuenta}» en el banco ahora?»**
- Apply: **«Ajusté «{cuenta}» a {real}. Había una diferencia de {delta} que
  registré como «ajuste de reconciliación» para que cuadre con tu banco.»**
- Audit answer (every number auditable): **«Tu saldo se reancló el {fecha}: de
  {projected} a {real}. La diferencia fue {delta}.»**

**Surface decision:** onboarding anchor entry = native form field (precise
number) reachable from the chat onboarding; re-anchor = chat-initiated
("corregí mi saldo" / a "¿está mal?" affordance on the account screen) →
native confirm. No Telegram-only numeric entry (avoids fat-finger on a balance).

**Done-when:** ✅ both flows specified end-to-end with copy + surface — **modulo
Decision Gate B**, which changes step 4's semantics.

---

## DECISION GATES (operator must rule — I did not resolve these)

Per the approval gate / pause-and-report: each of these is a divergence between
a locked decision and what the code/math actually allows. **A and B are
blocking** (the build plan can't be finalized without them).

### Gate A — Time granularity for "after the anchor" (BLOCKING, load-bearing)

The formula needs `txn_time > anchor_time`. **Transactions have no `occurred_at`
column** — only `transaction_date` (DATE, `transaction.py:59`) and `created_at`
(row-insert TIMESTAMP, not the economic time). AGENT_CONTEXT rule #6 mandates
day-level granularity. So "after" must be defined at DATE resolution. Options:

- **A1 (recommended).** Anchor carries `effective_date` (DATE). Inclusion rule:
  `transaction_date > anchor.effective_date` (**strict** — same-day-as-anchor
  txns are pre-anchor/excluded). Rationale: a purchase already made earlier
  today is *already baked into* the bank balance you just read, so excluding
  same-day is correct. No transactions schema change. Cost: a same-day txn the
  bank hadn't yet reflected is missed until the next re-anchor (rare, self-
  healing). Onboarding copy says "desde hoy te llevo la cuenta" — implying
  same-day-and-earlier is in the anchor.
- **A2.** Do the long-deferred refactor: add `transactions.occurred_at
  TIMESTAMPTZ`, backfill `= transaction_date 00:00 user-tz`, compare strictly.
  Cost: touches every writer/reader; backfilled rows all collide at midnight
  (same-day ordering still ambiguous historically); softens the day-level
  product stance. **Not recommended now.**
- **A3.** DATE-only but **inclusive** (`>=`) with the anchor defined as "balance
  at the **start** of `effective_date`." Cost: onboarding must tell the user to
  read the balance "as of start of day," which is unnatural and error-prone.

**✅ RULED: A1.** Respects rule #6, no transactions migration, natural reading of
"I just read my balance." **Accepted cost (operator):** a same-day-after-read
expense is missed → balance reads slightly **high**, self-healing at the next
re-anchor. This is the *right direction of error* — high-and-healing beats a
double-subtract that would look exactly like the original drift.

### Gate B — Re-anchor mechanics (BLOCKING — decision #6 vs the formula #2)

Decision #6 says re-anchor writes **(a)** a new anchor at the real balance **and
(b)** an "ajuste de reconciliación" transaction for the delta. But balance =
`latest_anchor.value + Σ post-anchor txns`. If the ajuste is a **post-anchor**
transaction, the balance becomes `real + delta` → **double-applies the
correction.** The two parts of #6 are only consistent if exactly one of them
"owns" the delta:

- **B1 (recommended).** Keep the new anchor at the **real** balance; write the
  ajuste as an **informational, pre-anchor (or non-summed) record** — it
  *explains* the gap in the ledger view but is **excluded** from the sum
  (matches decision #4: "pre-anchor txns are informational by derivation"). End
  balance = real. ✔
- **B2.** Do **not** write a new anchor; write **only** a post-anchor ajuste txn
  of `delta`. Balance = `old_anchor + Σ + delta = real`. ✔ Fully consistent with
  #2, but contradicts #6's "write a new anchor row" (re-anchor history then lives
  in ajuste txns, not anchor rows).
- **B3 (literal #6, REJECT).** New anchor (real) **and** summed ajuste → `real +
  delta`. ✘ double-counts.

**✅ RULED: B1**, with the implementation pin that makes it free: **date the
ajuste row on the new anchor's `effective_date`.** A1's strict `>` then excludes
it from the sum automatically — so **no `is_summed` flag and no schema special-
case** is needed: the ajuste is a normal `transaction_date`-stamped row that is
*visible in the ledger* but *absent from the balance* by pure date arithmetic.
Implementation note: **style the ajuste distinctly** (e.g. an "ajuste de
reconciliación" chip/category) so it does not read as real spend in activity/
category views.

### Gate C — Anchor storage shape (lean: append-only table)

**✅ RULED: C1** — append-only `account_anchors` table (§Block 2). `initial_balance`
is kept as the migrated seed anchor and dropped in a later cleanup. **Pin:** the
roll-up and any multi-account read must fetch latest-anchor-per-account with a
**`DISTINCT ON (account_id) … ORDER BY account_id, effective_date DESC,
created_at DESC`** (or an equivalent window function) — a correlated subquery
per account would make the dashboard N+1.

### Gate D — Cross-currency roll-up (scope)

**✅ RULED: D3** (overrides the earlier D1). Per-account anchor math is
currency-clean; the cross-account **roll-up** must NOT add ₡+$ on the hardcoded
₡500 placeholder (`fx.py:23`) — a reconciliation product cannot reconcile a
fabricated number. The roll-up is **CRC-led with USD shown apart**: «₡X
disponible (+ $Y en cuentas en dólares)». Each currency reconciles exactly
against its own accounts. Single-number conversion waits for the **live BCCR
rate**, not a placeholder. (The earlier D1 — `fx.convert` at the boundary — is
rejected for exactly this reason.)

### Gate E — Straddle rule

**✅ RULED: E** — shared onboarding cut for the first anchors + forward-only
per-leg thereafter (Block 4). **Lock the cross-currency transfer straddle test.**

### Gate F — Undated Gmail email (Block 3)

**✅ RULED: F-opt-1, no-null variant.** Kill the `date.today()` fallback
(`reconciler.py:279`). An undated email is **held as a pending "needs date"
record in `gmail_messages_seen`** and never becomes a `transaction_date IS NULL`
ledger row (nulls weaken an invariant every date reader leans on). **Lighter
fallback (accepted if the pending-review UX is too heavy this sprint):**
skip+notify — `outcome='skipped', reason='no_date'` — so the email is recorded,
counted, and surfaced, just not turned into a guessed-date row.

### Gate G — "Payment received" class in dedup (Block 5)

**✅ RULED: reject suppress.** A "pago recibido / pago aplicado a tarjeta" email
is the **credit leg of a checking→card transfer**, not a receipt to drop —
suppressing it permanently loses the card-paydown signal (card debt reads too
high whenever the transfer wasn't also entered by hand). Direction comes from
**counterparty identity, not keywords** (same bug class as the SINPE fix): a
card-payment-received whose counterparty is the user's own card is an internal
transfer leg, never income. Solve it with the **counterparty-identity mechanism
the SINPE fix already needs** (`api/services/identity.py` +
`api/services/dispatch/transfer_direction.py`) — one mechanism, not two. **This
plan scopes to the negative guard only:** these emails must not become income/
expense rows that pollute the post-anchor sum; hold-for-review when unmatched.
Full transfer reconstruction defers to that counterparty/SINPE workstream.

---

## Block 7 — Proposed build plan (ordered by dependency) — FOR APPROVAL

Sequenced so the load-bearing prerequisites (`date.today()` fix, dedup, the
single-path collapse) land before the anchor changes the displayed number.
**No block starts until Gates A & B (min.) are ruled.** Each block is
backend-first, tests included, mobile after backend, on-device sign-off per
project convention.

> Convention reminder for every block: `committed_outflows`/unified-cashflow
> math is **byte-locked** — none of this may touch it (anchors change *account
> balance*, not the envelope/cashflow surplus). A byte-lock regression must stay
> green in each block.

- **A0 — Decisions ratified.** Operator rules Gates A–G; this doc updated; vault
  `Decision - Balance Anchor & Reconciliation` drafted.
  **Done-when:** every gate has a chosen option recorded here.

- **A1 — Kill the ingestion-date fallback (H3).** Replace `reconciler.py:279`
  per Gate F. No anchor yet — pure correctness fix.
  **Done-when:** an undated email no longer becomes a "today" row; test proves
  the chosen Gate-F behavior; `-k gmail` slice green.

- **A2 — Cross-email Gmail dedup (H6, Block 5).** New
  `_find_existing_gmail_sibling` step + `DUPLICATE_GMAIL_SIBLING` outcome in
  `reconcile()`; deterministic key per Block 5 / Gate G.
  **Done-when:** the debit + card-payment-received pair yields one row, not two;
  collision (two distinct same-amount same-day) still flags-not-merges; tests +
  `-k gmail` green.

- **A3 — Collapse to one balance invariant (H1/H2, Block 1) — STILL
  pre-anchor.** Re-point `_balance_total`, `_balance_split`, and the chat
  `get_account_balance` at `compute_account_balances`; add the Gate-D fx step to
  the roll-ups; fix the chat tool to include `initial_balance` + filter
  shadow/archived. **Numbers must not change yet** (still `initial_balance + Σ`).
  **Done-when:** all four paths return identical balances for the same account;
  the chat "¿cuánto tengo?" equals the home "Disponible"; dashboard byte-lock +
  account/transfer/goal/credit regressions green.

- **A4 — Anchor schema (H7, Block 2).** Migration `0035`: `account_anchors`
  (Gate C) + backfill one `migrated` anchor per account, `value =
  accounts.initial_balance`, **`effective_date = MIN(transaction_date) − 1 day`
  per account (fallback `created_at::date` when the account has no txns)** — so
  every existing row is strictly after the anchor and A5 reproduces
  `initial_balance + Σ` byte-for-byte. No reader change yet.
  **Done-when:** `alembic → 0035`; backfill reproduces today's balances exactly;
  `account_anchors` round-trips in `Decimal`.

- **A5 — Forward-only projection (Decisions #2/#3, Gate A).** Rewrite
  `compute_account_balances` body to `latest_anchor.value + Σ(txns after that
  account's latest anchor per Gate A)`. Because every account has exactly the
  migrated creation-anchor, **displayed numbers are unchanged** until a real
  re-anchor. Straddle handled implicitly (Block 4).
  **Done-when:** balances identical to pre-A5 for migrated anchors; a synthetic
  re-anchor changes only that account; cross-currency transfer straddle test
  green; byte-lock green.

- **A6 — Onboarding anchor entry (Block 6).** Native numeric "saldo de partida"
  per account writing `source='onboarding'` anchors at the shared cut; chat
  onboarding copy.
  **Done-when:** a fresh account's first anchor is the user's real number; chat
  + home agree; on-device sign-off.

- **A7 — Re-anchor / heal-drift (Block 6, Gate B).** "Corregí mi saldo" chat
  intent + native confirm → new anchor + ajuste per Gate B's chosen semantics;
  auditable "¿de dónde salió este número?" answer.
  **Done-when:** stating the real balance makes the displayed balance match in
  one step; the ajuste is recorded with the Gate-B semantics; advice/audit trail
  shows the re-anchor; tests + on-device sign-off.

- **A8 — Docs/vault freeze + tech-debt log.** Record residuals: `initial_balance`
  drop (later cleanup), fx roll-up rate still ₡500 placeholder (BCCR debt),
  amount-type still deferred.
  **Done-when:** this doc + vault note finalized; CLAUDE.md tech-debt updated.

**Hard prerequisites encoded in the order:** A1 (date fix) and A2 (dedup) before
A5 (projection), because a wrong-dated or doubled row poisons the post-anchor
sum; A3 (single path) before A4/A5 so there is one place to make anchor-aware;
A4 (backfill) guarantees a behavior-preserving cutover so A5 changes nothing
until the user re-anchors.

**Done-when (Block 7):** ✅ ordered build blocks with a done-when each exist
(above), contingent on the Gates.

---

## Out of scope (restated)

Code/migration/test changes (this is planning); Phase 6c/5d/5c; re-architecting
the parser beyond the `occurred_at` provenance fix; multi-currency strategy
beyond locating the (absent) CRC/USD boundary on the balance path; the BCCR live
fx rate; the `Transaction.amount` float→Decimal refactor.
