"""Tests for the bank presets data module."""
from __future__ import annotations

import pytest

from api.data.bank_senders_cr import (
    KNOWN_BANK_SENDERS_CR,
    infer_bank_from_email,
    preset_senders_for,
)


# ── infer_bank_from_email ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "email,expected_bank",
    [
        # Exact preset addresses
        ("notificaciones@bac.cr", "BAC"),
        ("serviciosbac@credomatic.com", "BAC"),
        ("notificaciones@promerica.fi.cr", "Promerica"),
        ("notificaciones@bancobcr.com", "BCR"),
        ("notificaciones@scotiabank.com", "Scotiabank"),
        # Different local-part on a known domain
        ("alerts@bac.cr", "BAC"),
        ("estado-cuenta@davivienda.cr", "Davivienda"),
        # Subdomain
        ("notif.alerts@notif.bac.cr", "BAC"),
        # Case insensitive
        ("Notificaciones@BAC.CR", "BAC"),
        # Whitespace
        ("  user@lafise.com  ", "Lafise"),
        # Trailing dot the user typed by accident
        ("user@bac.cr.", "BAC"),
        # Unknown domain
        ("anyone@gmail.com", None),
        ("user@otrobanco.com", None),
        # Malformed
        ("notanemail", None),
        ("@bac.cr", None),
        ("user@", None),
        ("", None),
    ],
)
def test_infer_bank_from_email(email, expected_bank):
    assert infer_bank_from_email(email) == expected_bank


# ── preset_senders_for ───────────────────────────────────────────────────────


def test_preset_senders_for_known_bank_returns_list():
    senders = preset_senders_for("BAC")
    assert "notificaciones@bac.cr" in senders
    assert "serviciosbac@credomatic.com" in senders


def test_preset_senders_for_is_case_insensitive():
    assert preset_senders_for("bac") == preset_senders_for("BAC")
    assert preset_senders_for("Bac") == preset_senders_for("BAC")


def test_preset_senders_for_unknown_returns_empty():
    assert preset_senders_for("HSBC") == []
    assert preset_senders_for("") == []


def test_preset_senders_for_returns_a_copy():
    """Mutating the returned list must NOT pollute the dict."""
    senders = preset_senders_for("BAC")
    senders.append("hax@evil.com")
    assert "hax@evil.com" not in KNOWN_BANK_SENDERS_CR["BAC"]


# ── shape sanity (guards changes to KNOWN_BANK_SENDERS_CR) ───────────────────


def test_every_bank_has_at_least_one_sender():
    for bank, senders in KNOWN_BANK_SENDERS_CR.items():
        assert isinstance(senders, list)
        assert len(senders) >= 1, f"bank {bank!r} has no senders"
        for s in senders:
            assert "@" in s, f"bank {bank!r} sender {s!r} is not an email"


def test_every_preset_sender_is_inferrable():
    """Round-trip: every email listed as a preset should be detected by
    `infer_bank_from_email`. Otherwise users who tap a preset and later
    edit will see inconsistent inference."""
    for bank, senders in KNOWN_BANK_SENDERS_CR.items():
        for sender in senders:
            inferred = infer_bank_from_email(sender)
            assert inferred == bank, (
                f"Sender {sender!r} of bank {bank!r} was inferred as "
                f"{inferred!r}. Add the domain to _DOMAIN_TO_BANK or "
                f"remove this sender."
            )
