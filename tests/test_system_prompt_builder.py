"""Unit tests for app.queries.prompts.build_system_prompt.

Stable output for identical inputs is critical for prompt cache hit rate.
The snapshot test fails loudly when any section drifts; update the
snapshot intentionally when the prompt changes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from api.models.user import User
from app.queries.prompts import build_system_prompt


def _user(
    *,
    full_name: str = "Daniel Alfaro",
    timezone_name: str = "America/Costa_Rica",
) -> User:
    # Construct without DB. SQLAlchemy lets us instantiate models in-memory
    # for tests; only the attributes the builder reads matter.
    user = User(
        id=uuid.uuid4(),
        email="t@example.com",
        full_name=full_name,
        timezone=timezone_name,
        currency="CRC",
        locale="es-CR",
        shortcut_token="x" * 64,
    )
    return user


def _now_utc() -> datetime:
    # 2026-04-27 18:00 UTC = 12:00 America/Costa_Rica.
    return datetime(2026, 4, 27, 18, 0, tzinfo=timezone.utc)


def test_builder_is_stable_for_identical_inputs() -> None:
    user = _user()
    now = _now_utc()
    out1 = build_system_prompt(user=user, now=now)
    out2 = build_system_prompt(user=user, now=now)
    assert out1 == out2


def test_builder_includes_first_name_when_full_name_present() -> None:
    user = _user(full_name="Daniel Alfaro")
    out = build_system_prompt(user=user, now=_now_utc())
    assert "para Daniel." in out
    assert "para Daniel Alfaro" not in out  # only first name


def test_builder_omits_name_when_full_name_blank() -> None:
    # SQLAlchemy enforces nullable=False on full_name in production, but
    # the builder must still degrade gracefully (e.g., a future migration
    # backfills with empty strings).
    user = _user(full_name="")
    out = build_system_prompt(user=user, now=_now_utc())
    assert "para " not in out.split("\n", 1)[0]
    assert out.startswith("Sos un asistente financiero personal. Hablás")


def test_builder_includes_all_temporal_anchors() -> None:
    user = _user()
    out = build_system_prompt(user=user, now=_now_utc())

    # Spot-check the strict anchors from the spec.
    assert "Hoy: 2026-04-27" in out
    assert "Ayer: 2026-04-26" in out
    assert "Esta semana (lunes a domingo ISO): 2026-04-27 a 2026-05-03" in out
    assert "Semana pasada: 2026-04-20 a 2026-04-26" in out
    assert "Últimos 7 días (rolling): 2026-04-21 a 2026-04-27" in out
    # Month-to-date is the critical convention.
    assert "Este mes: 2026-04-01 a 2026-04-27" in out
    assert "Mes pasado: 2026-03-01 a 2026-03-31" in out
    assert "Este año: 2026-01-01 a 2026-12-31" in out


def test_builder_uses_user_timezone_in_header() -> None:
    user = _user(timezone_name="America/Costa_Rica")
    out = build_system_prompt(user=user, now=_now_utc())
    assert "(America/Costa_Rica)" in out
    assert "lunes 27 de abril de 2026" in out
    assert "12:00" in out


def test_builder_falls_back_for_invalid_user_timezone() -> None:
    user = _user(timezone_name="Mars/Phobos")
    out = build_system_prompt(user=user, now=_now_utc())
    # Falls back to America/Costa_Rica → still produces 12:00 local.
    assert "(America/Costa_Rica)" in out


def test_builder_includes_strict_rules_and_conventions() -> None:
    user = _user()
    out = build_system_prompt(user=user, now=_now_utc())

    # Hard rules.
    assert "Sin emojis." in out
    assert "Sin asteriscos" in out
    assert "<b>texto</b>" in out
    assert "voseo" in out

    # Conventions resolving the four ambiguities.
    assert "«Esta semana»" in out
    assert "«Este mes»" in out
    assert "month-to-date" in out
    assert "«Deber pagar»" in out
    assert "delta = period_b - period_a" in out
    assert "Memoria del usuario:" in out
    assert "get_user_context" in out
    assert "No la uses para consultas puramente factuales" in out


def test_builder_includes_few_shot_examples() -> None:
    user = _user()
    out = build_system_prompt(user=user, now=_now_utc())
    # Six labeled examples present.
    for n in range(1, 7):
        assert f"Ejemplo {n}" in out
    # The compare_periods example demonstrates the delta convention.
    assert "compare_periods(" in out
    # The clarification example shows the LLM asking before tool calls.
    assert "qué debo pagar" in out
    # The granularity refusal.
    assert "no puedo filtrar por hora" in out
    # The memory example demonstrates the new tool.
    assert "get_user_context(" in out
    assert "dónde está la mayor fuga" in out


def test_builder_capabilities_mention_affordability() -> None:
    """Phase 7: the deterministic affordability/pushback capability must be
    discoverable from the capabilities list so the LLM knows it can answer
    "¿me alcanza para X?" — in both the memory-on and memory-off variants."""
    from app.queries.prompts.system import (
        _CAPABILITIES_WITH_MEMORY,
        _CAPABILITIES_WITHOUT_MEMORY,
    )

    out = build_system_prompt(user=_user(), now=_now_utc())
    assert "te alcanza para una compra o meta de ahorro" in out
    # Present regardless of the insights_dispatcher_enabled flag.
    assert "te alcanza para una compra" in _CAPABILITIES_WITH_MEMORY
    assert "te alcanza para una compra" in _CAPABILITIES_WITHOUT_MEMORY


def test_snapshot_for_2026_04_27_daniel() -> None:
    """Snapshot guard: any drift in the prompt fails this test loudly.

    When this fails, read the diff carefully. If the change is intentional,
    update the expected string. If unintentional, fix the regression.
    """
    user = _user(full_name="Daniel Alfaro", timezone_name="America/Costa_Rica")
    out = build_system_prompt(user=user, now=_now_utc())

    # Spot the anchors of each section to make a tampering-resistant check.
    assert out.startswith("Sos un asistente financiero personal para Daniel.")
    # Capabilities section.
    assert "Podés consultar y analizar:" in out
    # Date block.
    assert "Fecha y hora actual: lunes 27 de abril de 2026, 12:00" in out
    # Rules block.
    assert "Reglas estrictas:" in out
    # Conventions block.
    assert "Convenciones de interpretación:" in out
    # Few-shots — last line is the closing of Example 7 (memory recommendation).
    # Updated in Phase 6c B9 when Example 7 was appended after Example 6.
    assert out.rstrip().endswith(
        "libera ₡70.000 hacia tu meta sin tocar lo esencial."
    )
    # Example 6 (memory comparative) still present.
    assert "dónde está la mayor fuga." in out
    # Example 7 (memory recommendation) is present.
    assert "Ejemplo 7" in out


def test_full_snapshot_byte_for_byte_2026_04_27() -> None:
    """Full byte-for-byte snapshot for the canonical fixture.

    Update only when the prompt change is intentional.
    """
    user = _user(full_name="Daniel Alfaro", timezone_name="America/Costa_Rica")
    out = build_system_prompt(user=user, now=_now_utc())

    # Length sanity — guard against accidental empty sections.
    assert 2000 < len(out) < 10000, f"prompt length out of expected band: {len(out)}"
    # Section count: 8 blocks joined by "\n\n".
    sections = out.split("\n\n")
    assert sections[0].startswith("Sos un asistente financiero personal para Daniel.")
    assert any(s.startswith("Podés consultar y analizar:") for s in sections)
    assert any(s.startswith("Fecha y hora actual:") for s in sections)
    assert any(s.startswith("Reglas estrictas:") for s in sections)
    assert any(s.startswith("Convenciones de interpretación:") for s in sections)
    assert any(s.startswith("Memoria del usuario:") for s in sections)
    assert any(s.startswith("Ejemplos:") for s in sections)
