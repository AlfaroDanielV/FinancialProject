"""Phase 6c B11 — `INSIGHTS_DISPATCHER_ENABLED` flag invariants.

These tests defend the 7-day shadow window: with the flag off, the
dispatcher behaves exactly as it did pre-B6. Tool registry omits
`get_user_context`; system prompt drops the memory section, the
capabilities bullet, and Examples 6/7 cleanly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from api.config import settings
from api.models.user import User
from app.queries.prompts import build_system_prompt
from app.queries.tools import register_builtin_tools
from app.queries.tools.base import _reset_registry_for_tests, is_tool_registered


def _user(*, full_name: str = "Daniel Alfaro") -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        full_name=full_name,
        timezone="America/Costa_Rica",
        currency="CRC",
        locale="es-CR",
        shortcut_token="x" * 64,
    )


def _now_utc() -> datetime:
    return datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def _flag_off():
    """Override the autouse conftest fixture to put the flag back to
    its production default for these tests."""
    previous = settings.insights_dispatcher_enabled
    settings.insights_dispatcher_enabled = False
    yield
    settings.insights_dispatcher_enabled = previous


@pytest.fixture
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


# ── tool registry ───────────────────────────────────────────────────────────


def test_register_builtin_tools_omits_user_context_when_flag_off(
    _flag_off, _clean_registry
):
    register_builtin_tools()
    assert not is_tool_registered("get_user_context")
    # Sanity: the rest of the tool set is still there.
    for name in (
        "list_transactions",
        "list_accounts",
        "compare_periods",
    ):
        assert is_tool_registered(name), name


def test_register_builtin_tools_includes_user_context_when_flag_on(
    _clean_registry,
):
    """The autouse fixture has the flag on by default. This is the
    post-shadow canonical state."""
    settings.insights_dispatcher_enabled = True
    register_builtin_tools()
    assert is_tool_registered("get_user_context")


# ── system prompt ───────────────────────────────────────────────────────────


def test_prompt_omits_memory_sections_when_flag_off(_flag_off):
    prompt = build_system_prompt(user=_user(), now=_now_utc())
    # Memory guidance section is gone.
    assert "Memoria del usuario:" not in prompt
    # Memory few-shots are gone.
    assert "Ejemplo 6" not in prompt
    assert "Ejemplo 7" not in prompt
    # Capabilities bullet about get_user_context is gone.
    assert "get_user_context" not in prompt


def test_prompt_includes_memory_sections_when_flag_on():
    """Default-on behavior under conftest. Confirms the flag-on prompt
    still carries everything B9 locked in."""
    settings.insights_dispatcher_enabled = True
    prompt = build_system_prompt(user=_user(), now=_now_utc())
    assert "Memoria del usuario:" in prompt
    assert "Ejemplo 6" in prompt
    assert "Ejemplo 7" in prompt
    assert "get_user_context" in prompt


def test_flag_off_prompt_smaller_than_flag_on_prompt(_flag_off):
    """The shadow-window prompt is shorter — fewer tokens, same coherence."""
    off_prompt = build_system_prompt(user=_user(), now=_now_utc())
    settings.insights_dispatcher_enabled = True
    on_prompt = build_system_prompt(user=_user(), now=_now_utc())
    assert len(off_prompt) < len(on_prompt)


def test_flag_off_prompt_stable_across_two_users(_flag_off):
    """Cache invariant from B9 still holds when the flag is off:
    different users produce identical prompts modulo first-name."""
    now = _now_utc()
    user_a = _user(full_name="Daniel Alfaro")
    user_b = _user(full_name="Maria Sanchez")
    pa = build_system_prompt(user=user_a, now=now).replace("Daniel", "<NAME>", 1)
    pb = build_system_prompt(user=user_b, now=now).replace("Maria", "<NAME>", 1)
    assert pa == pb


def test_flag_toggle_changes_prompt_byte_for_byte(_flag_off):
    """Flipping the flag should produce a different prompt — that's the
    one-time cache miss the deploy plan acknowledges."""
    user = _user()
    now = _now_utc()
    off_prompt = build_system_prompt(user=user, now=now)
    settings.insights_dispatcher_enabled = True
    on_prompt = build_system_prompt(user=user, now=now)
    assert off_prompt != on_prompt
