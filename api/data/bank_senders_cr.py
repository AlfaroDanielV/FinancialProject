"""Curated list of Costa Rica bank notification senders.

⚠️  IMPORTANT: these are a **proposed initial list**. Each domain must be
    confirmed against actual emails Daniel (or the relevant beta tester)
    receives BEFORE promoting them through onboarding. Banks change their
    notification senders without warning, and an outdated entry silently
    breaks the scanner for that user.

If a bank sends from a different address, the user can add it via
`/agregar_banco` as a custom email — the bot will infer the bank from
the domain (e.g. `notif@new.bac.cr` → BAC) and persist it to the
whitelist with `source='custom_typed'`.

To add a bank to the preset list:
    1. Confirm the actual sender(s) against a real notification email.
    2. Add the entry below.
    3. Add the domain to `_DOMAIN_TO_BANK` if `infer_bank_from_email`
       should also pick it up for custom emails.
    4. Update the inline keyboard in `bot/gmail_handlers.py` if you want
       a preset button for the new bank — order in the dict here is
       irrelevant; the button labels are derived from the dict keys.
"""
from __future__ import annotations

from typing import Optional


# Bank → list of canonical sender emails. Order within each list does
# not matter — the scanner ORs them all in the Gmail query.
KNOWN_BANK_SENDERS_CR: dict[str, list[str]] = {
    "BAC": [
        "notificaciones@bac.cr",
        "serviciosbac@credomatic.com",
    ],
    "Promerica": [
        "notificaciones@promerica.fi.cr",
    ],
    "BCR": [
        "notificaciones@bancobcr.com",
    ],
    "BN": [
        "bnnotificaciones@bncr.fi.cr",
    ],
    "Davivienda": [
        "notificaciones@davivienda.cr",
    ],
    "Scotiabank": [
        "notificaciones@scotiabank.com",
    ],
    "Lafise": [
        "notificaciones@lafise.com",
    ],
    "Coopealianza": [
        "notificaciones@coopealianza.fi.cr",
    ],
}


# Domain → bank inference. Used by `infer_bank_from_email` when the
# user types a custom email. Multiple domains can map to the same bank
# (e.g. credomatic.com is the BAC parent group).
_DOMAIN_TO_BANK: dict[str, str] = {
    "bac.cr": "BAC",
    "credomatic.com": "BAC",
    "promerica.fi.cr": "Promerica",
    "bancobcr.com": "BCR",
    "bncr.fi.cr": "BN",
    "davivienda.cr": "Davivienda",
    "scotiabank.com": "Scotiabank",
    "lafise.com": "Lafise",
    "coopealianza.fi.cr": "Coopealianza",
}


def infer_bank_from_email(email: str) -> Optional[str]:
    """Best-effort bank inference from an email's domain.

    Returns the canonical bank name (matching a key in
    KNOWN_BANK_SENDERS_CR) or None if the domain isn't recognized.
    Lowercases input. Whitespace-tolerant.

    Subdomain handling: the function walks the domain from the right,
    so `notif.alerts.bac.cr` → BAC matches via `bac.cr`. Plain TLD
    (`bac` without `.cr`) returns None.
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().lower().partition("@")
    if not local or not domain:
        return None
    # Strip a trailing dot if the user typed `user@bac.cr.`
    domain = domain.rstrip(".")

    parts = domain.split(".")
    # Try progressively shorter suffixes: `notif.alerts.bac.cr` →
    # `notif.alerts.bac.cr` → `alerts.bac.cr` → `bac.cr`.
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        bank = _DOMAIN_TO_BANK.get(candidate)
        if bank is not None:
            return bank
    return None


def preset_senders_for(bank: str) -> list[str]:
    """Returns the senders associated with a preset bank, or [] if the
    bank name is unknown. Case-insensitive on the lookup."""
    if not bank:
        return []
    # Match dict keys case-insensitively. Banks in CR are short and
    # canonical-cased in the dict; users tapping a button can't typo this.
    for key, senders in KNOWN_BANK_SENDERS_CR.items():
        if key.casefold() == bank.casefold():
            return list(senders)
    return []
