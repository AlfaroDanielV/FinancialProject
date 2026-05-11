"""Curated Costa Rica bank directory for Gmail onboarding.

This is a visual entry point, not a sender source of truth. Users still
provide or confirm the exact sender email that their bank uses. The optional
``hint_pattern`` only warns about suspicious combinations; it never blocks.
"""
from __future__ import annotations

import re
from typing import Optional, TypedDict


class BankDirectoryEntry(TypedDict):
    name: str
    slug: str
    hint_pattern: str | None


BANK_DIRECTORY_CR: list[BankDirectoryEntry] = [
    {"name": "BAC Credomatic", "slug": "bac", "hint_pattern": r"(bac|credomatic)"},
    {"name": "Promerica", "slug": "promerica", "hint_pattern": r"promerica"},
    {"name": "BCR", "slug": "bcr", "hint_pattern": r"(bancobcr|bcr)"},
    {
        "name": "Banco Nacional",
        "slug": "bn",
        "hint_pattern": r"(bncr|bnonline|bn\.fi\.cr)",
    },
    {"name": "Davivienda", "slug": "davivienda", "hint_pattern": r"davivienda"},
    {"name": "Scotiabank", "slug": "scotiabank", "hint_pattern": r"scotiabank"},
    {"name": "Lafise", "slug": "lafise", "hint_pattern": r"lafise"},
    {
        "name": "Coopealianza",
        "slug": "coopealianza",
        "hint_pattern": r"coopealianza",
    },
    {"name": "Otro / no en la lista", "slug": "other", "hint_pattern": None},
]


def bank_by_slug(slug: str | None) -> BankDirectoryEntry | None:
    if not slug:
        return None
    for bank in BANK_DIRECTORY_CR:
        if bank["slug"] == slug:
            return bank
    return None


def bank_name_for_slug(slug: str | None) -> str | None:
    bank = bank_by_slug(slug)
    return bank["name"] if bank is not None else None


def match_bank_by_hint(sender_email: str, bank_slug: str | None) -> bool:
    """Return whether ``sender_email`` looks plausible for ``bank_slug``.

    ``None`` / unknown / no-pattern banks return True: no opinion. This helper
    protects against obvious typos without enforcing a brittle sender list.
    """
    bank = bank_by_slug(bank_slug)
    if bank is None:
        return True
    pattern = bank.get("hint_pattern")
    if not pattern:
        return True
    return re.search(pattern, sender_email.strip().lower()) is not None


def known_bank_slugs() -> set[str]:
    return {bank["slug"] for bank in BANK_DIRECTORY_CR}

