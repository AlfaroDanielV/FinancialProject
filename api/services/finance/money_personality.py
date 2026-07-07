"""`money_personality` — the deterministic money-archetype label (Workstream E).

A behavioral classifier that reads a user's OWN ledger and labels them
Spender / Avoider / Saver / Investor. It lives HERE, next to
``financial_state`` — the sanctioned deterministic-label precedent in the P7
finance layer — and follows the same discipline: a FROZEN label set, a pure
first-match-wins classifier over typed signals, and a ``gather`` half that
composes EXISTING engine outputs (``compute_envelope_summary``,
``compute_account_balances``, the dashboard income/expense filters) and
re-derives no new financial figure.

**"LLM extracts; rules decide."** Nothing here calls an LLM. The classifier is
pure math over the ledger; the label is persisted as a Phase 6c COMPUTED
insight (``money_personality``) by ``compute_money_personality`` in
``api/services/insights/computed.py`` and shapes narration only — never a
number.

**The labels are FROZEN** — the ``framing`` ranking modifier maps them to
Klontz money-scripts, and the persisted insight is tagged against these exact
strings. Renaming or removing one requires a decision note.

Precedence (first-match-wins, deterministic — see ``classify_money_personality``):

0. **insufficient data** — fewer than ``MIN_CONFIRMED_TXNS`` confirmed captures
   in 90 days → ``None`` (nothing trustworthy to say yet).
1. **AVOIDER** — disengaged: at least two of {no budget, no capture in
   ``AVOIDER_STALE_DAYS`` days, ``AVOIDER_SHADOW_BACKLOG``+ shadow rows waiting}.
2. **INVESTOR** — an investment account with a positive balance, or an
   investing-class allocation ≥ ``INVESTOR_ALLOCATION_SHARE`` of the budget.
3. **SAVER** — saving hard: savings-rate ≥ ``SAVER_SAVINGS_RATE`` AND (wants
   spend restrained OR ``SAVER_GOAL_CONTRIBUTIONS``+ goal aportes in 90 days).
4. **SPENDER** — low/negative savings-rate, wants-heavy, or repeatedly
   over-limit.
5. **fallback** — saver if the savings-rate clears ``FALLBACK_SAVER_SAVINGS_RATE``
   else spender. When income is unknown (``income_3m <= 0``) the savings-rate
   rules never match and, absent a wants/over-limit spender signal, the result
   is ``None`` (genuinely indeterminate).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.account import Account
from ...models.goal import Goal
from ...models.goal_contribution import GoalContribution
from ...models.transaction import Transaction
from ...models.user import User
from ...models.user_insight import UserInsight
from ..accounts import compute_account_balances
from ..anchors import AJUSTE_CATEGORY
from ..clock import user_today
from ..envelopes import compute_envelope_summary
from ..income_rules import not_card_payment_income

_ZERO = Decimal("0")
_ONE = Decimal("1")
_SCORE_Q = Decimal("0.01")

# FROZEN label set — see module docstring before touching.
MONEY_PERSONALITIES: tuple[str, ...] = ("spender", "avoider", "saver", "investor")

# ── classification thresholds (every knob is a named constant) ────────────────

# Below this many confirmed captures in the 90-day window there isn't enough
# behavior to classify. Mirrors the "insufficient sample → no insight" spirit.
MIN_CONFIRMED_TXNS = 10

# AVOIDER — disengagement signals; two of three trip the label.
AVOIDER_STALE_DAYS = 14
AVOIDER_SHADOW_BACKLOG = 10
AVOIDER_SIGNALS_REQUIRED = 2

# INVESTOR — an investing-class allocation of this share of the total budget
# (or any positive investment-account balance) reads as investor behavior.
INVESTOR_ALLOCATION_SHARE = Decimal("0.15")

# SAVER — a healthy savings-rate plus restraint or active goal funding.
SAVER_SAVINGS_RATE = Decimal("0.20")
SAVER_WANTS_SHARE_MAX = Decimal("0.35")
SAVER_GOAL_CONTRIBUTIONS = 3

# SPENDER — thin/negative savings-rate, wants-heavy, or over-budget.
SPENDER_SAVINGS_RATE = Decimal("0.05")
SPENDER_WANTS_SHARE = Decimal("0.45")
SPENDER_OVER_LIMIT = 2

# Fallback split when nothing above fires but income is known.
FALLBACK_SAVER_SAVINGS_RATE = Decimal("0.10")

# Sentinel days-since when the user has never captured a confirmed row. Only
# ever surfaces when the count gate (< MIN_CONFIRMED_TXNS) already forces None.
_NO_CAPTURE_DAYS = 10**6

# Spanish (voseo, CR) labels — the memory view + user_context reuse these.
PERSONALITY_LABELS_ES: dict[str, str] = {
    "spender": "Gastador",
    "avoider": "Evasor",
    "saver": "Ahorrador",
    "investor": "Inversionista",
}


@dataclass(frozen=True)
class MoneyPersonalityInputs:
    """The typed deterministic signals the classifier reads. Every field is
    sourced from an existing engine or a direct ledger query — no new financial
    figure is derived here.
    """

    # Confirmed, non-archived captures in the last 90 days, excluding transfer
    # legs / goal flows / reconciliation ajustes (the money-behavior sample).
    confirmed_txn_count_90d: int
    # Income + expense over the last 3 COMPLETE calendar months, user-currency
    # rows, mirroring the dashboard summary exclusions (incl. positive-on-credit
    # is a card payment, never income).
    income_3m: Decimal
    expenses_3m: Decimal
    # Envelope execution (compute_envelope_summary, current month).
    has_budget: bool
    over_limit_count: int
    wants_share: Decimal  # wants-class spend / total classed spend, 0..1
    investing_allocation_share: Decimal  # investing limit / total_limit, 0..1
    # Investing / saving behavior.
    has_investment_balance: bool
    goal_contribution_count_90d: int
    # Engagement.
    days_since_last_capture: int
    shadow_backlog_count: int


@dataclass(frozen=True)
class MoneyPersonalityResult:
    """The label (or None) plus per-label dimension scores and the reasons that
    fired — the advice-trace-friendly payload."""

    personality: Optional[str]
    scores: dict[str, Decimal]  # per-label 0..1
    reasons: list[str]


# ── pure classifier ───────────────────────────────────────────────────────────


def _savings_rate(inputs: MoneyPersonalityInputs) -> Optional[Decimal]:
    """(income − expenses) / income. ``None`` when income is unknown
    (``income_3m <= 0``): every savings-rate rule then treats it as
    not-matching (documented in the module precedence)."""
    if inputs.income_3m <= _ZERO:
        return None
    return (inputs.income_3m - inputs.expenses_3m) / inputs.income_3m


def _clamp01(value: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, value))


def _q_score(value: Decimal) -> Decimal:
    return _clamp01(value).quantize(_SCORE_Q, rounding=ROUND_HALF_UP)


def _dimension_scores(
    inputs: MoneyPersonalityInputs, *, savings_rate: Optional[Decimal]
) -> dict[str, Decimal]:
    """Bounded [0,1] strength per label — informational, deterministic. These
    do NOT decide the label (the precedence does); they explain it."""
    avoider_signals = _avoider_signal_count(inputs)
    avoider = Decimal(avoider_signals) / Decimal(3)

    if inputs.has_investment_balance:
        investor = _ONE
    elif INVESTOR_ALLOCATION_SHARE > _ZERO:
        investor = inputs.investing_allocation_share / INVESTOR_ALLOCATION_SHARE
    else:  # pragma: no cover - constant is positive
        investor = _ZERO

    if savings_rate is None:
        saver_base = _ZERO
        spender_low_savings = _ZERO
    else:
        saver_base = savings_rate / SAVER_SAVINGS_RATE
        spender_low_savings = (
            SPENDER_SAVINGS_RATE - savings_rate
        ) / SPENDER_SAVINGS_RATE
    saver_restraint = _ONE if inputs.wants_share <= SAVER_WANTS_SHARE_MAX else _ZERO
    saver_goal = (
        _ONE if inputs.goal_contribution_count_90d >= SAVER_GOAL_CONTRIBUTIONS else _ZERO
    )
    saver = (
        Decimal("0.6") * _clamp01(saver_base)
        + Decimal("0.2") * saver_restraint
        + Decimal("0.2") * saver_goal
    )

    spender_wants = (
        inputs.wants_share / SPENDER_WANTS_SHARE if SPENDER_WANTS_SHARE > _ZERO else _ZERO
    )
    spender_over = (
        _clamp01(Decimal(inputs.over_limit_count) / Decimal(SPENDER_OVER_LIMIT))
        if SPENDER_OVER_LIMIT > 0
        else _ZERO
    )
    spender = (
        Decimal("0.5") * _clamp01(spender_wants)
        + Decimal("0.3") * spender_over
        + Decimal("0.2") * _clamp01(spender_low_savings)
    )

    return {
        "spender": _q_score(spender),
        "avoider": _q_score(avoider),
        "saver": _q_score(saver),
        "investor": _q_score(investor),
    }


def _avoider_signal_count(inputs: MoneyPersonalityInputs) -> int:
    signals = (
        not inputs.has_budget,
        inputs.days_since_last_capture >= AVOIDER_STALE_DAYS,
        inputs.shadow_backlog_count >= AVOIDER_SHADOW_BACKLOG,
    )
    return sum(1 for s in signals if s)


def classify_money_personality(
    inputs: MoneyPersonalityInputs,
) -> MoneyPersonalityResult:
    """Pure classifier — no LLM, no DB, no network. First-match-wins per the
    module precedence. Returns ``personality=None`` when there isn't enough
    signal (too few captures) or the case is genuinely indeterminate."""
    savings_rate = _savings_rate(inputs)
    scores = _dimension_scores(inputs, savings_rate=savings_rate)

    # 0 — insufficient data.
    if inputs.confirmed_txn_count_90d < MIN_CONFIRMED_TXNS:
        return MoneyPersonalityResult(None, scores, ["insufficient_data"])

    # 1 — AVOIDER: disengagement dominates (a wrong ledger makes the rest moot).
    if _avoider_signal_count(inputs) >= AVOIDER_SIGNALS_REQUIRED:
        return MoneyPersonalityResult("avoider", scores, ["disengaged"])

    # 2 — INVESTOR: money is being put to work.
    if inputs.has_investment_balance:
        return MoneyPersonalityResult("investor", scores, ["investment_balance"])
    if inputs.investing_allocation_share >= INVESTOR_ALLOCATION_SHARE:
        return MoneyPersonalityResult("investor", scores, ["investing_allocation"])

    # 3 — SAVER: real surplus set aside with restraint or active goal funding.
    if (
        savings_rate is not None
        and savings_rate >= SAVER_SAVINGS_RATE
        and (
            inputs.wants_share <= SAVER_WANTS_SHARE_MAX
            or inputs.goal_contribution_count_90d >= SAVER_GOAL_CONTRIBUTIONS
        )
    ):
        return MoneyPersonalityResult("saver", scores, ["high_savings_rate"])

    # 4 — SPENDER: thin/negative savings-rate, wants-heavy, or over-budget.
    if (
        (savings_rate is not None and savings_rate < SPENDER_SAVINGS_RATE)
        or inputs.wants_share >= SPENDER_WANTS_SHARE
        or inputs.over_limit_count >= SPENDER_OVER_LIMIT
    ):
        return MoneyPersonalityResult("spender", scores, ["low_savings_or_wants"])

    # 5 — fallback. Income unknown → indeterminate (never silently "spender").
    if savings_rate is None:
        return MoneyPersonalityResult(None, scores, ["indeterminate_no_income"])
    if savings_rate >= FALLBACK_SAVER_SAVINGS_RATE:
        return MoneyPersonalityResult("saver", scores, ["moderate_savings_rate"])
    return MoneyPersonalityResult("spender", scores, ["default_spender"])


# ── evidence copy (deterministic, voseo CR) ──────────────────────────────────


def describe_personality_evidence(
    result: MoneyPersonalityResult, inputs: MoneyPersonalityInputs
) -> str:
    """A short (≤300 char) deterministic Spanish rationale. No LLM, no synonym
    maps — just the signals that fired, phrased in voseo."""
    personality = result.personality
    wants_pct = int((inputs.wants_share * 100).to_integral_value(ROUND_HALF_UP))
    invest_pct = int(
        (inputs.investing_allocation_share * 100).to_integral_value(ROUND_HALF_UP)
    )
    rate = _savings_rate(inputs)
    rate_pct = (
        int((rate * 100).to_integral_value(ROUND_HALF_UP)) if rate is not None else None
    )
    if personality == "avoider":
        return (
            "Parece que te cuesta llevar el control al día: "
            f"última captura hace {inputs.days_since_last_capture} días"
            + (
                f" y {inputs.shadow_backlog_count} movimientos sin revisar."
                if inputs.shadow_backlog_count >= AVOIDER_SHADOW_BACKLOG
                else "."
            )
        )[:300]
    if personality == "investor":
        if inputs.has_investment_balance:
            return "Tenés plata trabajando en una cuenta de inversión."[:300]
        return (
            f"Estás asignando cerca del {invest_pct}% de tu presupuesto a inversión."
        )[:300]
    if personality == "saver":
        base = (
            f"Ahorrás cerca del {rate_pct}% de tus ingresos"
            if rate_pct is not None
            else "Ahorrás de forma constante"
        )
        if inputs.goal_contribution_count_90d >= SAVER_GOAL_CONTRIBUTIONS:
            return f"{base} y aportás seguido a tus metas."[:300]
        return f"{base} y mantenés tus gustos controlados."[:300]
    # spender
    parts = [f"La mayor parte de tu gasto va a gustos ({wants_pct}%)"]
    if rate_pct is not None and rate < SPENDER_SAVINGS_RATE:
        parts.append(f"y tu tasa de ahorro es baja ({rate_pct}%)")
    if inputs.over_limit_count >= SPENDER_OVER_LIMIT:
        parts.append(f"con {inputs.over_limit_count} sobres pasados del límite")
    return (", ".join(parts) + ".")[:300]


# ── gather (composes existing engines / ledger queries) ──────────────────────


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


async def gather_personality_inputs(
    db: AsyncSession, user: User, *, today: date | None = None
) -> MoneyPersonalityInputs:
    """Assemble the deterministic signals for one user. Reuses the dashboard
    income/expense exclusions, ``compute_envelope_summary`` (so the wants /
    over-limit / allocation figures can't drift from the bars),
    ``compute_account_balances`` (the single balance invariant), and direct
    ledger counts. Re-derives no financial figure."""
    today = today or user_today(user)
    currency = user.currency or "CRC"

    ninety_days_ago = today - timedelta(days=90)
    ninety_days_ago_dt = datetime.combine(
        ninety_days_ago, time.min, tzinfo=timezone.utc
    )
    current_month_start = date(today.year, today.month, 1)
    three_months_start = _add_months(current_month_start, -3)

    # Behavior sample: confirmed, non-archived captures in 90 days, excluding
    # transfer legs / goal flows / reconciliation ajustes.
    behavior_filters = (
        Transaction.user_id == user.id,
        Transaction.status == "confirmed",
        Transaction.archived.is_(False),
        Transaction.transfer_id.is_(None),
        Transaction.goal_id.is_(None),
        Transaction.category.is_distinct_from(AJUSTE_CATEGORY),
    )
    confirmed_txn_count_90d = int(
        (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    *behavior_filters,
                    Transaction.transaction_date >= ninety_days_ago,
                    Transaction.transaction_date <= today,
                )
            )
        ).scalar_one()
    )

    # Income / expenses over the last 3 COMPLETE calendar months, user-currency
    # rows, mirroring the dashboard summary exclusions (positive-on-credit is a
    # card payment, never income).
    flow_row = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (Transaction.amount > 0)
                                & not_card_payment_income(user.id),
                                Transaction.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            ((Transaction.amount < 0), func.abs(Transaction.amount)),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(
                Transaction.user_id == user.id,
                Transaction.status == "confirmed",
                Transaction.archived.is_(False),
                Transaction.transfer_id.is_(None),
                Transaction.goal_id.is_(None),
                Transaction.category.is_distinct_from(AJUSTE_CATEGORY),
                Transaction.currency == currency,
                Transaction.transaction_date >= three_months_start,
                Transaction.transaction_date < current_month_start,
            )
        )
    ).one()
    income_3m = Decimal(flow_row[0] or 0)
    expenses_3m = Decimal(flow_row[1] or 0)

    # Envelope execution — reuse the summary so wants / over-limit / allocation
    # figures are the same the bars show.
    summary = await compute_envelope_summary(db, user=user, today=today)
    has_budget = summary.total_limit > 0
    over_limit_count = sum(
        1 for e in summary.envelopes if not e.is_shared and e.over_limit
    )
    wants_spent = sum(
        Decimal(str(sub.spent_total))
        for sub in summary.by_class
        if sub.envelope_class == "wants"
    )
    total_classed_spent = sum(
        Decimal(str(sub.spent_total)) for sub in summary.by_class
    )
    wants_share = (
        (wants_spent / total_classed_spent) if total_classed_spent > _ZERO else _ZERO
    )
    investing_limit = sum(
        Decimal(str(sub.limit_total))
        for sub in summary.by_class
        if sub.envelope_class == "investing"
    )
    total_limit = Decimal(str(summary.total_limit))
    investing_allocation_share = (
        (investing_limit / total_limit) if total_limit > _ZERO else _ZERO
    )

    # Investment balance — any active investment account with a positive live
    # balance (compute_account_balances, the single invariant).
    inv_ids = list(
        (
            await db.execute(
                select(Account.id).where(
                    Account.user_id == user.id,
                    Account.is_active.is_(True),
                    Account.archived.is_(False),
                    Account.account_type == "investment",
                )
            )
        )
        .scalars()
        .all()
    )
    has_investment_balance = False
    if inv_ids:
        balances = await compute_account_balances(
            db, user_id=user.id, account_ids=inv_ids
        )
        has_investment_balance = any(
            aid in balances and balances[aid].current > _ZERO for aid in inv_ids
        )

    # Goal funding in the last 90 days (goal_contributions has no user_id → join
    # goals).
    goal_contribution_count_90d = int(
        (
            await db.execute(
                select(func.count(GoalContribution.id))
                .join(Goal, Goal.id == GoalContribution.goal_id)
                .where(
                    Goal.user_id == user.id,
                    GoalContribution.occurred_at >= ninety_days_ago_dt,
                )
            )
        ).scalar_one()
    )

    # Engagement — days since the most recent confirmed, non-archived capture.
    last_capture = (
        await db.execute(
            select(func.max(Transaction.transaction_date)).where(
                Transaction.user_id == user.id,
                Transaction.status == "confirmed",
                Transaction.archived.is_(False),
            )
        )
    ).scalar_one()
    days_since_last_capture = (
        max(0, (today - last_capture).days)
        if last_capture is not None
        else _NO_CAPTURE_DAYS
    )

    shadow_backlog_count = int(
        (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.user_id == user.id,
                    Transaction.status == "shadow",
                )
            )
        ).scalar_one()
    )

    return MoneyPersonalityInputs(
        confirmed_txn_count_90d=confirmed_txn_count_90d,
        income_3m=income_3m,
        expenses_3m=expenses_3m,
        has_budget=has_budget,
        over_limit_count=over_limit_count,
        wants_share=wants_share,
        investing_allocation_share=investing_allocation_share,
        has_investment_balance=has_investment_balance,
        goal_contribution_count_90d=goal_contribution_count_90d,
        days_since_last_capture=days_since_last_capture,
        shadow_backlog_count=shadow_backlog_count,
    )


async def classify_personality_for_user(
    db: AsyncSession, user: User, *, today: date | None = None
) -> MoneyPersonalityResult:
    """Gather + classify in one call. The nightly computed writer uses the
    lower-level ``gather`` + ``classify`` pair directly so it can reuse the
    inputs for the confidence/evidence; this is the convenience entrypoint."""
    inputs = await gather_personality_inputs(db, user, today=today)
    return classify_money_personality(inputs)


async def stored_personality(db: AsyncSession, user_id: uuid.UUID) -> Optional[str]:
    """Return the personality label from the latest VALID (non-expired)
    ``money_personality`` insight row, or None. The orchestrator + the framing
    ranking modifier read this; it never recomputes."""
    now = datetime.now(timezone.utc)
    row = (
        await db.execute(
            select(UserInsight.content)
            .where(
                UserInsight.user_id == user_id,
                UserInsight.insight_type == "money_personality",
                UserInsight.valid_until > now,
            )
            .order_by(
                UserInsight.confidence.desc(),
                UserInsight.updated_at.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return None
    personality = (row or {}).get("personality")
    if personality in MONEY_PERSONALITIES:
        return personality
    return None
