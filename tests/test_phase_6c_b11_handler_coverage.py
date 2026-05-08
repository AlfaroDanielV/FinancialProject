"""Phase 6c B11 — coverage-driven tests for memory_handlers callback paths.

The B7 tests cover the happy path of each command. These hit the
callback branches and the menu/group renderers that B7 didn't exercise,
to push memory_handlers.py above the 80% bar.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from api.models.user_insight import UserInsight
from api.schemas.insights import (
    SpendingPatternContent,
    StatedGoalContent,
    StatedPreferenceContent,
)
from api.services.insights.persister import persist_insights
from bot import memory_handlers, messages_es


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


class _SessionPassthrough:
    """Real session under the hood, no-op close()."""

    def __init__(self, real) -> None:
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def close(self) -> None:
        return None


class _FakeFromUser:
    def __init__(self, tg_id: int) -> None:
        self.id = tg_id


class _FakeMessage:
    def __init__(self, from_user_id: int) -> None:
        self.from_user = _FakeFromUser(from_user_id)
        self.chat = MagicMock()
        self.chat.id = 99999
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text_: str, reply_markup=None, **kwargs) -> None:
        self.answers.append((text_, reply_markup))


class _FakeCallbackMessage:
    """Minimal CallbackQuery.message stand-in."""

    def __init__(self) -> None:
        self.answers: list[tuple[str, object]] = []
        self.reply_markup = MagicMock()

    async def answer(self, text_: str, reply_markup=None, **kwargs) -> None:
        self.answers.append((text_, reply_markup))

    async def edit_reply_markup(self, reply_markup=None) -> None:
        return None


class _FakeCallbackQuery:
    def __init__(self, *, from_user_id: int, data: str) -> None:
        self.from_user = _FakeFromUser(from_user_id)
        self.data = data
        self.message = _FakeCallbackMessage()
        self.answer_calls: list[tuple] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answer_calls.append((text, show_alert))


class _FakeUser:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id


class _Cmd:
    def __init__(self, args: str | None = None) -> None:
        self.args = args


async def _seed_three_insights(session, user_id):
    """One of each major group: patrones, metas, conozco."""
    contents = [
        SpendingPatternContent(
            category="supermercado",
            monthly_avg_crc=Decimal("180000"),
            monthly_avg_usd=None,
            trend_pct_3mo=Decimal("8.0"),
            volatility_score=Decimal("0.32"),
        ),
        StatedGoalContent(
            goal_text="Ahorrar para el marchamo",
            target_amount=Decimal("500000"),
            target_date=None,
            status="committed",
        ),
        StatedPreferenceContent(
            topic="savings",
            stance="quiere apartar plata todos los meses",
            raw_quote="quiero ahorrar todos los meses",
            extracted_at=_utc(2026, 5, 8),
        ),
    ]
    for c in contents:
        if hasattr(c, "raw_quote"):
            setattr(c, "_llm_confidence", Decimal("0.80"))
        source = "llm_extracted" if c.type in {"stated_goal", "stated_preference"} else "computed"
        await persist_insights(
            session,
            user_id=user_id,
            insights=[c],
            source=source,
            now=_utc(2026, 5, 8),
        )
    await session.commit()


# ── /memoria ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_memoria_renders_grouped_output(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)

    msg = _FakeMessage(from_user_id=42)
    fake_user = _FakeUser(user_id)

    async def fake_user_lookup(*, telegram_user_id, db):
        return fake_user

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_memoria(msg)

    assert msg.answers
    text_, _kb = msg.answers[-1]
    # Footer is always present.
    assert "/olvidar" in text_
    # All three groups rendered.
    assert "Tus metas" in text_
    assert "Cómo te conozco" in text_
    assert "Patrones" in text_


@pytest.mark.asyncio
async def test_on_memoria_empty_state_includes_footer(db_with_user):
    session, user_id = db_with_user
    msg = _FakeMessage(from_user_id=42)
    fake_user = _FakeUser(user_id)

    async def fake_user_lookup(**kwargs):
        return fake_user

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_memoria(msg)

    text_, _kb = msg.answers[-1]
    assert "Todavía no aprendí" in text_
    assert "/olvidar" in text_


@pytest.mark.asyncio
async def test_on_memoria_unpaired_user_pair_prompt(db_with_user):
    session, _user_id = db_with_user
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return None

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_memoria(msg)

    assert msg.answers == [(messages_es.PAIR_PROMPT, None)]


# ── /olvidar — three argument paths ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_olvidar_no_arg_with_data_shows_menu(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_olvidar(msg, _Cmd())

    assert msg.answers
    text_, kb = msg.answers[-1]
    assert text_ == messages_es.OLVIDAR_MENU_HEADER
    # Inline keyboard has at least the "Borrar todo" button.
    assert kb is not None


@pytest.mark.asyncio
async def test_olvidar_arg_todo_shows_first_confirm(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_olvidar(msg, _Cmd(args="todo"))

    text_, kb = msg.answers[-1]
    assert "Esto NO se deshace" in text_
    assert kb is not None


@pytest.mark.asyncio
async def test_olvidar_arg_group_alias_shows_group_view(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_olvidar(msg, _Cmd(args="metas"))

    text_, kb = msg.answers[-1]
    assert "Tus metas" in text_
    assert kb is not None


# ── /olvidar callbacks ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_mem_cancel_sends_cancelled_message():
    cb = _FakeCallbackQuery(from_user_id=42, data="mem:cancel")
    await memory_handlers.on_mem_cancel(cb)
    assert any(
        text_ == messages_es.OLVIDAR_CANCELLED for text_, _ in cb.message.answers
    )


@pytest.mark.asyncio
async def test_callback_mem_group_renders_group(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    cb = _FakeCallbackQuery(from_user_id=42, data="mem:grp:metas")

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_group(cb)

    assert cb.message.answers
    text_, kb = cb.message.answers[-1]
    assert "Tus metas" in text_


@pytest.mark.asyncio
async def test_callback_mem_olv_one_shows_then_executes_delete(db_with_user):
    """Two clicks behind one prefix — first shows confirm, second deletes."""
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    target = (
        await session.execute(
            select(UserInsight)
            .where(UserInsight.user_id == user_id)
            .where(UserInsight.insight_type == "stated_goal")
        )
    ).scalar_one()

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    # First click: show confirm.
    cb_show = _FakeCallbackQuery(
        from_user_id=42, data=f"mem:olv:one:{target.id}"
    )
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_olv_one(cb_show)
    show_text, kb = cb_show.message.answers[-1]
    assert "Voy a borrar" in show_text
    assert kb is not None

    # Second click: confirm yes → row deleted.
    cb_yes = _FakeCallbackQuery(
        from_user_id=42, data=f"mem:olv:one:yes:{target.id}"
    )
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_olv_one(cb_yes)

    done_text, _kb = cb_yes.message.answers[-1]
    assert done_text == messages_es.OLVIDAR_SINGLE_DONE

    # Verify deletion.
    remaining = (
        await session.execute(
            select(UserInsight).where(UserInsight.id == target.id)
        )
    ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_callback_mem_olv_one_unknown_id_soft_fails(db_with_user):
    session, user_id = db_with_user
    bogus = uuid.uuid4()
    cb = _FakeCallbackQuery(
        from_user_id=42, data=f"mem:olv:one:yes:{bogus}"
    )

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_olv_one(cb)

    text_, _kb = cb.message.answers[-1]
    assert text_ == messages_es.OLVIDAR_SINGLE_NOT_FOUND


@pytest.mark.asyncio
async def test_callback_mem_olv_all_three_step_flow_deletes_everything(
    db_with_user,
):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    # Step 0 → first confirm.
    cb0 = _FakeCallbackQuery(from_user_id=42, data="mem:olv:all:0")
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_olv_all(cb0)
    text0, _kb0 = cb0.message.answers[-1]
    assert "Esto NO se deshace" in text0

    # Step 1 → second confirm.
    cb1 = _FakeCallbackQuery(from_user_id=42, data="mem:olv:all:1")
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_olv_all(cb1)
    text1, _kb1 = cb1.message.answers[-1]
    assert text1 == messages_es.OLVIDAR_TODO_CONFIRM_2

    # Step 2 → execute.
    cb2 = _FakeCallbackQuery(from_user_id=42, data="mem:olv:all:2")
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_olv_all(cb2)
    text2, _kb2 = cb2.message.answers[-1]
    assert "Borré" in text2

    remaining = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalars().all()
    assert remaining == []


# ── /editar_memoria list + pick callback ────────────────────────────────────


@pytest.mark.asyncio
async def test_on_editar_memoria_lists_editable_insights(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_editar_memoria(msg)

    assert msg.answers
    text_, kb = msg.answers[-1]
    assert text_ == messages_es.EDITAR_MEMORIA_LIST_HEADER
    assert kb is not None


@pytest.mark.asyncio
async def test_on_editar_memoria_no_editable_replies_empty(db_with_user):
    """User with only computed (non-editable) insights gets the empty
    state, not the picker."""
    session, user_id = db_with_user
    content = SpendingPatternContent(
        category="supermercado",
        monthly_avg_crc=Decimal("180000"),
        monthly_avg_usd=None,
        trend_pct_3mo=Decimal("0"),
        volatility_score=Decimal("0.10"),
    )
    await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="computed",
        now=_utc(2026, 5, 8),
    )
    await session.commit()

    msg = _FakeMessage(from_user_id=42)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_editar_memoria(msg)

    text_, _kb = msg.answers[-1]
    assert text_ == messages_es.EDITAR_MEMORIA_EMPTY


@pytest.mark.asyncio
async def test_callback_mem_edit_pick_stashes_state_and_prompts(db_with_user):
    session, user_id = db_with_user
    await _seed_three_insights(session, user_id)
    target = (
        await session.execute(
            select(UserInsight)
            .where(UserInsight.user_id == user_id)
            .where(UserInsight.insight_type == "stated_goal")
        )
    ).scalar_one()

    redis_store: dict[str, str] = {}

    class _FakeRedis:
        async def get(self, key):
            return redis_store.get(key)

        async def setex(self, key, ttl, value):
            redis_store[key] = value

        async def delete(self, key):
            redis_store.pop(key, None)

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    cb = _FakeCallbackQuery(from_user_id=42, data=f"mem:edit:{target.id}")
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
        patch.object(memory_handlers, "get_redis", lambda: _FakeRedis()),
    ):
        await memory_handlers.on_mem_edit_pick(cb)

    # State stored.
    assert f"telegram:memory_edit:{user_id}" in redis_store
    text_, _kb = cb.message.answers[-1]
    assert text_ == messages_es.EDITAR_MEMORIA_PROMPT


# ── /editar_memoria — non-editable type rejects via not-found message ───────


@pytest.mark.asyncio
async def test_callback_mem_edit_pick_rejects_computed_type(db_with_user):
    session, user_id = db_with_user
    content = SpendingPatternContent(
        category="restaurantes",
        monthly_avg_crc=Decimal("90000"),
        monthly_avg_usd=None,
        trend_pct_3mo=Decimal("0"),
        volatility_score=Decimal("0.10"),
    )
    await persist_insights(
        session,
        user_id=user_id,
        insights=[content],
        source="computed",
        now=_utc(2026, 5, 8),
    )
    await session.commit()

    target = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalar_one()

    async def fake_user_lookup(**kwargs):
        return _FakeUser(user_id)

    cb = _FakeCallbackQuery(from_user_id=42, data=f"mem:edit:{target.id}")
    with (
        patch.object(
            memory_handlers,
            "AsyncSessionLocal",
            lambda: _SessionPassthrough(session),
        ),
        patch.object(memory_handlers, "user_by_telegram_id", fake_user_lookup),
    ):
        await memory_handlers.on_mem_edit_pick(cb)

    text_, _kb = cb.message.answers[-1]
    assert text_ == messages_es.OLVIDAR_SINGLE_NOT_FOUND
