"""Deterministic duplicate-transaction detection.

The LLM never decides a duplicate — this package is the rule. It flags a
likely-duplicate expense at capture time, raises a `duplicate_transaction`
nudge through the existing Phase 5d rails (Telegram + in-app Alertas), and
resolves the user's keep/delete decision. "Rules decide; the LLM only phrases
the notification."
"""
from .duplicate_detector import (
    clear_duplicate_nudges_for_txn,
    find_likely_duplicate,
    flag_and_notify,
    resolve_duplicate,
)

__all__ = [
    "clear_duplicate_nudges_for_txn",
    "find_likely_duplicate",
    "flag_and_notify",
    "resolve_duplicate",
]
