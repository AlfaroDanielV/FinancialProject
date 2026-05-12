"""Phase 6d B11 — full hybrid onboarding E2E.

These tests stay inside pytest because the repo does not yet ship a browser
runner. They still cross the real seams B11 cares about:

- Telegram /start handler mints a setup link.
- SPA auth exchange consumes that link and sets the session cookie.
- Cookie-authenticated API calls create the same entities the SPA creates.
- The bot write pipeline resolves a later transaction against that account.
- Lazy-only onboarding creates an account from chat and resumes the transaction.
"""
from __future__ import annotations

import gzip
import math
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select

from api.database import get_db
from api.main import app
from api.models.account import Account
from api.models.lazy_detection_event import LazyDetectionEvent
from api.models.user import User
from api.services.llm_extractor import FixtureLLMClient, RecordedLLMResponse
from bot import handlers, pipeline
from bot.account_creation import load_account_creation


ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"


@dataclass
class _FakeRedis:
    data: dict[str, str]
    ttls: dict[str, int]

    def __init__(self) -> None:
        self.data = {}
        self.ttls = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.data[key] = value
        self.ttls[key] = ttl

    async def delete(self, key: str):
        self.data.pop(key, None)
        self.ttls.pop(key, None)


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeFromUser:
    def __init__(self, tg_id: int, first_name: str = "Dani") -> None:
        self.id = tg_id
        self.first_name = first_name


class _FakeMessage:
    def __init__(self, from_user_id: int, first_name: str = "Dani") -> None:
        self.from_user = _FakeFromUser(from_user_id, first_name)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:
        self.answers.append((text, reply_markup))


@pytest.fixture
def allow_pipeline(monkeypatch: pytest.MonkeyPatch):
    async def _true(**kwargs):
        return True

    async def _none(**kwargs):
        return None

    def _noop_enqueue(**kwargs):
        return None

    monkeypatch.setattr(pipeline, "check_and_increment_rate", _true)
    monkeypatch.setattr(pipeline, "assert_within_budget", _none)
    monkeypatch.setattr(pipeline, "enqueue_insight_extraction", _noop_enqueue)


async def _user(session, user_id):
    user = await session.get(User, user_id)
    assert user is not None
    user.currency = "CRC"
    user.timezone = "America/Costa_Rica"
    return user


def _extract_setup_url(reply_markup) -> str:
    assert reply_markup is not None
    url = reply_markup.inline_keyboard[0][0].url
    assert url
    return url


def _extract_token(setup_url: str) -> str:
    parsed = urlparse(setup_url)
    token = parse_qs(parsed.query).get("token", [None])[0]
    assert token is not None
    return token


