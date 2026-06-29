"""Shared income rule: a positive amount on a credit account is a card
payment or refund — never income.

You don't earn income onto a credit card; a positive movement there reduces
what's owed (a payment) or reverses a charge (a refund). Counting it as income
inflates dashboards, cashflow, and the chat agent's answers. This single clause
is reused by every income aggregation so the rule lives in one place
("LLM extracts; rules decide" — deterministic, by `account_type`, not text).
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select

from ..models.account import Account
from ..models.transaction import Transaction


def not_card_payment_income(user_id: uuid.UUID):
    """A boolean clause TRUE for rows that may count as income — i.e. rows NOT
    sitting on one of the user's credit accounts. Combine with
    `Transaction.amount > 0` in the caller's income case/filter. Orphan rows
    (`account_id IS NULL`) count as income (they're not on a credit account).
    """
    credit_accounts = select(Account.id).where(
        Account.user_id == user_id,
        Account.account_type == "credit",
    )
    return or_(
        Transaction.account_id.is_(None),
        Transaction.account_id.not_in(credit_accounts),
    )
