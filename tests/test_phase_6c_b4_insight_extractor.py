from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.user_insight import UserInsight
from api.services.insights.extractor import (
    compact_transaction_context_from_tools,
    extract_insights,
    run_insight_extraction_job,
)
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from prompts.insight_extractor import TOOL_DEFINITION


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _client(tool_input: dict, *, input_tokens: int = 100, output_tokens: int = 20):
    return FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input=tool_input,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=40,
            cache_creation_input_tokens=10,
        )
    )


_CONVERSATION = [
    {"role": "user", "content": "Quiero bajar deuda antes de ahorrar más."},
    {"role": "assistant", "content": "Tiene sentido priorizar tarjetas caras."},
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_input", "expected_type"),
    [
        (
            {
                "insights": [
                    {
                        "type": "stated_preference",
                        "confidence": 0.82,
                        "content": {
                            "topic": "debt_payoff",
                            "stance": "prioriza pagar deuda antes que ahorrar",
                            "raw_quote": "Quiero bajar deuda antes de ahorrar más.",
                            "extracted_at": "2026-05-07T00:00:00Z",
                        },
                    }
                ]
            },
            "stated_preference",
        ),
        (
            {
                "insights": [
                    {
                        "type": "stated_preference",
                        "confidence": 0.78,
                        "content": {
                            "topic": "savings",
                            "stance": "quiere construir ahorro mensual fijo",
                            "raw_quote": "Quiero apartar algo todos los meses.",
                            "extracted_at": "2026-05-07T00:00:00Z",
                        },
                    }
                ]
            },
            "stated_preference",
        ),
        (
            {
                "insights": [
                    {
                        "type": "stated_goal",
                        "confidence": 0.84,
                        "content": {
                            "goal_text": "Ahorrar para el marchamo",
                            "target_amount": 500000,
                            "target_date": "2026-12-01",
                            "status": "committed",
                        },
                    }
                ]
            },
            "stated_goal",
        ),
        (
            {
                "insights": [
                    {
                        "type": "stated_goal",
                        "confidence": 0.70,
                        "content": {
                            "goal_text": "Armar fondo de emergencia",
                            "target_amount": None,
                            "target_date": None,
                            "status": "mentioned",
                        },
                    }
                ]
            },
            "stated_goal",
        ),
        (
            {
                "insights": [
                    {
                        "type": "risk_posture",
                        "confidence": 0.80,
                        "content": {
                            "posture": "conservative",
                            "evidence_basis": "stated",
                        },
                    }
                ]
            },
            "risk_posture",
        ),
        (
            {
                "insights": [
                    {
                        "type": "risk_posture",
                        "confidence": 0.74,
                        "content": {
                            "posture": "aggressive",
                            "evidence_basis": "stated",
                        },
                    }
                ]
            },
            "risk_posture",
        ),
        (
            {
                "insights": [
                    {
                        "type": "decision_style",
                        "confidence": 0.79,
                        "content": {
                            "style": "analytical",
                            "evidence": "pidió números, escenarios y supuestos",
                        },
                    }
                ]
            },
            "decision_style",
        ),
        (
            {
                "insights": [
                    {
                        "type": "decision_style",
                        "confidence": 0.68,
                        "content": {
                            "style": "avoidant",
                            "evidence": "pidió posponer una decisión financiera",
                        },
                    }
                ]
            },
            "decision_style",
        ),
        (
            {
                "insights": [
                    {
                        "type": "financial_literacy",
                        "confidence": 0.72,
                        "content": {
                            "level": "beginner",
                            "evidence": "pidió explicación simple de tasa efectiva",
                        },
                    }
                ]
            },
            "financial_literacy",
        ),
        (
            {
                "insights": [
                    {
                        "type": "archetype",
                        "confidence": 0.76,
                        "content": {
                            "primary": "debt_juggler",
                            "secondary": "aguinaldo_dependent",
                            "confidence": 0.76,
                            "evidence_summary": (
                                "menciona pagar una tarjeta con otra y depender "
                                "del aguinaldo"
                            ),
                        },
                    }
                ]
            },
            "archetype",
        ),
    ],
)
async def test_extract_insights_accepts_curated_fixture_shapes(
    tool_input, expected_type
):
    insights = await extract_insights(
        uuid.uuid4(),
        _CONVERSATION,
        transaction_context="sin agregados",
        client=_client(tool_input),
        model="claude-haiku-4-5",
    )

    assert len(insights) == 1
    assert insights[0].type == expected_type


