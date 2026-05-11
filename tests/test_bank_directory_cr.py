"""Tests for the Costa Rica bank directory used by Gmail onboarding."""
from __future__ import annotations

from api.data.bank_directory_cr import (
    BANK_DIRECTORY_CR,
    bank_by_slug,
    bank_name_for_slug,
    match_bank_by_hint,
)


def test_directory_has_required_banks_and_other():
    slugs = {bank["slug"] for bank in BANK_DIRECTORY_CR}
    assert {
        "bac",
        "promerica",
        "bcr",
        "bn",
        "davivienda",
        "scotiabank",
        "lafise",
        "coopealianza",
        "other",
    } <= slugs


def test_bank_by_slug_and_name_lookup():
    assert bank_by_slug("bac")["name"] == "BAC Credomatic"
    assert bank_name_for_slug("promerica") == "Promerica"
    assert bank_by_slug("missing") is None
    assert bank_name_for_slug(None) is None


def test_match_bank_by_hint_warns_but_only_as_boolean():
    assert match_bank_by_hint("notificacion@notificacionesbaccr.com", "bac")
    assert match_bank_by_hint("servicios@credomatic.com", "bac")
    assert not match_bank_by_hint("alertas@otrobanco.com", "bac")
    assert match_bank_by_hint("alertas@otrobanco.com", "other")
    assert match_bank_by_hint("alertas@otrobanco.com", None)


def test_every_entry_has_stable_shape():
    seen = set()
    for bank in BANK_DIRECTORY_CR:
        assert bank["name"]
        assert bank["slug"]
        assert bank["slug"] not in seen
        seen.add(bank["slug"])
        pattern = bank["hint_pattern"]
        assert pattern is None or isinstance(pattern, str)

