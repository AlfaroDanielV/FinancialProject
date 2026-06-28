"""Loan reconciliation guardrails — the "massive debt" net.

Pure (no DB): `build_reconcile_plan` + `statement_guardrails` over hand-built
`StatementExtractionV2` + stub `Debt` rows. Proves a loan anchors the CURRENT
outstanding principal (NOT the original / total-to-pay), and that an implausible
balance is FLAGGED for review (default OFF, confirmable) instead of silently
written. The writer's server-side ack-gate is covered in
`test_statement_reconcile.py`.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

from api.schemas.statements import StatementExtractionV2
from api.services.statement_guardrails import evaluate_loan_balance
from api.services.statement_normalize import build_reconcile_plan


def _debt(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="Préstamo BAC",
        currency="CRC",
        original_amount=Decimal("10000000"),
        current_balance=Decimal("6500000"),
        interest_rate=Decimal("0.12"),
        minimum_payment=Decimal("250000"),
        payment_due_day=1,
        start_date=None,
        archived=False,
    )
    base.update(kw)
    return NS(**base)


def _loan_extraction(candidates, *, verification=None, account_type="loan"):
    acct = {
        "account_type": account_type,
        "issuer": "BAC",
        "product_name": "Préstamo personal",
        "identifiers": [{"kind": "last4", "value": "9012"}],
        "currency_legs": [{"currency": "CRC", "closing_candidates": candidates}],
    }
    if verification is not None:
        acct["verification"] = verification
    return StatementExtractionV2.model_validate(
        {"bank": "BAC", "period_end": "2026-05-31", "confidence": 0.9, "accounts": [acct]}
    )


def _leg(plan):
    assert len(plan.legs) == 1
    return plan.legs[0]


# ── plan builder ──────────────────────────────────────────────────────────────


def test_current_outstanding_selected_over_original_and_total():
    """A loan with all three printed numbers anchors the CURRENT principal."""
    debt = _debt(original_amount=Decimal("10000000"), current_balance=Decimal("6500000"))
    ext = _loan_extraction(
        [
            {"amount": 10000000.0, "role": "original_principal"},
            {"amount": 6200000.0, "role": "principal_outstanding"},
            {"amount": 8900000.0, "role": "total_with_interest"},
        ]
    )
    leg = _leg(build_reconcile_plan(ext, accounts=[], debts=[debt]))
    assert str(leg.reconcile_value) == "6200000.00"
    assert leg.target_role == "principal_outstanding"
    assert leg.needs_review is False
    assert leg.resolution.kind == "loan"
    assert leg.resolution.target_id == debt.id


def test_missing_principal_outstanding_needs_review_not_financed():
    """No `principal_outstanding`/`closing` → review, NEVER anchor the
    original/total (the dropped `financed` fallback)."""
    debt = _debt()
    ext = _loan_extraction(
        [
            {"amount": 10000000.0, "role": "original_principal"},
            {"amount": 8900000.0, "role": "total_with_interest"},
            {"amount": 9500000.0, "role": "financed"},
        ]
    )
    leg = _leg(build_reconcile_plan(ext, accounts=[], debts=[debt]))
    assert leg.reconcile_value is None
    assert leg.needs_review is True
    assert leg.review_reason == "no_target_role"


def test_balance_above_original_flags_review():
    """Anchoring a number above the original amount is the massive-debt tell."""
    debt = _debt(original_amount=Decimal("10000000"), current_balance=Decimal("6500000"))
    ext = _loan_extraction([{"amount": 18000000.0, "role": "principal_outstanding"}])
    leg = _leg(build_reconcile_plan(ext, accounts=[], debts=[debt]))
    assert str(leg.reconcile_value) == "18000000.00"  # carried (editable), not dropped
    assert leg.needs_review is True
    assert leg.review_reason == "loan_exceeds_original"
    assert leg.original_amount == Decimal("10000000")
    assert leg.prior_balance == Decimal("6500000")


def test_verification_disagreement_flags():
    """Plausible numerically, but the focused read disagrees on the current
    balance → flag (so the user reconciles the two)."""
    debt = _debt(original_amount=Decimal("10000000"), current_balance=Decimal("6500000"))
    ext = _loan_extraction(
        [{"amount": 6200000.0, "role": "principal_outstanding"}],
        verification={"current_balance_amount": 8900000.0},
    )
    leg = _leg(build_reconcile_plan(ext, accounts=[], debts=[debt]))
    assert str(leg.reconcile_value) == "6200000.00"
    assert leg.needs_review is True
    assert leg.review_reason == "loan_verification_disagreement"


def test_verification_is_original_flags_role_suspect():
    debt = _debt(original_amount=Decimal("10000000"), current_balance=Decimal("6500000"))
    ext = _loan_extraction(
        [{"amount": 6200000.0, "role": "principal_outstanding"}],
        verification={"current_balance_amount": 6200000.0, "is_original_amount": True},
    )
    leg = _leg(build_reconcile_plan(ext, accounts=[], debts=[debt]))
    assert leg.needs_review is True
    assert leg.review_reason == "loan_role_suspect"


def test_loan_mistyped_investment_forced_via_verification():
    """A loan the inventory pass mis-typed `investment` is forced to the loan
    policy when the focused pass confirms it — so it sets Debt.current_balance
    instead of anchoring the wrong ledger."""
    debt = _debt()
    ext = _loan_extraction(
        [{"amount": 6200000.0, "role": "principal_outstanding"}],
        verification={"account_type_confirmed": "loan", "current_balance_amount": 6200000.0},
        account_type="investment",
    )
    leg = _leg(build_reconcile_plan(ext, accounts=[], debts=[debt]))
    assert leg.account_type == "loan"
    assert leg.resolution.kind == "loan"
    assert leg.resolution.target_id == debt.id


# ── evaluate_loan_balance unit ────────────────────────────────────────────────


def test_evaluate_plausible_not_flagged():
    v = evaluate_loan_balance(
        new_balance=Decimal("6000000"),
        original_amount=Decimal("10000000"),
        prior_balance=Decimal("6500000"),
    )
    assert v.flagged is False


def test_evaluate_exceeds_original():
    v = evaluate_loan_balance(
        new_balance=Decimal("18000000"),
        original_amount=Decimal("10000000"),
        prior_balance=Decimal("6500000"),
    )
    assert v.flagged and v.reason == "loan_exceeds_original"


def test_evaluate_balance_jumped():
    v = evaluate_loan_balance(
        new_balance=Decimal("9000000"),  # ≤ original, but >> prior
        original_amount=Decimal("10000000"),
        prior_balance=Decimal("6500000"),  # 9M > 6.5M × 1.25
    )
    assert v.flagged and v.reason == "loan_balance_jumped"


def test_evaluate_amortization_high_zero_interest():
    # 0% loan: 6 of 12 payments of ₡1M on ₡12M → expected ₡6M.
    v = evaluate_loan_balance(
        new_balance=Decimal("10000000"),  # ≤ original 12M, but 10M > 6M × 1.4
        original_amount=Decimal("12000000"),
        prior_balance=Decimal("11000000"),  # no jump (10M < 11M)
        annual_rate=Decimal("0"),
        cuota=Decimal("1000000"),
        due_day=1,
        start_date=date(2025, 1, 1),
        corte_date=date(2025, 7, 1),
    )
    assert v.flagged and v.reason == "loan_amortization_high"
    assert v.expected_outstanding == Decimal("6000000.00")
