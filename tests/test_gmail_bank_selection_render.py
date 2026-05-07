"""Pure-function tests for the bank selection UI helpers."""
from __future__ import annotations

from bot.gmail_handlers import _bank_selection_kb, _bank_selection_text
from bot import messages_es
from api.data.bank_senders_cr import KNOWN_BANK_SENDERS_CR


def test_text_when_no_pending():
    assert _bank_selection_text([]) == messages_es.GMAIL_BANK_SELECTION_HEADER_EMPTY


def test_text_lists_pending_with_bank_names():
    pending = [
        {"email": "a@bac.cr", "bank_name": "BAC", "source": "preset_tap"},
        {"email": "b@x.com", "bank_name": None, "source": "custom_typed"},
    ]
    out = _bank_selection_text(pending)
    assert "a@bac.cr" in out
    assert "(BAC)" in out
    assert "b@x.com" in out


def test_text_appends_awaiting_footer_when_set():
    """Tap on BAC preset → footer reminding the user we're waiting for
    the BAC email. Empty list still shows the footer."""
    out = _bank_selection_text([], awaiting_bank="BAC")
    assert "Esperando" in out
    assert "BAC" in out


def test_text_no_awaiting_footer_when_not_set():
    out = _bank_selection_text(
        [{"email": "x@y.com", "bank_name": None, "source": "custom_typed"}]
    )
    assert "Esperando" not in out


def test_kb_includes_one_button_per_preset_bank():
    kb = _bank_selection_kb(mode="onboarding")
    flat = [b for row in kb.inline_keyboard for b in row]
    bank_buttons = [b for b in flat if b.callback_data and b.callback_data.startswith("bank_preset:")]
    assert len(bank_buttons) == len(KNOWN_BANK_SENDERS_CR)
    seen = {b.text for b in bank_buttons}
    for bank in KNOWN_BANK_SENDERS_CR:
        assert bank in seen


def test_kb_listo_callback_differs_by_mode():
    onboarding = _bank_selection_kb(mode="onboarding")
    add_bank = _bank_selection_kb(mode="add_bank")

    def find_listo(kb):
        for row in kb.inline_keyboard:
            for b in row:
                if b.text == messages_es.GMAIL_BANK_SELECTION_LISTO:
                    return b
        return None

    a = find_listo(onboarding)
    b = find_listo(add_bank)
    assert a is not None and b is not None
    assert a.callback_data == "bank_done"
    assert b.callback_data == "bank_done_addonly"


def test_kb_has_cancelar_button():
    kb = _bank_selection_kb(mode="onboarding")
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "bank_cancel" for b in flat)
