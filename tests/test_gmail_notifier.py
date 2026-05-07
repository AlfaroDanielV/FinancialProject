"""Tests for the Block C.1 notifier.

Covers branching of `notify_run_completed`, the shadow accumulator,
and `maybe_send_shadow_summary`. Uses the `db_with_user` fixture
(Postgres) for the SQL paths and patches `_send` so we don't need a
live bot.
"""
from __future__ import annotations

import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from api.config import settings
from api.models.gmail_credential import GmailCredential
from api.models.transaction import Transaction
from api.services.gmail import notifier as notifier_mod
from api.services.gmail.scanner import ScanResult


def _db_reachable() -> bool:
    try:
        url = urlparse(settings.database_url.replace("+asyncpg", ""))
        with socket.create_connection(
            (url.hostname or "localhost", url.port or 5432), timeout=0.5
        ):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(), reason="Postgres not reachable"
)


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def captured_sends(monkeypatch):
    """Patch notifier._send to record outbound messages without hitting
    the bot."""
    sent: list[tuple[int, str]] = []

    async def fake_send(*, chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr(notifier_mod, "_send", fake_send)
    return sent


async def _activate(db, user_id, *, days_ago: int):
    cred = GmailCredential(
        user_id=user_id,
        kv_secret_name=f"gmail-refresh-{user_id}",
        scopes=[],
        granted_at=datetime.now(timezone.utc) - timedelta(days=days_ago + 1),
        activated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db.add(cred)
    await db.commit()


async def _set_telegram_id(db, user_id, tg_id: int = 999_001):
    """Pair the test user with a fake Telegram id so the notifier can
    resolve a chat. No DB constraint blocks this."""
    from api.models.user import User
    from sqlalchemy import select

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one()
    user.telegram_user_id = tg_id
    await db.commit()


def _make_result(
    *,
    user_id,
    mode="backfill",
    scanned=0,
    matched=0,
    created=0,
    revoked=False,
    no_whitelist=False,
    created_ids=None,
):
    return ScanResult(
        user_id=user_id,
        mode=mode,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        messages_scanned=scanned,
        transactions_matched=matched,
        transactions_created=created,
        revoked=revoked,
        no_whitelist=no_whitelist,
        created_transaction_ids=list(created_ids or []),
    )


# ── notify_run_completed branches ────────────────────────────────────────────


async def test_revoked_sends_invalid_grant_message(
    db_with_user, captured_sends
):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=10)
    await _set_telegram_id(db, user_id)

    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(user_id=user_id, revoked=True),
        db=db,
    )
    assert len(captured_sends) == 1
    assert "desconectó" in captured_sends[0][1]


async def test_no_whitelist_sends_no_whitelist_message(
    db_with_user, captured_sends
):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=10)
    await _set_telegram_id(db, user_id)

    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(user_id=user_id, no_whitelist=True),
        db=db,
    )
    assert len(captured_sends) == 1
    assert "whitelist" in captured_sends[0][1]


async def test_zero_messages_backfill_sends_no_results(
    db_with_user, captured_sends
):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=10)
    await _set_telegram_id(db, user_id)

    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(user_id=user_id, mode="backfill", scanned=0),
        db=db,
    )
    assert len(captured_sends) == 1
    assert "30 días" in captured_sends[0][1]


async def test_zero_messages_daily_is_silent(db_with_user, captured_sends):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=10)
    await _set_telegram_id(db, user_id)

    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(user_id=user_id, mode="daily", scanned=0),
        db=db,
    )
    assert captured_sends == []


# ── shadow window accumulation ──────────────────────────────────────────────


async def test_shadow_window_accumulates_ids_in_redis(
    db_with_user, captured_sends, monkeypatch
):
    """In shadow window, the notifier appends IDs to the per-day Redis
    set AND sends the rolled-up "esta semana" message."""
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=0)  # in shadow
    await _set_telegram_id(db, user_id)

    # Capture appended IDs by patching _append_shadow_ids.
    appended: list[uuid.UUID] = []

    async def fake_append(*, user_id, transaction_ids, date_iso=None):
        appended.extend(transaction_ids)

    monkeypatch.setattr(notifier_mod, "_append_shadow_ids", fake_append)

    txn_ids = [uuid.uuid4(), uuid.uuid4()]
    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(
            user_id=user_id,
            mode="backfill",
            scanned=10,
            matched=2,
            created=2,
            created_ids=txn_ids,
        ),
        db=db,
    )

    assert sorted(appended) == sorted(txn_ids)
    assert len(captured_sends) == 1
    text = captured_sends[0][1]
    assert "modo sombra" in text
    assert "10 correos" in text


# ── outside shadow: per-transaction vs batch ─────────────────────────────────


