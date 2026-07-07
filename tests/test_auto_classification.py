"""Auto-classification at capture — the deterministic classifier contract.

Covers `api/services/classification.py` (operator ask 2026-07-06): every
capture best-effort fills category (+ envelope for expenses) from the user's
OWN confirmed history and, for the category only, an exact free-text hint
match. The LLM never decides here — this is pure lookup of the user's own rows.

Locked behaviors:
- History category + envelope match on normalized merchant (expense only for
  the envelope), flagged auto.
- Auto-assigned rows do NOT seed future suggestions (the flag fences them out),
  so a wrong guess can never self-reinforce.
- A free-text category hint that names an existing category links the FK
  WITHOUT the auto flag (dual-field reconciliation); history never overrides
  provided text.
- Sign filter: an income lookup never borrows an expense's history and never
  gets an envelope.
- Machine-managed rows (transfer legs, goal flows, loan cargos, ajustes,
  archived) are not classifiable.
- An explicit category_id / envelope_id set by the client is never overwritten.
- `apply_classification` never raises and `suggest_classification` never mutates.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from api.models.envelope import Envelope
from api.models.transaction import Transaction
from api.models.user_category import UserCategory
from api.services.anchors import AJUSTE_CATEGORY
from api.services.classification import (
    apply_classification,
    is_classifiable,
    suggest_classification,
)

pytestmark = pytest.mark.asyncio

_TODAY = date(2026, 7, 6)


async def _category(
    session, user_id, *, name="Comida", kind="expense"
) -> UserCategory:
    cat = UserCategory(
        user_id=user_id, name=name, kind=kind, color="#abcdef", icon=None
    )
    session.add(cat)
    await session.flush()
    return cat


async def _envelope(session, user_id, *, name="Súper") -> Envelope:
    env = Envelope(
        user_id=user_id,
        name=name,
        envelope_class="needs",
        limit_amount=100000,
        currency="CRC",
    )
    session.add(env)
    await session.flush()
    return env


async def _txn(
    session,
    user_id,
    *,
    merchant="Auto Mercado",
    amount=-5000,
    category=None,
    category_id=None,
    category_auto=False,
    envelope_id=None,
    envelope_auto=False,
    source="manual",
    status="confirmed",
    days_ago=1,
    transfer_id=None,
    goal_id=None,
    archived=False,
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        amount=amount,
        currency="CRC",
        merchant=merchant,
        category=category,
        category_id=category_id,
        category_auto_assigned=category_auto,
        envelope_id=envelope_id,
        envelope_auto_assigned=envelope_auto,
        transaction_date=_TODAY - timedelta(days=days_ago),
        source=source,
        status=status,
        transfer_id=transfer_id,
        goal_id=goal_id,
        archived=archived,
    )
    session.add(txn)
    await session.flush()
    return txn


# ── history: category + envelope ────────────────────────────────────────────


async def test_history_fills_category_and_envelope_flagged_auto(db_with_user):
    session, user_id = db_with_user
    cat = await _category(session, user_id)
    env = await _envelope(session, user_id)
    # A prior USER-SET row for this merchant.
    await _txn(
        session,
        user_id,
        category="Comida",
        category_id=cat.id,
        envelope_id=env.id,
        days_ago=3,
    )
    # New uncategorized capture, same merchant.
    new = await _txn(session, user_id, days_ago=0)

    applied = await apply_classification(session, user_id=user_id, txn=new)

    assert new.category_id == cat.id
    assert new.category == "Comida"
    assert new.category_auto_assigned is True
    assert new.envelope_id == env.id
    assert new.envelope_auto_assigned is True
    assert applied.category_source == "history"
    assert applied.envelope_name == env.name


async def test_auto_assigned_history_does_not_seed(db_with_user):
    session, user_id = db_with_user
    cat = await _category(session, user_id)
    # The only prior row was itself AUTO-assigned — it must NOT be a source.
    await _txn(
        session,
        user_id,
        category="Comida",
        category_id=cat.id,
        category_auto=True,
        days_ago=3,
    )
    new = await _txn(session, user_id, days_ago=0)

    await apply_classification(session, user_id=user_id, txn=new)

    assert new.category_id is None
    assert new.category_auto_assigned is False


async def test_no_history_no_hint_is_noop(db_with_user):
    session, user_id = db_with_user
    new = await _txn(session, user_id, merchant="Tienda Nueva", days_ago=0)

    applied = await apply_classification(session, user_id=user_id, txn=new)

    assert new.category_id is None
    assert new.envelope_id is None
    assert applied.category_id is None and applied.envelope_id is None


# ── hint (free-text category) ───────────────────────────────────────────────


async def test_hint_links_fk_without_auto_flag(db_with_user):
    session, user_id = db_with_user
    cat = await _category(session, user_id, name="Comida")
    # Client-provided free text names an existing category.
    new = await _txn(session, user_id, category="comida", days_ago=0)

    await apply_classification(session, user_id=user_id, txn=new)

    assert new.category_id == cat.id
    # Dual-field reconciliation — the user-provided name is not an auto guess.
    assert new.category_auto_assigned is False


async def test_provided_text_wins_over_history_category(db_with_user):
    session, user_id = db_with_user
    hist_cat = await _category(session, user_id, name="Servicios")
    hint_cat = await _category(session, user_id, name="Comida")
    await _txn(
        session,
        user_id,
        category="Servicios",
        category_id=hist_cat.id,
        days_ago=3,
    )
    # New row carries free text that matches a DIFFERENT existing category.
    new = await _txn(session, user_id, category="comida", days_ago=0)

    await apply_classification(session, user_id=user_id, txn=new)

    assert new.category_id == hint_cat.id
    assert new.category_auto_assigned is False


# ── sign filter + envelope expense-only ─────────────────────────────────────


async def test_income_never_gets_envelope_and_ignores_expense_history(
    db_with_user,
):
    session, user_id = db_with_user
    cat = await _category(session, user_id, name="Comida")
    env = await _envelope(session, user_id)
    # Expense precedent for this merchant.
    await _txn(
        session,
        user_id,
        amount=-5000,
        category="Comida",
        category_id=cat.id,
        envelope_id=env.id,
        days_ago=3,
    )
    # An INCOME row with the same merchant text.
    inc = await _txn(session, user_id, amount=8000, days_ago=0)

    await apply_classification(session, user_id=user_id, txn=inc)

    # Expense history must not cross the sign boundary, and income never gets
    # an envelope regardless.
    assert inc.category_id is None
    assert inc.envelope_id is None


# ── machine-managed exclusions ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"transfer_id": uuid.uuid4()},
        {"goal_id": uuid.uuid4()},
        {"source": "loan_cargo"},
        {"category": AJUSTE_CATEGORY},
        {"archived": True},
    ],
)
async def test_machine_managed_rows_not_classifiable(db_with_user, kwargs):
    session, user_id = db_with_user
    txn = Transaction(
        user_id=user_id,
        amount=-5000,
        currency="CRC",
        merchant="Auto Mercado",
        transaction_date=_TODAY,
        source=kwargs.get("source", "manual"),
        status="confirmed",
        transfer_id=kwargs.get("transfer_id"),
        goal_id=kwargs.get("goal_id"),
        category=kwargs.get("category"),
        archived=kwargs.get("archived", False),
    )
    assert is_classifiable(txn) is False


async def test_apply_skips_machine_managed(db_with_user):
    session, user_id = db_with_user
    cat = await _category(session, user_id)
    await _txn(
        session, user_id, category="Comida", category_id=cat.id, days_ago=3
    )
    # A loan-cargo row (machine-managed) with the same merchant must be left
    # untouched — it inherits its obligation's classification, not history.
    cargo = await _txn(session, user_id, source="loan_cargo", days_ago=0)

    await apply_classification(session, user_id=user_id, txn=cargo)

    assert cargo.category_id is None


# ── never overwrite an explicit client choice ───────────────────────────────


async def test_explicit_category_id_not_overwritten(db_with_user):
    session, user_id = db_with_user
    hist_cat = await _category(session, user_id, name="Servicios")
    chosen = await _category(session, user_id, name="Comida")
    await _txn(
        session,
        user_id,
        category="Servicios",
        category_id=hist_cat.id,
        days_ago=3,
    )
    new = await _txn(session, user_id, category_id=chosen.id, days_ago=0)

    await apply_classification(session, user_id=user_id, txn=new)

    assert new.category_id == chosen.id  # client wins
    assert new.category_auto_assigned is False


async def test_explicit_envelope_not_overwritten(db_with_user):
    session, user_id = db_with_user
    env_hist = await _envelope(session, user_id, name="Súper")
    env_chosen = await _envelope(session, user_id, name="Ocio")
    cat = await _category(session, user_id)
    await _txn(
        session,
        user_id,
        category="Comida",
        category_id=cat.id,
        envelope_id=env_hist.id,
        days_ago=3,
    )
    new = await _txn(session, user_id, envelope_id=env_chosen.id, days_ago=0)

    await apply_classification(session, user_id=user_id, txn=new)

    assert new.envelope_id == env_chosen.id  # client wins


# ── read-only + robustness ──────────────────────────────────────────────────


async def test_suggest_is_read_only(db_with_user):
    session, user_id = db_with_user
    cat = await _category(session, user_id)
    env = await _envelope(session, user_id)
    await _txn(
        session,
        user_id,
        category="Comida",
        category_id=cat.id,
        envelope_id=env.id,
        days_ago=3,
    )
    new = await _txn(session, user_id, days_ago=0)

    suggestion = await suggest_classification(
        session,
        user_id=user_id,
        merchant=new.merchant,
        amount=new.amount,
        exclude_id=new.id,
    )

    assert suggestion.category_id == cat.id
    assert suggestion.envelope_id == env.id
    # suggest() must not have mutated the row.
    assert new.category_id is None
    assert new.envelope_id is None


async def test_archived_envelope_suggestion_dropped(db_with_user):
    session, user_id = db_with_user
    cat = await _category(session, user_id)
    env = await _envelope(session, user_id)
    await _txn(
        session,
        user_id,
        category="Comida",
        category_id=cat.id,
        envelope_id=env.id,
        days_ago=3,
    )
    # The referenced envelope is archived after the precedent was set.
    env.archived = True
    await session.flush()
    new = await _txn(session, user_id, days_ago=0)

    await apply_classification(session, user_id=user_id, txn=new)

    # Category still lands (its FK is valid); the envelope is dropped because
    # it is no longer assignable.
    assert new.category_id == cat.id
    assert new.envelope_id is None