def _expense_extractor_client() -> FixtureLLMClient:
    return FixtureLLMClient(
        default=RecordedLLMResponse(
            tool_input={
                "intent": "log_expense",
                "dispatcher": "write",
                "amount": 5000,
                "currency": None,
                "merchant": "super",
                "category_hint": "supermercado",
                "account_hint": "BAC",
                "occurred_at_hint": None,
                "query_window": None,
                "confidence": 0.95,
                "raw_notes": None,
            }
        )
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = math.ceil(len(ordered) * 0.95) - 1
    return ordered[max(index, 0)]


async def _timed(
    timings: list[float],
    method,
    url: str,
    **kwargs,
) -> Response:
    start = time.perf_counter()
    response = await method(url, **kwargs)
    timings.append(time.perf_counter() - start)
    return response


async def _override_get_db(session):
    async def _yield_session():
        yield session

    app.dependency_overrides[get_db] = _yield_session


def _clear_overrides():
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_full_hybrid_onboarding_path_from_start_to_transaction(
    db_with_user,
    allow_pipeline,
    monkeypatch: pytest.MonkeyPatch,
):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    telegram_id = int(str(user_id.int)[-12:])
    user.telegram_user_id = telegram_id
    await session.commit()

    msg = _FakeMessage(from_user_id=telegram_id)

    async def fake_user_lookup(*, telegram_user_id, db):
        assert telegram_user_id == telegram_id
        return user

    with (
        monkeypatch.context() as m,
    ):
        m.setattr(handlers, "AsyncSessionLocal", lambda: _SessionContext(session))
        m.setattr(handlers, "user_by_telegram_id", fake_user_lookup)
        await handlers.on_start(msg, SimpleNamespace(args=None))

    assert len(msg.answers) == 1
    start_text, reply_markup = msg.answers[0]
    assert "registrá al menos una cuenta" in start_text
    setup_url = _extract_setup_url(reply_markup)
    token = _extract_token(setup_url)

    timings: list[float] = []
    await _override_get_db(session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exchange = await ac.post(
                "/api/v1/auth/magic-link/exchange",
                json={"token": token},
            )
            assert exchange.status_code == 200, exchange.text

            account = await _timed(
                timings,
                ac.post,
                "/api/v1/accounts",
                json={
                    "name": "BAC",
                    "account_type": "savings",
                    "currency": "CRC",
                    "initial_balance": "0",
                },
            )
            assert account.status_code == 201, account.text
            account_id = account.json()["id"]

            salary = await _timed(
                timings,
                ac.post,
                "/api/v1/recurring-incomes",
                json={
                    "name": "Salario",
                    "income_type": "salary",
                    "amount": "1000000",
                    "currency": "CRC",
                    "frequency": "monthly",
                    "next_payment_date": "2026-05-31",
                },
            )
            assert salary.status_code == 201, salary.text
            salary_id = salary.json()["id"]

            aguinaldo = await _timed(
                timings,
                ac.post,
                "/api/v1/recurring-incomes",
                json={
                    "name": "Aguinaldo",
                    "income_type": "aguinaldo",
                    "currency": "CRC",
                    "frequency": "annual",
                    "next_payment_date": "2026-12-15",
                    "base_salary_link_id": salary_id,
                },
            )
            assert aguinaldo.status_code == 201, aguinaldo.text
            assert Decimal(aguinaldo.json()["amount"]) == Decimal("1000000")

            debt = await _timed(
                timings,
                ac.post,
                "/api/v1/debts",
                json={
                    "name": "Hipoteca BAC",
                    "debt_type": "mortgage",
                    "lender": "BAC",
                    "original_amount": 10000000,
                    "current_balance": 10000000,
                    "interest_rate": 0.085,
                    "minimum_payment": 123985.37,
                    "payment_due_day": 15,
                    "term_months": 120,
                    "start_date": "2026-05-12",
                    "currency": "CRC",
                    "account_id": account_id,
                    "rate_type": "fixed",
                    "prepayment_penalty_pct": 0,
                    "payments_made": 2,
                    "includes_insurance": False,
                },
            )
            assert debt.status_code == 201, debt.text

            bill = await _timed(
                timings,
                ac.post,
                "/api/v1/recurring-bills",
                json={
                    "name": "ICE",
                    "provider": "ICE",
                    "category": "servicios",
                    "amount_expected": 25000,
                    "currency": "CRC",
                    "is_variable_amount": False,
                    "frequency": "monthly",
                    "day_of_month": 15,
                    "start_date": "2026-05-12",
                    "lead_time_days": 3,
                    "account_id": account_id,
                },
            )
            assert bill.status_code == 201, bill.text

            status = await _timed(timings, ac.get, "/api/v1/onboarding/status")
            assert status.status_code == 200, status.text
            assert status.json()["completeness_score"] == 1.0
    finally:
        _clear_overrides()

    backend_p95 = _p95(timings)
    assert backend_p95 < 0.500, (
        f"onboarding endpoint p95 exceeded budget: p95={backend_p95:.3f}s, "
        f"median={median(timings):.3f}s, samples={timings}"
    )

    redis = _FakeRedis()
    reply = await pipeline.process_message(
        user=user,
        text="gasté 5000 en el super con la BAC",
        db=session,
        redis=redis,
        llm_client=_expense_extractor_client(),
        llm_model="claude-haiku-4-5",
    )

    assert "No tengo registrada" not in reply.text
    assert "Gasto de ₡5.000 en super" in reply.text
    assert "cuenta BAC" in reply.text
    assert reply.buttons

    events = (
        await session.execute(
            select(LazyDetectionEvent)
            .where(LazyDetectionEvent.user_id == user_id)
            .order_by(LazyDetectionEvent.created_at.asc())
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].hint_text == "BAC"
    assert events[0].resolution == "linked_existing"


@pytest.mark.asyncio
async def test_lazy_only_onboarding_path_creates_account_and_resumes_transaction(
    db_with_user,
    allow_pipeline,
):
    session, user_id = db_with_user
    user = await _user(session, user_id)
    redis = _FakeRedis()

    first = await pipeline.process_message(
        user=user,
        text="gasté 5000 con la BAC",
        db=session,
        redis=redis,
        llm_client=_expense_extractor_client(),
        llm_model="claude-haiku-4-5",
    )
    assert "No tengo registrada una cuenta llamada BAC" in first.text

    state = await load_account_creation(user_id=user_id, redis=redis)
    assert state is not None
    assert state.step == "awaiting_start"
    assert state.name == "BAC"

    prompts = [
        await pipeline.process_message(
            user=user,
            text=text,
            db=session,
            redis=redis,
            llm_client=_expense_extractor_client(),
            llm_model="claude-haiku-4-5",
        )
        for text in ("crear", "ahorros", "CRC", "0")
    ]
    assert "La creo como BAC" in prompts[0].text
    assert "Voy a crear esta cuenta" in prompts[-1].text

    final = await pipeline.process_message(
        user=user,
        text="sí",
        db=session,
        redis=redis,
        llm_client=_expense_extractor_client(),
        llm_model="claude-haiku-4-5",
    )

    assert "Listo, creé la cuenta BAC." in final.text
    assert "Gasto de ₡5.000 en super" in final.text
    assert "cuenta BAC" in final.text
    assert final.buttons
    assert await load_account_creation(user_id=user_id, redis=redis) is None

    accounts = (
        await session.execute(select(Account).where(Account.user_id == user_id))
    ).scalars().all()
    assert [account.name for account in accounts] == ["BAC"]

    events = (
        await session.execute(
            select(LazyDetectionEvent)
            .where(LazyDetectionEvent.user_id == user_id)
            .order_by(LazyDetectionEvent.created_at.asc())
        )
    ).scalars().all()
    assert [event.resolution for event in events] == ["pending", "linked_existing"]


def test_spa_initial_build_transfer_budget_under_two_seconds_mobile_4g():
    subprocess.run(
        ["npm", "run", "build"],
        cwd=WEB_DIR,
        check=True,
        capture_output=True,
        text=True,
    )

    index_html = (WEB_DIR / "dist" / "index.html").read_text()
    assets = re.findall(r'(?:src|href)="/(assets/[^"]+)"', index_html)
    assert assets, "Vite build did not emit JS/CSS entry assets"

    compressed_bytes = len(gzip.compress(index_html.encode("utf-8"), compresslevel=9))
    for asset in assets:
        payload = (WEB_DIR / "dist" / asset).read_bytes()
        compressed_bytes += len(gzip.compress(payload, compresslevel=9))

    # Slow 4G approximation: 1.6 Mbps throughput plus one 300 ms RTT for
    # initial HTML and one multiplexed asset round-trip. Real static hosting
    # gzip/brotli compresses these files; gzip is the conservative local proxy.
    slow_4g_bytes_per_second = 1_600_000 / 8
    estimated_seconds = (compressed_bytes / slow_4g_bytes_per_second) + 0.600

    assert estimated_seconds < 2.0, (
        "SPA initial load exceeds B11 budget: "
        f"{compressed_bytes} gzip bytes -> {estimated_seconds:.2f}s"
    )
