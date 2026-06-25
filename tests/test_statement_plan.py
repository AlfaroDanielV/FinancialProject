"""Generalized statement reconciliation — the deterministic plan builder.

Pure tests (no DB): `build_reconcile_plan` + the policy table + conservation. The
LLM is irrelevant here — these exercise normalize → dedup → validate → policy →
resolve over hand-built `StatementExtractionV2` objects + stub ledger rows.

Covers:
- Promerica VISA Emerald Infinite Dual regression (the operator fixture): 2 legs
  (dedup of supplementary cards), payoff selected, conservation verified, the
  current-period interest excluded as contingent.
- Bug 1 (wrong field): policy selects `payoff` over `available`/`financed`.
- Conservation mismatch → needs_review, excluded from the auto-include set.
- No opening balance → auto-include, conservation unverifiable (decision 6).
- Loan via policy → collapses to kind="loan" against a Debt.
- Identity priority: an exact last4 / IBAN beats fuzzy and beats ambiguity.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace as NS

from api.schemas.statements import StatementExtractionV2
from api.services.statement_normalize import (
    auto_includable_items,
    build_reconcile_plan,
    leg_to_item,
    plan_to_extraction,
)


def _acct(name, currency, account_type="credit", **ids):
    return NS(
        id=uuid.uuid4(),
        name=name,
        currency=currency,
        account_type=account_type,
        iban=ids.get("iban"),
        account_number=ids.get("account_number"),
        last4=ids.get("last4"),
    )


def _promerica_extraction(*, extra_instruments=True, perturb=None):
    """The dual-currency card statement. `perturb` adds a fake flow to the CRC leg
    to break conservation."""
    crc_flows = [
        {"amount": 892309.69, "direction": "inflow", "label_raw": "pago"},
        {"amount": 778303.73, "direction": "outflow", "label_raw": "compras"},
        {"amount": 10232.92, "direction": "inflow", "label_raw": "credito"},
        {"amount": 700.50, "direction": "outflow", "label_raw": "cargo"},
        # Current-period interest — contingent, excluded from payoff.
        {"amount": 5440.56, "direction": "outflow", "contingent": True, "label_raw": "interes"},
    ]
    if perturb is not None:
        crc_flows.append({"amount": perturb, "direction": "outflow", "label_raw": "fake"})
    instruments = [{"masked_pan": "****1234", "holder_name": "Daniel", "is_supplementary": False}]
    if extra_instruments:
        instruments.append(
            {"masked_pan": "****5678", "holder_name": "Otro", "is_supplementary": True}
        )
    return StatementExtractionV2.model_validate(
        {
            "bank": "Promerica",
            "period_end": "2026-06-19",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "credit_card",
                    "issuer": "Promerica",
                    "product_name": "VISA Emerald Infinite",
                    "identifiers": [{"kind": "last4", "value": "1234"}],
                    "instruments": instruments,
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "opening_balance": 316828.03,
                            "flows": crc_flows,
                            "closing_candidates": [
                                {"amount": 193289.65, "role": "payoff"},
                                {"amount": 198730.21, "role": "financed"},
                            ],
                        },
                        {
                            "currency": "USD",
                            "opening_balance": None,
                            "flows": [],
                            "closing_candidates": [{"amount": 7.77, "role": "payoff"}],
                        },
                    ],
                }
            ],
        }
    )


def test_promerica_dual_currency_conservation_and_dedup():
    crc = _acct("Promerica ₡", "CRC")
    usd = _acct("Promerica $", "USD")
    plan = build_reconcile_plan(
        _promerica_extraction(), accounts=[crc, usd], debts=[]
    )

    # Two supplementary PANs collapse into ONE account → exactly two legs (Bug 2).
    assert len(plan.legs) == 2
    crc_leg = next(l for l in plan.legs if l.currency == "CRC")
    usd_leg = next(l for l in plan.legs if l.currency == "USD")

    # Payoff (not financed); the ₡5 440,56 interest is excluded.
    assert str(crc_leg.reconcile_value) == "193289.65"
    assert crc_leg.target_role == "payoff"
    assert crc_leg.conservation_ok is True
    assert crc_leg.conservation_delta == 0  # exact
    assert crc_leg.resolution.target_id == crc.id
    assert len(crc_leg.attributed_instruments) == 2  # both PANs attributed

    # USD leg: no opening/flows → unverifiable but auto-included.
    assert str(usd_leg.reconcile_value) == "7.77"
    assert usd_leg.conservation_ok is None
    assert usd_leg.needs_review is False
    assert usd_leg.resolution.target_id == usd.id

    items = auto_includable_items(plan)
    assert len(items) == 2
    assert {i.currency for i in items} == {"CRC", "USD"}
    assert all(i.kind == "credit" for i in items)


def test_bug1_policy_selects_payoff_over_other_roles():
    crc = _acct("Promerica ₡", "CRC")
    plan = build_reconcile_plan(_promerica_extraction(), accounts=[crc], debts=[])
    crc_leg = next(l for l in plan.legs if l.currency == "CRC")
    # financed (198730.21) and payoff (193289.65) both present → payoff wins.
    assert str(crc_leg.reconcile_value) == "193289.65"


def test_conservation_mismatch_flags_and_excludes():
    crc = _acct("Promerica ₡", "CRC")
    usd = _acct("Promerica $", "USD")
    # A fake ₡50 000 outflow makes the payoff candidate inconsistent with flows.
    plan = build_reconcile_plan(
        _promerica_extraction(perturb=50000), accounts=[crc, usd], debts=[]
    )
    crc_leg = next(l for l in plan.legs if l.currency == "CRC")
    assert crc_leg.conservation_ok is False
    assert crc_leg.needs_review is True
    assert crc_leg.review_reason == "conservation_mismatch"

    items = auto_includable_items(plan)
    # The flagged CRC leg is withheld; the clean USD leg still auto-includes.
    assert {i.currency for i in items} == {"USD"}


def test_no_opening_balance_auto_includes_unverified():
    a = _acct("BAC Principal", "CRC", account_type="checking")
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta a la vista",
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [
                                {"amount": 268207.37, "role": "closing"}
                            ],
                        }
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[a], debts=[])
    (leg,) = plan.legs
    assert leg.conservation_ok is None  # no opening/flows to verify against
    assert leg.needs_review is False  # decision 6 — auto-include
    assert str(leg.reconcile_value) == "268207.37"
    assert leg.resolution.kind == "deposit"
    assert leg.resolution.target_id == a.id
    assert len(auto_includable_items(plan)) == 1


def test_loan_collapses_to_debt_via_policy():
    debt = NS(id=uuid.uuid4(), name="Préstamo BAC", currency="CRC", iban=None, account_number=None, last4=None)
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "loan",
                    "product_name": "Préstamo BAC",
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [
                                {"amount": 12119385.98, "role": "principal_outstanding"}
                            ],
                        }
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[], debts=[debt])
    (leg,) = plan.legs
    assert leg.resolution.kind == "loan"
    assert leg.resolution.target_id == debt.id
    items = auto_includable_items(plan)
    assert items[0].kind == "loan"
    assert str(items[0].closing_balance) == "12119385.98"


def test_identity_last4_beats_fuzzy_ambiguity():
    # Two same-currency credit accounts whose names both fuzzy-match "Promerica".
    a = _acct("Promerica ₡", "CRC", last4="1234")
    b = _acct("Promerica Oro ₡", "CRC", last4="9999")
    plan = build_reconcile_plan(_promerica_extraction(), accounts=[a, b], debts=[])
    crc_leg = next(l for l in plan.legs if l.currency == "CRC")
    # Identity (last4=1234) resolves to `a` outright, not an ambiguous fuzzy tie.
    assert crc_leg.resolution.target_id == a.id
    assert crc_leg.resolution.confidence == 1.0


def test_distinct_same_name_accounts_do_not_collapse():
    # Two genuinely different savings accounts that share bank + product name and
    # carry NO identifier must NOT collapse into one group (no dropped leg).
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "savings",
                    "product_name": "Cuenta de ahorro",
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [{"amount": 100000, "role": "closing"}],
                        }
                    ],
                },
                {
                    "account_type": "savings",
                    "product_name": "Cuenta de ahorro",
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [{"amount": 250000, "role": "closing"}],
                        }
                    ],
                },
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[], debts=[])
    assert len(plan.legs) == 2  # neither leg dropped
    assert {str(l.reconcile_value) for l in plan.legs} == {"100000.00", "250000.00"}


def test_empty_currency_legs_surfaces_review_not_dropped():
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {"account_type": "checking", "product_name": "Cuenta vacía", "currency_legs": []}
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[], debts=[])
    assert len(plan.legs) == 1  # surfaced, not silently dropped
    (leg,) = plan.legs
    assert leg.needs_review is True
    assert leg.review_reason == "no_balance"
    assert leg.reconcile_value is None
    assert auto_includable_items(plan) == []


def test_no_target_role_projects_null_balance():
    a = _acct("Cuenta", "CRC", account_type="checking")
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta",
                    # 'minimum' is neither the target nor a fallback for checking.
                    "currency_legs": [
                        {"currency": "CRC", "closing_candidates": [{"amount": 9, "role": "minimum"}]}
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[a], debts=[])
    (leg,) = plan.legs
    assert leg.review_reason == "no_target_role"
    assert leg.reconcile_value is None
    # The back-compat projection must NOT surface a real 0 balance.
    proj = plan_to_extraction(plan, confidence=0.9)
    assert proj.products[0].closing_balance is None
    assert auto_includable_items(plan) == []


def test_ambiguous_role_flags_review():
    a = _acct("Cuenta", "CRC", account_type="checking")
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta",
                    # Two differing 'closing' tags, no opening/flows to disambiguate.
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [
                                {"amount": 100000, "role": "closing"},
                                {"amount": 250000, "role": "closing"},
                            ],
                        }
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[a], debts=[])
    (leg,) = plan.legs
    assert leg.needs_review is True
    assert leg.review_reason == "ambiguous_role"
    assert auto_includable_items(plan) == []


def test_reconcile_value_quantized_to_cents():
    a = _acct("Cuenta", "CRC", account_type="checking")
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta",
                    "currency_legs": [
                        {"currency": "CRC", "closing_candidates": [{"amount": 193289.654, "role": "closing"}]}
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[a], debts=[])
    (leg,) = plan.legs
    assert str(leg.reconcile_value) == "193289.65"  # quantized, no >2-dp crash
    item = leg_to_item(leg)  # constructs without ValidationError
    assert str(item.closing_balance) == "193289.65"


def test_identity_not_stamped_on_elimination_match():
    # One candidate → resolved by elimination (confidence 0.99), NOT by identity.
    a = _acct("Mi cuenta", "CRC", account_type="checking")  # last4 stored None
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta",
                    "identifiers": [{"kind": "last4", "value": "1234"}],
                    "currency_legs": [
                        {"currency": "CRC", "closing_candidates": [{"amount": 1000, "role": "closing"}]}
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[a], debts=[])
    (leg,) = plan.legs
    assert leg.last4 == "1234"
    assert leg.resolution.target_id == a.id
    assert leg.resolution.confidence < 1.0  # by elimination, not identity
    item = leg_to_item(leg)
    # Identity hints withheld → the wrong account can't be poisoned.
    assert item.last4 is None and item.iban is None and item.account_number is None


def test_identity_iban_exact_match():
    a = _acct("Cuenta 1", "CRC", account_type="checking", iban="CR05015202001026284066")
    b = _acct("Cuenta 2", "CRC", account_type="checking")
    ext = StatementExtractionV2.model_validate(
        {
            "bank": "BAC",
            "period_end": "2026-05-31",
            "confidence": 0.9,
            "accounts": [
                {
                    "account_type": "checking",
                    "product_name": "Cuenta",
                    "identifiers": [
                        {"kind": "iban", "value": "CR05 0152 0200 1026 2840 66"}
                    ],
                    "currency_legs": [
                        {
                            "currency": "CRC",
                            "closing_candidates": [{"amount": 1000, "role": "closing"}],
                        }
                    ],
                }
            ],
        }
    )
    plan = build_reconcile_plan(ext, accounts=[a, b], debts=[])
    (leg,) = plan.legs
    # IBAN normalized (spaces stripped) → exact unique hit on `a`.
    assert leg.resolution.target_id == a.id
