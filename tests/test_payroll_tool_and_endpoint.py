"""CR net-salary — query tool + REST endpoint wiring.

The math is covered in test_cr_salary.py; here we prove the chat tool and the
`POST /payroll/net-salary` endpoint return the structured breakdown verbatim and
surface errors cleanly.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from api.dependencies import current_user
from api.main import app
from app.queries.tools.salary import compute_net_salary_tool


def _override_user():
    class _StubUser:
        def __init__(self) -> None:
            self.id = uuid.uuid4()
            self.status = "active"
            self.currency = "CRC"

    app.dependency_overrides[current_user] = lambda: _StubUser()


def _clear():
    app.dependency_overrides.pop(current_user, None)


# ── query tool ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_tool_returns_structured_breakdown_verbatim():
    out = await compute_net_salary_tool(
        gross_monthly=1_500_000, year=2026, user_id=uuid.uuid4()
    )
    assert out["net_monthly"] == 1_271_700
    assert out["ccss"]["total"] == 162_450
    assert out["isr"]["tax"] == 65_850
    assert out["effective_rate"] == 0.1522
    # The per-tramo detail rides along for traceability.
    assert [t["tramo"] for t in out["isr"]["tramos"]] == [2, 3]


@pytest.mark.asyncio
async def test_query_tool_unconfigured_year_returns_error_not_raise():
    out = await compute_net_salary_tool(
        gross_monthly=1_000_000, year=2099, user_id=uuid.uuid4()
    )
    assert "error" in out
    assert "2099" in out["error"]


@pytest.mark.asyncio
async def test_query_tool_solidarista_passthrough():
    out = await compute_net_salary_tool(
        gross_monthly=1_500_000, year=2026, solidarista_pct=5, user_id=uuid.uuid4()
    )
    assert out["solidarista"] == 75_000
    assert out["net_monthly"] == 1_196_700


# ── REST endpoint ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_net_salary_endpoint_ok_and_errors():
    _override_user()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ok = await ac.post(
                "/api/v1/payroll/net-salary",
                json={"gross_monthly": 1_500_000, "year": 2026, "hijos": 2,
                      "conyuge": True},
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["isr"]["creditos_aplicados"] == 6_010
            assert body["net_monthly"] == 1_277_710
            assert body["year"] == 2026
            assert body["isr_base_mode"] == "gross"

            # Unconfigured year → 400 with the helpful message.
            bad_year = await ac.post(
                "/api/v1/payroll/net-salary",
                json={"gross_monthly": 1_000_000, "year": 2099},
            )
            assert bad_year.status_code == 400, bad_year.text

            # gross_monthly must be > 0 → Pydantic 422.
            bad_amount = await ac.post(
                "/api/v1/payroll/net-salary", json={"gross_monthly": 0}
            )
            assert bad_amount.status_code == 422, bad_amount.text
    finally:
        _clear()
