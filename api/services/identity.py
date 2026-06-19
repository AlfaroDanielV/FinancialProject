"""User identity matching for deterministic transaction-direction rules.

Used to decide whether a counterparty named on a transfer receipt (SINPE Móvil,
bank transfer, …) is the user themselves — so that a deterministic rule, not the
LLM, derives income vs expense vs internal transfer. Pure: no DB, no network, no
LLM. Reuses the identity the system already has (`users.full_name` +
`users.phone_number`).
"""
from __future__ import annotations

import re
import unicodedata

from api.models.user import User


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalize_name(s: str | None) -> str:
    """Lowercase, accent-stripped, punctuation-free, whitespace-collapsed."""
    if not s:
        return ""
    s = _strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return " ".join(s.split())


def _name_tokens(s: str | None) -> set[str]:
    # Drop single-char tokens (initials) so "J" doesn't match everything.
    return {t for t in normalize_name(s).split() if len(t) > 1}


def name_matches(candidate: str | None, reference: str | None) -> bool:
    """True when one name's meaningful tokens are a subset of the other's
    (order-independent, accent/case-insensitive). Requires ≥2 shared tokens so a
    single common surname ('Alfaro') can't false-positive — a receipt names a
    full legal name, and the user's `full_name` is a full name too.
    """
    cand = _name_tokens(candidate)
    ref = _name_tokens(reference)
    if len(cand) < 2 or len(ref) < 2:
        return False
    shorter, longer = (ref, cand) if len(ref) <= len(cand) else (cand, ref)
    return len(shorter) >= 2 and shorter.issubset(longer)


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def phone_matches(candidate: str | None, reference: str | None) -> bool:
    """CR numbers are 8 digits; compare the last 8 so country codes / formatting
    ('+506 8510-2997' vs '85102997') don't matter."""
    c = _digits(candidate)
    r = _digits(reference)
    if len(c) < 8 or len(r) < 8:
        return False
    return c[-8:] == r[-8:]


def is_user(
    user: User, *, name: str | None = None, phone: str | None = None
) -> bool:
    """True if the given counterparty name and/or phone identifies the user.
    Phone is the stronger signal and checked first; name corroborates / is the
    fallback when no phone is present on the receipt."""
    if phone and phone_matches(phone, user.phone_number):
        return True
    if name and name_matches(name, user.full_name):
        return True
    return False
