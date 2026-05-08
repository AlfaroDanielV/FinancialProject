from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.user import User
from api.models.user_insight import UserInsight
from api.schemas.insights import (
    RiskPostureContent,
    SpendingPatternContent,
    StatedGoalContent,
)
from app.queries import dispatcher
from app.queries.llm_client import AnthropicQueryClient
from app.queries.session import set_query_session_factory
from app.queries.tools import register_builtin_tools
from app.queries.tools.base import _reset_registry_for_tests, execute_tool


@dataclass
class _Usage:
    input_tokens: int = 5
    output_tokens: int = 4


@dataclass
class _ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list[Any]
    usage: _Usage = field(default_factory=_Usage)
    stop_reason: str = "end_turn"


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _Response(
                content=[
                    _ToolUse(
                        id="tool-1",
                        name="get_user_context",
                        input={
                            "insight_types": [
                                "spending_pattern",
                                "stated_goal",
                                "risk_posture",
                            ],
                            "max_insights": 5,
                        },
                    )
                ],
                stop_reason="tool_use",
            )
        return _Response(
            content=[
                _Text(
                    text=(
                        "Tus gastos en supermercado van más rápido que tu meta "
                        "de ahorro, y tu postura al riesgo es conservadora."
                    )
                )
            ]
        )


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        if len(self._rows) != 1:
            raise AssertionError(f"expected exactly one row, got {len(self._rows)}")
        return self._rows[0]


class _FakeSession:
    def __init__(self, user: User, insights: list[UserInsight]):
        self.user = user
        self.insights = insights
        self.dispatches: list[LLMQueryDispatch] = []

    async def get(self, model, ident):
        if model is User and ident == self.user.id:
            return self.user
        return None

    def add(self, row):
        if isinstance(row, LLMQueryDispatch) and row.id is None:
            row.id = uuid.uuid4()
            self.dispatches.append(row)
            return
        if isinstance(row, UserInsight):
            self.insights.append(row)

    async def commit(self):
        return None

    async def refresh(self, row):
        return None

    async def rollback(self):
        return None

    async def execute(self, stmt):
        entity = None
        if getattr(stmt, "column_descriptions", None):
            entity = stmt.column_descriptions[0].get("entity")
        if entity is UserInsight:
            rows = self._filter_user_insights(stmt)
            return _Result(rows)
        if entity is LLMQueryDispatch:
            rows = [row for row in self.dispatches]
            return _Result(rows)
        raise AssertionError(f"unexpected statement entity: {entity!r}")

    def _filter_user_insights(self, stmt):
        rows = list(self.insights)
        filters = {"insight_type_in": None, "user_id": None, "valid_after": None}
        for criterion in getattr(stmt, "_where_criteria", ()):
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            column = getattr(left, "name", None)
            operator_name = getattr(getattr(criterion, "operator", None), "__name__", "")
            value = getattr(right, "value", None)
            if column == "user_id" and operator_name == "eq":
                filters["user_id"] = value
            elif column == "valid_until" and operator_name == "gt":
                filters["valid_after"] = value
            elif column == "insight_type" and operator_name == "in_op":
                filters["insight_type_in"] = list(value)

        if filters["user_id"] is not None:
            rows = [row for row in rows if row.user_id == filters["user_id"]]
        if filters["valid_after"] is not None:
            rows = [row for row in rows if row.valid_until > filters["valid_after"]]
        if filters["insight_type_in"] is not None:
            wanted = set(filters["insight_type_in"])
            rows = [row for row in rows if row.insight_type in wanted]

        rows.sort(key=lambda row: (row.confidence, row.updated_at), reverse=True)

        limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
        if limit is not None:
            rows = rows[: int(limit)]
        return rows


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


async def _noop(*args, **kwargs):
    return None


async def _empty_history(*args, **kwargs):
    return []