async def test_outside_shadow_under_threshold_sends_per_transaction(
    db_with_user, captured_sends, monkeypatch
):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=10)  # outside shadow
    await _set_telegram_id(db, user_id)
    monkeypatch.setattr(settings, "gmail_batch_threshold", 5)

    txn_ids = []
    for amount, merchant in [
        (Decimal("-5000"), "Walmart"),
        (Decimal("-2500"), "Uber"),
    ]:
        t = Transaction(
            user_id=user_id,
            amount=amount,
            currency="CRC",
            merchant=merchant,
            transaction_date=datetime.now(timezone.utc).date(),
            source="gmail",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        txn_ids.append(t.id)

    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(
            user_id=user_id,
            mode="manual",
            scanned=2,
            created=2,
            created_ids=txn_ids,
        ),
        db=db,
    )
    assert len(captured_sends) == 2
    # Each message mentions one merchant.
    texts = " ".join(t for _, t in captured_sends)
    assert "Walmart" in texts
    assert "Uber" in texts
    assert "gasto" in texts


async def test_outside_shadow_over_threshold_sends_batch(
    db_with_user, captured_sends, monkeypatch
):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=10)
    await _set_telegram_id(db, user_id)
    monkeypatch.setattr(settings, "gmail_batch_threshold", 2)

    txn_ids = []
    for i in range(4):
        t = Transaction(
            user_id=user_id,
            amount=Decimal(f"-{1000 + i * 100}"),
            currency="CRC",
            merchant=f"Merch{i}",
            transaction_date=datetime.now(timezone.utc).date(),
            source="gmail",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        txn_ids.append(t.id)

    await notifier_mod.notify_run_completed(
        user_id=user_id,
        result=_make_result(
            user_id=user_id,
            mode="manual",
            scanned=4,
            created=4,
            created_ids=txn_ids,
        ),
        db=db,
    )
    # One aggregate message
    assert len(captured_sends) == 1
    text = captured_sends[0][1]
    assert "Encontré 4 transacciones" in text
    # Top 3 shown + tail of 1 more
    assert "y 1 más" in text


# ── maybe_send_shadow_summary ───────────────────────────────────────────────


async def test_shadow_summary_reads_yesterdays_ids_and_sends(
    db_with_user, captured_sends, monkeypatch
):
    from datetime import date

    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=2)  # in window
    await _set_telegram_id(db, user_id)

    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))
    # Seed 3 shadow transactions and an ids set as if the notifier had
    # accumulated them yesterday.
    txn_ids = []
    for amt, m in [
        (Decimal("-1000"), "A"),
        (Decimal("-2000"), "B"),
        (Decimal("-3000"), "C"),
    ]:
        t = Transaction(
            user_id=user_id,
            amount=amt,
            currency="CRC",
            merchant=m,
            transaction_date=yesterday,
            source="gmail",
            status="shadow",
        )
        db.add(t)
        await db.commit()
        await db.refresh(t)
        txn_ids.append(t.id)

    # Stub the redis read/clear.
    cleared: list = []

    async def fake_read(*, user_id, date_iso):
        return list(txn_ids)

    async def fake_clear(*, user_id, date_iso):
        cleared.append(date_iso)

    monkeypatch.setattr(notifier_mod, "_read_shadow_ids", fake_read)
    monkeypatch.setattr(notifier_mod, "_clear_shadow_ids", fake_clear)

    sent = await notifier_mod.maybe_send_shadow_summary(
        user_id=user_id, db=db, target_date=yesterday
    )
    assert sent is True
    assert len(captured_sends) == 1
    text = captured_sends[0][1]
    assert "3" in text
    assert "modo sombra" in text
    assert cleared == [yesterday.isoformat()]


async def test_shadow_summary_skips_when_outside_window(
    db_with_user, captured_sends
):
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=20)  # outside
    await _set_telegram_id(db, user_id)

    sent = await notifier_mod.maybe_send_shadow_summary(
        user_id=user_id, db=db
    )
    assert sent is False
    assert captured_sends == []


async def test_shadow_summary_filters_already_approved(
    db_with_user, captured_sends, monkeypatch
):
    """If the user approved mid-cycle, those rows have status='confirmed'.
    The summary must skip them and not send a message about confirmed rows."""
    db, user_id = db_with_user
    await _activate(db, user_id, days_ago=2)
    await _set_telegram_id(db, user_id)

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    # Two were approved; one is still shadow.
    confirmed = Transaction(
        user_id=user_id,
        amount=Decimal("-500"),
        currency="CRC",
        merchant="x",
        transaction_date=yesterday,
        source="gmail",
        status="confirmed",
    )
    shadow = Transaction(
        user_id=user_id,
        amount=Decimal("-700"),
        currency="CRC",
        merchant="y",
        transaction_date=yesterday,
        source="gmail",
        status="shadow",
    )
    db.add_all([confirmed, shadow])
    await db.commit()
    await db.refresh(confirmed)
    await db.refresh(shadow)

    async def fake_read(*, user_id, date_iso):
        return [confirmed.id, shadow.id]

    async def fake_clear(*, user_id, date_iso):
        pass

    monkeypatch.setattr(notifier_mod, "_read_shadow_ids", fake_read)
    monkeypatch.setattr(notifier_mod, "_clear_shadow_ids", fake_clear)

    sent = await notifier_mod.maybe_send_shadow_summary(
        user_id=user_id, db=db, target_date=yesterday
    )
    assert sent is True
    assert len(captured_sends) == 1
    # Only 1 (the still-shadow one) is mentioned in the count.
    assert "<b>1</b>" in captured_sends[0][1]
