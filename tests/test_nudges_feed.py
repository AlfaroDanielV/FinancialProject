"""Native in-app nudge feed (pull surface).

The feed renders pending nudges to deterministic CR-voseo text from the
structured payload (no LLM on this path), drops actively-silenced types, and
never mutates a nudge. State changes still flow through /act and /dismiss.

Prereqs: `docker compose up -d db && alembic upgrade head`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.main import app
from api.models.user_nudge import UserNudge, UserNudgeSilence
from api.services.auth.magic_link import generate_link
from api.services.nudges.feed import build_feed, render_nudge_text
from api.services.nudges.policy import REASON_MANUAL_USER_REQUEST


_NOW = datetime(2026, 6, 15, 15, 0, tzinfo=timezone.utc)

_OVER_PAYLOAD = {
    "currency": "CRC",
    "monthly_income": "800000.00",
    "monthly_fixed_expenses": "400000.00",
    "monthly_debt_payments": "300000.00",
    "monthly_disposable": "100000.00",
    "committed_ratio_pct": 88,
    "threshold_pct": 85,
    "excluded_variable_bills": 0,
    "month_tag": "2026-06",
}


async def _add_over_commitment(session, user_id, *, status="pending") -> uuid.UUID:
    nudge = UserNudge(
        user_id=user_id,
        nudge_type="over_commitment",
        priority="normal",
        dedup_key=f"over_commitment:{user_id}:2026-06",
        payload=_OVER_PAYLOAD,
        status=status,
    )
    session.add(nudge)
    await session.commit()
    await session.refresh(nudge)
    return nudge.id


# ── deterministic rendering ──────────────────────────────────────────────────


def test_render_over_commitment_uses_real_numbers():
    text = render_nudge_text("over_commitment", _OVER_PAYLOAD)
    assert "88%" in text
    assert "₡100.000" in text  # CR thousands separator, from monthly_disposable
    assert "?" in text  # ends with a call to action


def test_render_usd_disposable_formats_with_dollar():
    text = render_nudge_text(
        "over_commitment",
        {**_OVER_PAYLOAD, "currency": "USD", "monthly_disposable": "250.00"},
    )
    assert "$250.00" in text


def test_render_unknown_type_has_safe_fallback():
    assert render_nudge_text("totally_unknown", {}) == "Tenés una notificación pendiente."


# ── build_feed ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_feed_returns_pending_with_text_and_buttons(db_with_user):
    session, user_id = db_with_user
    await _add_over_commitment(session, user_id)

    feed = await build_feed(session, user_id=user_id, now=_NOW)
    mine = [e for e in feed if e.nudge_type == "over_commitment"]

    assert len(mine) == 1
    entry = mine[0]
    assert "88%" in entry.text
    assert entry.priority == "normal"
    # buttons come from the delivery catalog: act / later / dismiss.
    verbs = {b.verb for b in entry.buttons}
    assert verbs == {"act", "later", "dismiss"}


@pytest.mark.asyncio
async def test_feed_hides_silenced_types(db_with_user):
    session, user_id = db_with_user
    await _add_over_commitment(session, user_id)
    session.add(
        UserNudgeSilence(
            user_id=user_id,
            nudge_type="over_commitment",
            silenced_until=_NOW + timedelta(days=7),
            reason=REASON_MANUAL_USER_REQUEST,
        )
    )
    await session.commit()

    feed = await build_feed(session, user_id=user_id, now=_NOW)
    assert [e for e in feed if e.nudge_type == "over_commitment"] == []


@pytest.mark.asyncio
async def test_feed_excludes_non_pending(db_with_user):
    session, user_id = db_with_user
    await _add_over_commitment(session, user_id, status="sent")

    feed = await build_feed(session, user_id=user_id, now=_NOW)
    assert [e for e in feed if e.nudge_type == "over_commitment"] == []


# ── endpoint + act flow ──────────────────────────────────────────────────────


def _override_db(session):
    async def _yield():
        yield session

    app.dependency_overrides[get_db] = _yield


async def _token(session, user_id) -> str:
    link = await generate_link(session, user_id=user_id, purpose="onboarding")
    _override_db(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/magic-link/exchange", json={"token": link.raw_token}
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.mark.asyncio
async def test_feed_endpoint_then_act_clears_it(db_with_user):
    session, user_id = db_with_user
    nudge_id = await _add_over_commitment(session, user_id)
    token = await _token(session, user_id)
    headers = {"Authorization": f"Bearer {token}"}

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            feed = await ac.get("/api/v1/nudges/feed", headers=headers)
            assert feed.status_code == 200, feed.text
            items = feed.json()["items"]
            mine = [i for i in items if i["id"] == str(nudge_id)]
            assert len(mine) == 1
            assert "88%" in mine[0]["text"]
            assert len(mine[0]["buttons"]) == 3

            # Acting on it removes it from the feed (status → acted_on).
            acted = await ac.post(
                f"/api/v1/nudges/{nudge_id}/act", headers=headers
            )
            assert acted.status_code == 200, acted.text

            feed2 = await ac.get("/api/v1/nudges/feed", headers=headers)
            assert [i for i in feed2.json()["items"] if i["id"] == str(nudge_id)] == []
    finally:
        app.dependency_overrides.pop(get_db, None)
