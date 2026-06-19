# Phase 7c — UI 2.0: Neutral Theme + Money Clarity

Status: code-complete on branch `phase-7c-ui` — operator on-device sign-off
pending. Decision note: vault `Decision - UI 2.0 - Neutral Theme & Money
Clarity`.

## Why

Operator ask (2026-06-11): *"Is too hard to understand my money with this UI —
make it extremely professional, tasteful, modern; German design principles
(form follows function); subtle icons; minimal."*

Diagnosis: two distinct problems, not one.

1. **Information architecture.** The Inicio tab led with SALDO TOTAL (not the
   daily question), buried upcoming payments inside a collapsed section, and
   filtered the upcoming feed to `item_type === "bill"` only — so projected
   loan cuotas and card minimums (Phase 7b B5) never appeared on the home
   screen at all.
2. **Aesthetic.** The warm-parchment palette read rustic, not professional-
   modern. Bauhaus principles were right; the surface treatment wasn't.

## Locked decisions

### 1. Neutral "Rams" palette as a token swap (no key renames)

`mobile/src/theme.ts` keeps every existing token name; only values changed.
Off-white canvas (`#F7F7F4`), white cards, graphite ink text (`#191917`),
**ink accent** (primary actions/active states are near-black, not a brand
hue). Color is reserved for meaning: green income, brick-red expense/overdue
(rule unchanged), ochre caution, class hues on envelope bars. Because every
screen consumes tokens, the retheme propagates app-wide without touching the
other ~27 screens.

### 2. Inter for display/money text — scoped rollout, not a big-bang sweep

Static Inter TTFs (Regular/Medium/SemiBold/Bold, v4.1) vendored in
`mobile/assets/fonts/`, loaded via the already-present `expo-font` in
`App.tsx` (waits on splash; **proceeds on system font if loading fails** —
never blocks). `theme.ts` exports `Fonts`; rule: set `fontFamily`, never also
`fontWeight` (static files carry one weight; iOS would synthesize a fake
bold). RN has no global font override, so migrating all ~60 style sheets in
one block would be high-churn/low-verifiability (no native CI; on-device
review is the only visual gate). Rollout: Inicio + navigation chrome now;
other screens adopt `Fonts` as they're touched. System San Francisco remains
the body font elsewhere — acceptable pairing, both neutral grotesks.

### 3. The home screen answers "¿cuánto me queda este mes?" first

New Inicio order: **hero** (Te queda este mes) → **Próximos pagos** (always
visible) → **Sobres** → **Resumen** (period picker + categorías on demand +
saldo total as a quiet footer row). The hero number is
`total_available` from `/envelopes/summary` — computed in
`compute_envelope_summary` next to the per-envelope bars, so the headline can
never drift from them (auditability rule: the UI never derives new financial
math). Hero bar drains like the envelope bars and goes red in the last 5% /
over budget. No envelopes → an explicit "creá tus sobres" empty state, no
fabricated number.

### 4. Backend: grand totals on the envelope summary

`EnvelopeSummaryResponse` gains `total_spent`, `total_reserved`,
`total_available` (roots only, summary currency — same no-double-count rule
as `total_limit`). Additive schema change; no migration. `committed_outflows`
and the unified cashflow are untouched (byte-locked regression still green).

### 5. Period picker is scoped to Resumen

The hero, pagos, and sobres are inherently "now"; only the Resumen metrics
make sense for `month_prev`/`ytd`. The old top-level picker implied the whole
screen changed period — it didn't (sobres were always current-month). The
picker now lives in the Resumen card header.

### 6. Upcoming-feed fix: all payment types

`UpcomingFeedItem.item_type` union extended with `"card_payment"` (it was
missing client-side since 7b B5) and the home list now shows
bill + debt + card_payment (events excluded — reminders, not payments),
overdue first, 3-row preview with "Ver los N restantes".

### 7. Still no chart library

Bars remain flex `View`s. A trend chart is not what "understand my money"
was missing; revisit only with a concrete question a bar can't answer.

## Files touched

Backend: `api/schemas/envelopes.py`, `api/services/envelopes.py`,
`tests/test_envelopes.py`, `tests/test_envelope_reservations.py`.

Mobile: `theme.ts` (v2 palette + `Fonts` + `FontSize.hero`), `App.tsx`
(useFonts + StatusBar dark), `screens/Dashboard.tsx` (rewritten),
`screens/SplashScreen.tsx` (neutral), `navigation/AppNavigator.tsx`
(Inter chrome), `api/envelopes.ts` (totals + refreshed class hues),
`api/dashboard.ts` (card_payment), `api/goals.ts` + `api/categories.ts`
(palette-aligned status/preset colors), `assets/fonts/Inter-*.ttf` (new).

## Verification (2026-06-11)

- `bash scripts/test_phase_7b.sh` green in this branch: mobile
  `tsc --noEmit` clean; 48 focused + 136 regression (incl. the byte-locked
  unified-cashflow regression).
- Focused envelope suites with the new totals assertions: 14 passed; the
  envelope-adjacent slice (subenvelopes, per-item gate, monthly cashflow,
  tool, chat assign, attach suggestion): 36 passed.
- No migration; `alembic current` unchanged (`0029 (head)`).
- **Pending:** operator on-device sign-off (fonts render, hero math vs
  sobres bars, pagos list contents, overall look).

## Deferred

- Full Inter migration across remaining screens (adopt `Fonts` per touched
  screen; optional dedicated sweep later).
- Per-screen layout polish beyond Inicio (Cuentas/Movimientos/Más already
  inherit the new palette via tokens).
- Trend/cash-flow chart (only with a concrete operator question).
- Envelope class hues / goal status hues are display metadata in
  `api/envelopes.ts` / `api/goals.ts`; stored user category colors are
  untouched (only the preset swatches for new picks changed).
