"""Deterministic transfer-direction classification (SINPE Móvil & bank-transfer
receipts).

The LLM extracts the raw parties (sender/recipient name + phone) on a transfer
receipt but MUST NOT decide the direction — it cannot know which party is the
user. This rule compares those parties against the user's identity and returns
income / expense / internal / unknown. Pure: no DB, no LLM. Embodies the Phase
5b rule "the LLM extracts; the rules decide".
"""
from __future__ import annotations

from typing import Literal

from api.models.user import User
from api.services.identity import is_user
from api.services.llm_extractor import ExtractionResult

TransferDirection = Literal["income", "expense", "internal", "unknown"]


def classify_transfer_direction(
    extraction: ExtractionResult, user: User
) -> TransferDirection:
    """Compare the receipt's parties against the user.

    - recipient is the user, sender is a third party → ``income``
    - sender is the user, recipient is a third party → ``expense``
    - both are the user → ``internal`` (between own accounts)
    - neither side identifiable as the user → ``unknown`` (the dispatcher asks)
    """
    sender_is_user = is_user(
        user, name=extraction.sender_name, phone=extraction.sender_phone
    )
    recipient_is_user = is_user(
        user, name=extraction.recipient_name, phone=extraction.recipient_phone
    )

    if recipient_is_user and not sender_is_user:
        return "income"
    if sender_is_user and not recipient_is_user:
        return "expense"
    if sender_is_user and recipient_is_user:
        return "internal"
    return "unknown"
