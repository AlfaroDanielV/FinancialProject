"""Low-cost Gmail sender discovery for onboarding.

Discovery only reads Gmail metadata headers. It never calls the Haiku
transaction extractor and never reads message bodies.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...database import AsyncSessionLocal
from ...models.gmail_discovery_run import GmailDiscoveryRun
from ...redis_client import get_redis
from .scanner import _RevokedError, _ScannerRetryExhausted, _gmail_get
from .scanner import _resolve_access_token as _scanner_resolve_access_token


log = logging.getLogger("api.services.gmail.discovery")


class DiscoveryRateLimited(Exception):
    def __init__(self, ttl_s: int) -> None:
        super().__init__("gmail discovery cooldown active")
        self.ttl_s = ttl_s


class SenderCandidate(BaseModel):
    email: str
    count: int
    sample_subjects: list[str] = Field(default_factory=list)


class DiscoveryResult(BaseModel):
    senders: list[SenderCandidate]
    total_messages_scanned: int
    keywords: list[str]
    started_at: datetime
    finished_at: datetime
    run_id: uuid.UUID | None = None


def normalize_keywords(keywords: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in keywords:
        kw = " ".join(str(raw).strip().split())
        if not kw:
            continue
        key = kw.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(kw)
        if len(out) >= 5:
            break
    return out


def build_discovery_query(keywords: list[str], *, days: int) -> str:
    kws = normalize_keywords(keywords)
    if len(kws) == 1 and "@" in kws[0]:
        return f"from:{kws[0]} newer_than:{days}d"

    escaped = [_quote_kw(kw) for kw in kws]
    joined = " OR ".join(escaped)
    subject = f"subject:({joined})"
    body = " OR ".join(escaped)
    return f"(in:inbox OR in:spam) ({subject} OR {body}) newer_than:{days}d"


def _quote_kw(value: str) -> str:
    if any(ch.isspace() for ch in value):
        return f'"{value}"'
    return value


async def _check_rate_limit(
    *, user_id: uuid.UUID, redis: Redis, cooldown_s: int
) -> None:
    key = f"gmail_discovery_cooldown:{user_id}"
    was_set = await redis.set(key, "1", ex=cooldown_s, nx=True)
    if was_set:
        return
    ttl = await redis.ttl(key)
    raise DiscoveryRateLimited(ttl_s=max(1, ttl if ttl > 0 else cooldown_s))


async def _list_message_ids_capped(
    *,
    http: httpx.AsyncClient,
    access_token: str,
    query: str,
    max_messages: int,
) -> list[str]:
    out: list[str] = []
    page_token: Optional[str] = None
    while len(out) < max_messages:
        remaining = max_messages - len(out)
        params: dict[str, Any] = {
            "q": query,
            "maxResults": min(100, remaining),
        }
        if page_token:
            params["pageToken"] = page_token
        body = await _gmail_get(
            http=http, access_token=access_token, path="/messages", params=params
        )
        for msg in body.get("messages", []) or []:
            msg_id = msg.get("id")
            if msg_id:
                out.append(msg_id)
                if len(out) >= max_messages:
                    break
        page_token = body.get("nextPageToken")
        if not page_token:
            break
    return out


def _header_value(payload: dict[str, Any], name: str) -> str | None:
    name_low = name.lower()
    for header in payload.get("headers", []) or []:
        if str(header.get("name", "")).lower() == name_low:
            value = header.get("value")
            return str(value) if value is not None else None
    return None


def _sender_email(raw_from: str | None) -> str | None:
    if not raw_from:
        return None
    _display, email = parseaddr(raw_from)
    email = email.strip().lower()
    if "@" not in email:
        log.warning("gmail_discovery_bad_from raw=%r", raw_from)
        return None
    return email


async def discover_senders(
    user_id: uuid.UUID,
    keywords: list[str],
    days: int = 30,
    max_messages: int = 200,
    *,
    db: AsyncSession | None = None,
    http: httpx.AsyncClient | None = None,
    redis: Redis | None = None,
    access_token: str | None = None,
    enforce_cooldown: bool = True,
) -> DiscoveryResult:
    """Discover candidate sender emails matching keywords.

    Optional keyword-only dependencies exist for tests. Production callers use
    the four-argument public shape from the phase spec.
    """
    normalized = normalize_keywords(keywords)
    started = datetime.now(timezone.utc)
    if not normalized:
        finished = datetime.now(timezone.utc)
        return DiscoveryResult(
            senders=[],
            total_messages_scanned=0,
            keywords=[],
            started_at=started,
            finished_at=finished,
        )

    max_messages = max(1, min(max_messages, settings.gmail_discovery_max_messages))
    days = max(1, days)

    if enforce_cooldown:
        await _check_rate_limit(
            user_id=user_id,
            redis=redis or get_redis(),
            cooldown_s=settings.gmail_discovery_cooldown_s,
        )

    close_db = db is None
    close_http = http is None
    session = db or AsyncSessionLocal()
    client = http or httpx.AsyncClient(timeout=20.0)
    run: GmailDiscoveryRun | None = None

    try:
        token = access_token
        if token is None:
            token = await _scanner_resolve_access_token(user_id=user_id, db=session)
        if token is None:
            finished = datetime.now(timezone.utc)
            return await _persist_and_return(
                db=session,
                run=None,
                user_id=user_id,
                keywords=normalized,
                senders=[],
                total_scanned=0,
                started=started,
                finished=finished,
            )

        run = GmailDiscoveryRun(
            user_id=user_id,
            keywords=normalized,
            started_at=started,
            senders_found=[],
            senders_confirmed_as_bank=[],
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        query = build_discovery_query(normalized, days=days)
        try:
            message_ids = await _list_message_ids_capped(
                http=client,
                access_token=token,
                query=query,
                max_messages=max_messages,
            )
        except _RevokedError:
            log.warning("gmail_discovery_revoked user=%s", user_id)
            message_ids = []
        except _ScannerRetryExhausted:
            log.exception("gmail_discovery_list_failed user=%s", user_id)
            message_ids = []

        counts: Counter[str] = Counter()
        subjects: dict[str, list[str]] = defaultdict(list)
        scanned = 0
        for message_id in message_ids:
            try:
                body = await _gmail_get(
                    http=client,
                    access_token=token,
                    path=f"/messages/{message_id}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject"],
                    },
                )
            except (_RevokedError, _ScannerRetryExhausted):
                log.warning(
                    "gmail_discovery_metadata_failed user=%s message=%s",
                    user_id,
                    message_id,
                    exc_info=True,
                )
                continue

            scanned += 1
            payload = body.get("payload") or {}
            sender = _sender_email(_header_value(payload, "From"))
            if sender is None:
                continue
            counts[sender] += 1
            subject = (_header_value(payload, "Subject") or "").strip()
            if subject and subject not in subjects[sender] and len(subjects[sender]) < 3:
                subjects[sender].append(subject)

        candidates = [
            SenderCandidate(
                email=email,
                count=count,
                sample_subjects=subjects.get(email, [])[:3],
            )
            for email, count in counts.most_common(10)
        ]
        finished = datetime.now(timezone.utc)
        return await _persist_and_return(
            db=session,
            run=run,
            user_id=user_id,
            keywords=normalized,
            senders=candidates,
            total_scanned=scanned,
            started=started,
            finished=finished,
        )
    finally:
        if close_http:
            await client.aclose()
        if close_db:
            await session.close()


async def _persist_and_return(
    *,
    db: AsyncSession,
    run: GmailDiscoveryRun | None,
    user_id: uuid.UUID,
    keywords: list[str],
    senders: list[SenderCandidate],
    total_scanned: int,
    started: datetime,
    finished: datetime,
) -> DiscoveryResult:
    if run is None:
        run = GmailDiscoveryRun(
            user_id=user_id,
            keywords=keywords,
            started_at=started,
            senders_found=[],
            senders_confirmed_as_bank=[],
        )
        db.add(run)
    run.finished_at = finished
    run.senders_found = [s.model_dump() for s in senders]
    await db.commit()
    await db.refresh(run)
    return DiscoveryResult(
        senders=senders,
        total_messages_scanned=total_scanned,
        keywords=keywords,
        started_at=started,
        finished_at=finished,
        run_id=run.id,
    )


async def record_confirmed_senders(
    *,
    db: AsyncSession,
    run_id: uuid.UUID,
    confirmed: list[SenderCandidate],
) -> None:
    row = await db.get(GmailDiscoveryRun, run_id)
    if row is None:
        return
    row.senders_confirmed_as_bank = [s.model_dump() for s in confirmed]
    await db.flush()

