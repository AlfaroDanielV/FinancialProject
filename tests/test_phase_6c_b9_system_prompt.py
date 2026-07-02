"""Phase 6c B9 — cache-safety invariants on the dispatcher system prompt.

These tests defend Decision #8 (insights NEVER inline in system prompt)
and the cache-hit rate that makes Sonnet usage affordable. If they fail,
the cache is at risk and so is the per-query cost budget.

Companion to test_system_prompt_builder.py — that file pins prose
content; this one pins cache invariants.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from api.models.user import User
from app.queries.prompts import build_system_prompt
from app.queries.prompts.system import (
    _CAPABILITIES,
    _CONVENTIONS,
    _FEW_SHOTS,
    _MEMORY_FEW_SHOT,
    _MEMORY_GUIDANCE,
    _RULES,
)


# Maximum allowed prompt length. Current build is ~8600 chars; this cap
# leaves headroom for a couple more few-shots before forcing review.
# Going above this should require an intentional bump + cache-rate check.
# Bumped 8500 → 8900 at Phase 7b B4 (card-analysis capability bullet).
# Bumped 8900 → 9100 at Phase 8 B4 (reallocation "¿de dónde muevo plata?" bullet).
# Bumped 9100 → 9500 at fix-pack B1 (fetch-registered-income routing: never ask
#   "¿cuánto ganás?" + debt-affordability → get_savings_capacity).
MAX_PROMPT_CHARS = 9500


def _user(
    *,
    user_id: uuid.UUID | None = None,
    full_name: str = "Daniel Alfaro",
) -> User:
    return User(
        id=user_id or uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        full_name=full_name,
        timezone="America/Costa_Rica",
        currency="CRC",
        locale="es-CR",
        shortcut_token="x" * 64,
    )


def _now_utc() -> datetime:
    return datetime(2026, 5, 8, 18, 0, tzinfo=timezone.utc)


# ── invariant 1: size cap ────────────────────────────────────────────────────


def test_prompt_size_within_cap():
    user = _user()
    prompt = build_system_prompt(user=user, now=_now_utc())
    assert len(prompt) <= MAX_PROMPT_CHARS, (
        f"system prompt grew to {len(prompt)} chars; cap is {MAX_PROMPT_CHARS}. "
        "Bumping the cap is OK if intentional, but verify cache-hit rate "
        "stays healthy after deploy (see docs/phase-6c/cache-verification.md)."
    )


# ── invariant 2: no per-user data leak beyond first name ────────────────────


def test_two_users_differ_only_in_first_name():
    """The Sonnet cache key is the prompt text. Anything per-user that's
    not the first name busts the cache for every other user."""
    now = _now_utc()
    user_a = _user(full_name="Daniel Alfaro")
    user_b = _user(full_name="Maria Sanchez")
    prompt_a = build_system_prompt(user=user_a, now=now)
    prompt_b = build_system_prompt(user=user_b, now=now)

    # Replace the first name back to a placeholder; the rest must match.
    normalized_a = prompt_a.replace("Daniel", "<NAME>", 1)
    normalized_b = prompt_b.replace("Maria", "<NAME>", 1)
    assert normalized_a == normalized_b, (
        "Prompt text differs between two users beyond the first-name token. "
        "Per-user content leaked into the system prompt — that breaks Decision #8 "
        "and busts the prompt cache."
    )

    # Defence in depth: the user IDs must NOT appear anywhere.
    assert str(user_a.id) not in prompt_a
    assert str(user_b.id) not in prompt_b
    # Last name and email must NOT leak either.
    assert "Alfaro" not in prompt_a
    assert "@example.com" not in prompt_a


# ── invariant 3: memory section content ─────────────────────────────────────


def test_memory_guidance_mentions_tool_and_negative_example():
    assert "get_user_context" in _MEMORY_GUIDANCE
    # Negative example surfaces in copy so the LLM has a concrete anchor.
    assert "cuánto gasté ayer" in _MEMORY_GUIDANCE


def test_memory_few_shot_calls_get_user_context():
    assert "get_user_context" in _MEMORY_FEW_SHOT
    # Both Example 6 (comparative) and Example 7 (recommendation) live here.
    assert "Ejemplo 6" in _MEMORY_FEW_SHOT
    assert "Ejemplo 7" in _MEMORY_FEW_SHOT


def test_capabilities_mentions_get_user_context():
    """The capabilities list must surface the memory tool so the LLM
    knows it exists without scrolling to the few-shots."""
    assert "get_user_context" in _CAPABILITIES


# ── invariant 4: insights are never embedded in the prompt ──────────────────


def test_prompt_contains_no_user_insight_payload_keys():
    """If a refactor accidentally inlines a user_insights row into the
    prompt, these key markers will appear. They must not."""
    prompt = build_system_prompt(user=_user(), now=_now_utc())
    forbidden = (
        '"raw_quote"',
        '"insight_type"',
        '"dedup_key"',
        '"valid_until"',
        '"reinforcement_count"',
        '"user_locked"',
    )
    for key in forbidden:
        assert key not in prompt, (
            f"prompt contains JSON-shaped insight payload key {key!r} — "
            "Decision #8 forbids inlining insights. Use the get_user_context "
            "tool instead."
        )


# ── invariant 5: stability for identical inputs (cache-hit precondition) ────


def test_prompt_stable_across_consecutive_same_day_builds():
    """build_system_prompt is the cache key. Same inputs → identical
    bytes → cache hit. If this regresses, the per-query Sonnet cost
    spikes."""
    user = _user()
    now = _now_utc()
    out1 = build_system_prompt(user=user, now=now)
    out2 = build_system_prompt(user=user, now=now)
    out3 = build_system_prompt(user=user, now=now)
    assert out1 == out2 == out3


def test_static_sections_have_no_format_placeholders():
    """Static sections are joined verbatim; if any of them still carry a
    format() placeholder, it's a sign someone half-converted a template
    and the prompt now contains literal `{...}` — bad signal to the LLM
    AND a cache-key landmine."""
    for label, section in (
        ("_CAPABILITIES", _CAPABILITIES),
        ("_RULES", _RULES),
        ("_CONVENTIONS", _CONVENTIONS),
        ("_MEMORY_GUIDANCE", _MEMORY_GUIDANCE),
    ):
        # Few-shots intentionally use {placeholders} as illustration.
        # These four sections should not.
        assert "{" not in section and "}" not in section, (
            f"{label} contains a brace; placeholders belong in few-shots only."
        )