@pytest.mark.asyncio
async def test_validation_failure_discards_entire_output():
    tool_input = {
        "insights": [
            {
                "type": "stated_goal",
                "confidence": 0.82,
                "content": {
                    "goal_text": "Ahorrar para vacaciones",
                    "target_amount": 300000,
                    "target_date": None,
                    "status": "mentioned",
                },
            },
            {
                "type": "stated_preference",
                "confidence": 0.80,
                "content": {
                    "topic": "savings",
                    "stance": "quiere ahorrar más",
                    # Missing raw_quote and extracted_at: whole batch is dirty.
                },
            },
        ]
    }

    insights = await extract_insights(
        uuid.uuid4(),
        _CONVERSATION,
        transaction_context=None,
        client=_client(tool_input),
        model="claude-haiku-4-5",
    )

    assert insights == []


@pytest.mark.asyncio
async def test_computed_type_from_llm_is_rejected():
    tool_input = {
        "insights": [
            {
                "type": "spending_pattern",
                "confidence": 0.80,
                "content": {
                    "category": "supermercado",
                    "monthly_avg_crc": 100000,
                    "monthly_avg_usd": None,
                    "trend_pct_3mo": 10,
                    "volatility_score": 0.2,
                },
            }
        ]
    }

    insights = await extract_insights(
        uuid.uuid4(),
        _CONVERSATION,
        transaction_context=None,
        client=_client(tool_input),
        model="claude-haiku-4-5",
    )

    assert insights == []


@pytest.mark.asyncio
async def test_extractor_job_persists_and_logs_cost(db_with_user):
    session, user_id = db_with_user
    origin = LLMQueryDispatch(
        user_id=user_id,
        message_hash="origin",
        tools_used=[],
    )
    session.add(origin)
    await session.commit()
    await session.refresh(origin)

    tool_input = {
        "insights": [
            {
                "type": "stated_preference",
                "confidence": 0.84,
                "content": {
                    "topic": "debt_payoff",
                    "stance": "prefiere bajar deuda agresivamente",
                    "raw_quote": "Quiero salir de tarjetas rápido.",
                    "extracted_at": "2026-05-07T00:00:00Z",
                },
            }
        ]
    }

    result = await run_insight_extraction_job(
        user_id=user_id,
        conversation_window=_CONVERSATION,
        transaction_context=compact_transaction_context_from_tools(
            [{"name": "aggregate_transactions", "args_summary": {"period": "month"}}]
        ),
        source_event="post_query",
        origin_dispatch_id=origin.id,
        client=_client(tool_input, input_tokens=1000, output_tokens=100),
        model="claude-haiku-4-5",
        session_factory=lambda: _SessionContext(session),
    )

    rows = (
        await session.execute(
            select(UserInsight).where(UserInsight.user_id == user_id)
        )
    ).scalars().all()
    run_row = await session.get(LLMQueryDispatch, result.run_id)
    linked_origin = await session.get(LLMQueryDispatch, origin.id)

    assert result.stats.created == 1
    assert rows[0].insight_type == "stated_preference"
    assert rows[0].confidence == Decimal("0.84")
    assert run_row is not None
    assert run_row.total_input_tokens == 1000
    assert run_row.total_output_tokens == 100
    assert run_row.estimated_cost_usd is not None
    assert run_row.estimated_cost_usd > 0
    assert run_row.tools_used[0]["name"] == "insight_extractor"
    assert linked_origin.extractor_run_id == result.run_id


def test_tool_schema_allows_only_llm_extractable_types():
    enum = TOOL_DEFINITION["input_schema"]["properties"]["insights"]["items"][
        "properties"
    ]["type"]["enum"]

    assert set(enum) == {
        "stated_preference",
        "stated_goal",
        "archetype",
        "risk_posture",
        "decision_style",
        "financial_literacy",
    }
    assert "spending_pattern" not in enum
