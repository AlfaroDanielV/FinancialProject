"""Deterministic money-string → Decimal parsing for capture paths.

The Apple Pay App Intent (iOS) hands the backend a money string. iOS parses it
locale-aware in Swift and sends a canonical dot-decimal string, but we
re-validate here (defense in depth — never trust the client) and also tolerate
the Costa Rica locale form ("72.679,00" → 72679.00) so a less-than-perfect
client parse still lands cleanly.

This mirrors the numeric core of `bot/clarification.py::_parse_amount_es`. It is
kept as a small, isolated helper so the bot's clarification path stays untouched
(the bot also handles "mil"/"millones" multipliers, which a contactless tap
never produces). Decimal throughout — no float on this path.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

# Symbols/words stripped before the numeric parse. The currency itself is
# carried explicitly on the payload; this is only to be forgiving of a string
# that still has a symbol attached. ` ` is a non-breaking space.
_STRIP = ("₡", "$", "crc", "usd", "colones", "dólares", "dolares", " ", " ")


def parse_money_magnitude(raw: Optional[str]) -> Optional[Decimal]:
    """Parse a money string to a POSITIVE ``Decimal`` magnitude, or ``None``.

    Returns ``None`` on an empty / unparseable / non-positive value — the caller
    rejects it (no row written). CR locale: period thousands + comma decimal
    ("72.679,00" → 72679.00); also accepts a canonical "12345.67" or "12345".
    Sign is applied by the caller (a contactless purchase is always an expense).
    """
    if raw is None:
        return None
    t = str(raw).strip().lower()
    for sym in _STRIP:
        t = t.replace(sym, "")
    t = t.strip()
    if not t:
        return None

    # CR convention: "72.679,00" means 72679.00. Normalize before Decimal.
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        parts = t.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            t = parts[0] + "." + parts[1]
        else:
            t = t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")

    try:
        value = Decimal(t)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value
