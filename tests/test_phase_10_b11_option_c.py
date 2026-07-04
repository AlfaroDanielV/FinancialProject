"""P10 B11 — Option C: structural "no invented number".

Locks: the context pack is built from the single-owner engines only; the
narrator gets ZERO tools; the Gate-D scorer traces every numeric token in the
narration back to the pack (small structural integers up to twelve are free);
a violation degrades to the deterministic template fallback (honest numbers,
never a fabricated verdict, never a bare error); the audit choke points
(llm_query_dispatches + advice_events) both fire; the pipeline routes to
run_advisory only when the Option C flag is on.

Prereqs: `docker compose up -d db redis` + `alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from api.config import settings
from app.queries.advisory.orchestrator import (
    ContextPack,
    Finding,
    build_context_pack,
    render_fallback,
    run_advisory,
    score_narration,
)

_NOW = datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)


def _pack(**over) -> ContextPack:
    base = dict(
        findings=(
            Finding("ingreso_mensual", "800000.00", "cashflow"),
            Finding("sobrante_mensual", "100000.00", "cashflow"),
            Finding("patrimonio_CRC", "-300000.00", "net_worth"),
        ),
        framing=(),
        financial_state="high_interest_debt",
        currency="CRC",
    )
    base.update(over)
    return ContextPack(**base)


# ── Gate-D scorer ─────────────────────────────────────────────────────────────


def test_scorer_passes_pack_numbers_in_any_format():
    narration = (
        "Tu ingreso es ₡800.000 y te sobran ₡100.000 al mes; "
        "tu patrimonio hoy es -₡300.000. Son 3 datos clave."
    )
    assert score_narration(narration, _pack(), now=_NOW) == []


def test_scorer_flags_fabricated_numbers():
    narration = "Con ₡800.000 de ingreso podrías ahorrar ₡250.000 al mes."
    violations = score_narration(narration, _pack(), now=_NOW)
    assert violations == ["250.000"]


def test_scorer_allows_small_structural_integers_and_year():
    narration = "En 3 pasos y de aquí a 2026 salís: primero el sobrante."
    assert score_narration(narration, _pack(), now=_NOW) == []


def test_scorer_flags_a_percentage_not_in_pack():
    narration = "Destiná el 35% de tu ingreso a la deuda."
    assert score_narration(narration, _pack(), now=_NOW) == ["35"]


# ── template fallback ─────────────────────────────────────────────────────────


def test_fallback_renders_honest_numbers_no_verdict():
    pack = _pack(
        findings=_pack().findings + (Finding("gate_reason", "no_budget", "cashflow"),)
    )
    text = render_fallback(pack)
    assert "800000.00" in text
    assert "100000.00" in text
    assert "armá tus sobres" in text
    # No manufactured optimism, no invented verdict.
    assert "todo va a estar bien" not in text.lower()


# ── pack building (single owners only) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_pack_composes_state_cashflow_networth_and_framing(db_with_user):
    from api.models.account import Account
    from api.models.user import User

    session, user_id = db_with_user
    session.add(
        Account(
            user_id=user_id, name="BAC", account_type="checking",
            currency="CRC", initial_balance=Decimal("250000"),
        )
    )
    await session.commit()
    user = await session.get(User, user_id)

    pack = await build_context_pack(session, user=user)
    labels = {f.label for f in pack.findings}
    assert "estado_financiero" in labels
    assert "sobrante_mensual" in labels
    assert "patrimonio_CRC" in labels
    assert pack.financial_state == "irregular_income_stress"  # fresh user
    # Matched principles ride as framing — ids only, never numbers.
    ids = [f["principle_id"] for f in pack.framing]
    assert "paradoja_esperanza_loteria" in ids


# ── run_advisory end-to-end with a fake narrator ──────────────────────────────


@dataclass
class _FakeNarrator:
    reply_text: str
    calls: list[dict] = field(default_factory=list)

    async def run_query_loop(self, **kwargs: Any):
        from app.queries.llm_client import QueryLLMResponse

        self.calls.append(kwargs)
        return QueryLLMResponse(
            text=self.reply_text, total_iterations=1, total_input_tokens=10,
            total_output_tokens=10, tools_used=[], duration_ms=5,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


@pytest.mark.asyncio
async def test_run_advisory_narrator_has_no_tools(db_with_user, monkeypatch):
    import app.queries.advisory.orchestrator as orch

    session, user_id = db_with_user
    fake = _FakeNarrator(reply_text="Sin cifras: primero registrá tu ingreso.")
    monkeypatch.setattr(orch, "get_query_llm_client", lambda: fake)

    outcome = await run_advisory(user_id=user_id, message_text="¿cómo planeo mi año?")
    assert outcome.text == "Sin cifras: primero registrá tu ingreso."
    assert fake.calls[0]["tools"] == []  # ← the structural guarantee
    assert fake.calls[0]["max_iterations"] == 1


@pytest.mark.asyncio
async def test_run_advisory_fabricated_number_degrades_to_fallback(
    db_with_user, monkeypatch
):
    import app.queries.advisory.orchestrator as orch
    from api.models.advice_event import AdviceEvent

    session, user_id = db_with_user
    fake = _FakeNarrator(
        reply_text="Podés ahorrar ₡999.999 al mes sin problema, ¡todo va a estar bien!"
    )
    monkeypatch.setattr(orch, "get_query_llm_client", lambda: fake)

    outcome = await run_advisory(user_id=user_id, message_text="¿me alcanza?")
    # The fabricated figure never reaches the user — honest numbers instead.
    assert "999.999" not in outcome.text
    assert "999999" not in outcome.text
    assert "números reales" in outcome.text

    rows = (
        await session.execute(
            select(AdviceEvent).where(
                AdviceEvent.user_id == user_id,
                AdviceEvent.kind == "advisory_assessment",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].inputs["fallback_used"] is True
    assert rows[0].inputs["option"] == "C"


@pytest.mark.asyncio
async def test_run_advisory_llm_crash_degrades_to_fallback(db_with_user, monkeypatch):
    import app.queries.advisory.orchestrator as orch

    session, user_id = db_with_user

    class _Boom:
        async def run_query_loop(self, **kwargs):
            raise RuntimeError("anthropic exploded")

    monkeypatch.setattr(orch, "get_query_llm_client", lambda: _Boom())
    outcome = await run_advisory(user_id=user_id, message_text="¿me alcanza?")
    assert "números reales" in outcome.text  # fallback, never a bare error


# ── pipeline routing flag ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_routes_to_option_c_when_flag_on(db_with_user, monkeypatch):
    from datetime import date

    from api.redis_client import get_redis
    from api.services.llm_extractor import ExtractionResult, Intent
    from app.queries.dispatcher import DispatchOutcome
    from bot import pipeline

    monkeypatch.setattr(settings, "advisory_persona_enabled", True)
    monkeypatch.setattr(settings, "advisory_option_c_enabled", True)

    called = {"advisory": False, "dispatch": False}

    async def _fake_run_advisory(**kwargs):
        called["advisory"] = True
        return DispatchOutcome(text="opción C")

    async def _fake_run_dispatch(**kwargs):
        called["dispatch"] = True
        return DispatchOutcome(text="opción A")

    import app.queries.advisory as advisory_pkg

    monkeypatch.setattr(advisory_pkg, "run_advisory", _fake_run_advisory)
    monkeypatch.setattr(pipeline, "run_dispatch", _fake_run_dispatch)

    class _U:
        id = uuid.uuid4()
        telegram_user_id = 42
        timezone = "America/Costa_Rica"

    extraction = ExtractionResult(intent=Intent.QUERY, dispatcher="query", confidence=0.9)
    reply = await pipeline._route_extraction(
        user=_U(), text="hacé un plan para comprar casa", extraction=extraction,
        today=date.today(), db=None, redis=get_redis(),
    )
    assert reply.text == "opción C"
    assert called["advisory"] and not called["dispatch"]

    # Transactional lookup: plain Option A path even with the flag on.
    called["advisory"] = False
    reply = await pipeline._route_extraction(
        user=_U(), text="cuánto gasté esta semana", extraction=extraction,
        today=date.today(), db=None, redis=get_redis(),
    )
    assert reply.text == "opción A"
    assert called["dispatch"] and not called["advisory"]
