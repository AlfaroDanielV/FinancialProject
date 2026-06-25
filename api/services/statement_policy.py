"""Per-account-type policy for statement reconciliation — the ONLY type-specific
code in the whole feature.

Generality comes from two moves: the LLM normalizes every statement into the same
semantic primitives (see `schemas/statements.py`), and ALL bank/type behavior
lives here as a declarative table — which balance ROLE anchors the account, its
SIGN convention, and which ledger entity it collapses onto. Adding a new BANK is
zero code; adding a new account TYPE is one row.

Pure: no LLM, no DB, no network. "LLM extracts; rules decide."
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Optional

from ..schemas.statements import (
    AccountTypeSem,
    BalanceRole,
    StatementKind,
    StmtCurrencyLeg,
)


@dataclass(frozen=True)
class AccountPolicy:
    # The role whose printed balance becomes the anchor; tried first, then the
    # fallbacks (e.g. a card with no explicit "payoff" line falls back to
    # "financed").
    target_role: BalanceRole
    fallback_roles: tuple[BalanceRole, ...]
    # Storage sign: assets are stored positive, liabilities negative
    # (`credit_cards.py`: owed = max(0, -balance)). The writer applies it; it must
    # NEVER leak into the conservation magnitude comparison.
    sign: Literal["asset", "liability"]
    # Where the reconciled balance lands.
    ledger_entity: Literal["anchor", "debt"]
    # The legacy collapse kind the writer already branches on — this is what keeps
    # `reconcile_products` untouched: the rich `account_type` maps back to the
    # 3-value `StatementKind`.
    reconcile_kind: StatementKind


# The whole type-specific surface of the feature.
POLICY: dict[AccountTypeSem, AccountPolicy] = {
    "checking": AccountPolicy("closing", ("available",), "asset", "anchor", "deposit"),
    "savings": AccountPolicy("closing", ("available",), "asset", "anchor", "deposit"),
    "credit_card": AccountPolicy("payoff", ("financed",), "liability", "anchor", "credit"),
    "loan": AccountPolicy(
        "principal_outstanding", ("closing", "financed"), "liability", "debt", "loan"
    ),
    "line_of_credit": AccountPolicy(
        "principal_outstanding", ("closing", "financed"), "liability", "debt", "loan"
    ),
    # Experimental — no real fixture yet; ships so the table is complete.
    "investment": AccountPolicy("market_value", ("closing",), "asset", "anchor", "deposit"),
}


def policy_for(account_type: str) -> Optional[AccountPolicy]:
    return POLICY.get(account_type)  # type: ignore[arg-type]


_CENT = Decimal("0.01")


def select_target(
    leg: StmtCurrencyLeg, policy: AccountPolicy
) -> tuple[Optional[Decimal], Optional[BalanceRole], bool]:
    """Pick the role-tagged closing balance to anchor.

    Returns `(magnitude, matched_role, ambiguous)`:
    - `magnitude` — a POSITIVE Decimal **quantized to cents** (so the write item
      never trips the `decimal_places=2` constraint), or None when no candidate
      carries the target/fallback roles (→ the leg needs manual review; we NEVER
      fabricate a balance).
    - `matched_role` — which role was used (target or a fallback).
    - `ambiguous` — True when the matched role appears on ≥2 candidates with
      DIFFERENT amounts (the LLM tagged it twice and we can't tell which); the
      caller flags review unless conservation disambiguates.

    Bug-1 fix: the role is chosen by the policy table, not by field order or the
    LLM's preference.
    """
    by_role: dict[str, list[Decimal]] = {}
    for cand in leg.closing_candidates:
        by_role.setdefault(cand.role, []).append(
            Decimal(str(cand.amount)).copy_abs().quantize(_CENT)
        )
    for role in (policy.target_role, *policy.fallback_roles):
        amounts = by_role.get(role)
        if amounts:
            return amounts[0], role, len(set(amounts)) > 1
    return None, None, False


def select_target_magnitude(
    leg: StmtCurrencyLeg, policy: AccountPolicy
) -> Optional[Decimal]:
    """Thin wrapper — the role-tagged anchor magnitude (or None)."""
    return select_target(leg, policy)[0]