def _seed_insight(
    user_id: uuid.UUID,
    content,
    *,
    confidence: Decimal,
    valid_until: datetime,
    updated_at: datetime,
) -> UserInsight:
    return UserInsight(
        user_id=user_id,
        insight_type=content.type,
        content=content.model_dump(mode="json"),
        confidence=confidence,
        source="computed",
        valid_until=valid_until,
        user_locked=False,
        dedup_key=content.dedup_key(),
        reinforcement_count=1,
        created_at=updated_at,
        updated_at=updated_at,
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


@pytest.mark.asyncio
async def test_get_user_context_reads_active_rows_sorted_filtered_and_truncated():
    now = datetime.now(timezone.utc)
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"test-{uuid.uuid4().hex}@example.com",
        full_name="Test User",
        timezone="America/Costa_Rica",
        currency="CRC",
        locale="es-CR",
        shortcut_token="x" * 64,
    )
    insights = [
        _seed_insight(
            user_id,
            StatedGoalContent(
                goal_text="Ahorrar para el marchamo",
                target_amount=Decimal("500000"),
                target_date=date(2026, 12, 1),
                status="committed",
            ),
            confidence=Decimal("0.95"),
            valid_until=now + timedelta(days=30),
            updated_at=now - timedelta(days=2),
        ),
        _seed_insight(
            user_id,
            RiskPostureContent(posture="moderate", evidence_basis="mixed"),
            confidence=Decimal("0.80"),
            valid_until=now + timedelta(days=30),
            updated_at=now - timedelta(days=1),
        ),
        _seed_insight(
            user_id,
            SpendingPatternContent(
                category="supermercado",
                monthly_avg_crc=Decimal("180000"),
                monthly_avg_usd=None,
                trend_pct_3mo=Decimal("8.0"),
                volatility_score=Decimal("0.32"),
            ),
            confidence=Decimal("0.80"),
            valid_until=now + timedelta(days=30),
            updated_at=now - timedelta(days=3),
        ),
        _seed_insight(
            user_id,
            SpendingPatternContent(
                category="transporte",
                monthly_avg_crc=Decimal("120000"),
                monthly_avg_usd=None,
                trend_pct_3mo=Decimal("-2.0"),
                volatility_score=Decimal("0.15"),
            ),
            confidence=Decimal("0.70"),
            valid_until=now - timedelta(days=1),
            updated_at=now - timedelta(days=4),
        ),
    ]
    fake_session = _FakeSession(user=user, insights=insights)
    set_query_session_factory(lambda: _SessionContext(fake_session))
    register_builtin_tools()
    try:
        result = await execute_tool(
            "get_user_context",
            {
                "insight_types": [
                    "spending_pattern",
                    "stated_goal",
                    "risk_posture",
                ],
                "max_insights": 2,
            },
            user_id,
        )
    finally:
        set_query_session_factory(None)

    lines = result.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("- [stated_goal] Tu meta es Ahorrar para el marchamo")
    assert "₡500.000" in lines[0]
    assert lines[1].startswith("- [risk_posture] Tu postura al riesgo parece moderate")
    assert "cash_flow_stability" not in result
    assert "transporte" not in result
    assert "No tengo memoria activa" not in result


@pytest.mark.asyncio
async def test_dispatcher_uses_get_user_context_tool_and_persists_usage(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"query-{uuid.uuid4().hex}@example.com",
        full_name="Test User",
        timezone="America/Costa_Rica",
        currency="CRC",
        locale="es-CR",
        shortcut_token="x" * 64,
    )
    insights = [
        _seed_insight(
            user_id,
            SpendingPatternContent(
                category="supermercado",
                monthly_avg_crc=Decimal("180000"),
                monthly_avg_usd=None,
                trend_pct_3mo=Decimal("8.0"),
                volatility_score=Decimal("0.32"),
            ),
            confidence=Decimal("0.90"),
            valid_until=now + timedelta(days=30),
            updated_at=now - timedelta(days=1),
        ),
        _seed_insight(
            user_id,
            StatedGoalContent(
                goal_text="Ahorrar para el marchamo",
                target_amount=Decimal("500000"),
                target_date=date(2026, 12, 1),
                status="committed",
            ),
            confidence=Decimal("0.95"),
            valid_until=now + timedelta(days=30),
            updated_at=now - timedelta(days=2),
        ),
    ]
    fake_session = _FakeSession(user=user, insights=insights)
    set_query_session_factory(lambda: _SessionContext(fake_session))
    fake = _FakeAnthropic()
    monkeypatch.setattr(dispatcher, "assert_within_budget", _noop)
    monkeypatch.setattr(dispatcher, "get_redis", lambda: object())
    monkeypatch.setattr(dispatcher, "load_history", _empty_history)
    monkeypatch.setattr(dispatcher, "append_turn", _empty_history)
    dispatcher.set_query_llm_client(
        AnthropicQueryClient(api_key="", anthropic_client=fake)
    )
    try:
        response = await dispatcher.handle(
            user_id=user_id,
            message_text="qué pensás de mis gastos vs mis metas",
            telegram_chat_id=123,
        )
    finally:
        dispatcher.set_query_llm_client(None)
        set_query_session_factory(None)

    assert "supermercado" in response
    assert "meta de ahorro" in response
    assert fake_session.dispatches, "dispatcher should have persisted a query row"
    row = fake_session.dispatches[-1]
    assert row.tools_used[0]["name"] == "get_user_context"
    assert row.tools_used[0]["args_summary"] == {
        "insight_types": [
            "spending_pattern",
            "stated_goal",
            "risk_posture",
        ],
        "max_insights": 5,
    }
