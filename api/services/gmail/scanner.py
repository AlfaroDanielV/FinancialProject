"""Gmail inbox scanner — the heart of Block B.

Pulls a user's whitelisted bank notification emails for a date window,
dedupes against `gmail_messages_seen`, parses each body with the email
extractor, and hands the candidates to the reconciler.

Direct httpx calls to googleapis.com — no google-* SDK. See
docs/phase-6b-decisions.md (entry "Scanner: HTTP directo a Gmail").

Public surface:
    ScanResult        — Pydantic model returned by scan_user_inbox.
    scan_user_inbox   — the only entry point. Composes everything.

Error model:
    invalid_grant     — the user revoked from Google's side OR Daniel
                        removed them as a test user. We mark
                        gmail_credentials.revoked_at and stop. The
                        backfill runner sends them a Telegram nudge.
    rate_limited (429)→ exponential backoff up to 3 retries.
    server (5xx)      → linear backoff up to 2 retries.
    other             → log.exception, append to errors, continue.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...models.gmail_credential import GmailCredential
from ...models.gmail_ingestion_run import GmailIngestionRun
from ...models.gmail_message_seen import GmailMessageSeen
from ..extraction.email_extractor import (
    EmailExtractionError,
    extract_from_email_body,
)
from ..llm_extractor.client import AnthropicLLMClient, LLMClient
from ..secrets import get_secret_store, kv_name_for_user
from . import oauth as oauth_svc
from . import reconciler as reconcile_mod
from . import whitelist as wl_svc


log = logging.getLogger("api.services.gmail.scanner")


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
RunMode = Literal["backfill", "daily", "manual"]

# Min confidence to send a candidate to the reconciler. Below this we
# log `outcome='skipped'` without writing to transactions. This is a
# scanner-level gate; the reconciler has its own (stricter) gate at 0.7
# but we want to record very-low-confidence as `skipped` not call out to
# the reconciler unnecessarily.
_MIN_SCAN_CONFIDENCE = 0.6

# Retry policy. Tuned so the worst-case scan still finishes within the
# Container Apps Job's typical 30min wallclock.
_MAX_429_RETRIES = 3
_MAX_5XX_RETRIES = 2
_INITIAL_BACKOFF_S = 2.0


# ── result types ─────────────────────────────────────────────────────────────


class ScanResult(BaseModel):
    run_id: Optional[uuid.UUID] = None
    user_id: uuid.UUID
    mode: RunMode
    started_at: datetime
    finished_at: Optional[datetime] = None
    messages_scanned: int = 0
    transactions_created: int = 0
    transactions_matched: int = 0
    transactions_skipped: int = 0
    # Tracked separately so the notifier can decide between per-row
    # messages and a batch summary without re-querying the DB. UUIDs of
    # rows that the reconciler created (CREATED_NEW or CREATED_SHADOW).
    created_transaction_ids: list[uuid.UUID] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    revoked: bool = False
    no_whitelist: bool = False


@dataclass
class _MessageStub:
    id: str
    thread_id: Optional[str] = None


# ── access-token resolution ──────────────────────────────────────────────────


async def _resolve_access_token(
    *, user_id: uuid.UUID, db: AsyncSession
) -> Optional[str]:
    """Read the refresh token from KV/env, exchange for an access token,
    bump last_refresh_at. Returns None if the user has no live credential
    or the refresh failed with invalid_grant (and marks revoked_at)."""
    cred = (
        await db.execute(
            select(GmailCredential).where(GmailCredential.user_id == user_id)
        )
    ).scalar_one_or_none()
    if cred is None or cred.revoked_at is not None:
        return None

    store = get_secret_store()
    refresh_token = await store.get(cred.kv_secret_name)
    if not refresh_token:
        log.warning(
            "scan_no_refresh_token user=%s kv=%s — secret store returned None",
            user_id,
            cred.kv_secret_name,
        )
        return None

    try:
        access = await oauth_svc.refresh_access_token(refresh_token)
    except oauth_svc.OAuthExchangeError as e:
        if e.code == "invalid_grant":
            log.warning(
                "scan_invalid_grant user=%s — marking credential revoked",
                user_id,
            )
            cred.revoked_at = datetime.now(timezone.utc)
            await db.commit()
            return None
        raise

    cred.last_refresh_at = datetime.now(timezone.utc)
    await db.commit()
    return access.token


# ── Gmail query construction ────────────────────────────────────────────────


def _build_gmail_query(
    *, senders: Iterable[str], since: datetime, until: Optional[datetime]
) -> str:
    """Compose the `q=` parameter for messages.list.

    Gmail accepts `from:(a OR b)` for OR'd senders and Unix-timestamp
    `after:` / `before:`. Senders are wrapped individually because
    addresses with `+tags` need to round-trip cleanly.
    """
    sender_list = list(senders)
    if not sender_list:
        return ""
    senders_clause = " OR ".join(f"from:{s}" for s in sender_list)
    parts = [f"({senders_clause})", f"after:{int(since.timestamp())}"]
    if until is not None:
        parts.append(f"before:{int(until.timestamp())}")
    return " ".join(parts)


# ── HTTP plumbing with retry ────────────────────────────────────────────────


class _RevokedError(Exception):
    """Gmail returned 401 with invalid auth/grant — the calling user's
    credential is dead. Caller must mark revoked_at."""


class _ScannerRetryExhausted(Exception):
    """Retries exhausted; outer scan logs and keeps going on next msg."""

    def __init__(self, message: str, last_status: Optional[int] = None):
        super().__init__(message)
        self.last_status = last_status


async def _gmail_get(
    *,
    http: httpx.AsyncClient,
    access_token: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One Gmail GET with retry on 429 / 5xx and explicit invalid_grant
    detection. Returns the parsed JSON body. Raises on terminal failure."""
    headers = {"Authorization": f"Bearer {access_token}"}
    last_status: Optional[int] = None

    backoff = _INITIAL_BACKOFF_S
    attempt_429 = 0
    attempt_5xx = 0

    while True:
        resp = await http.get(
            f"{GMAIL_API_BASE}{path}", params=params, headers=headers
        )
        last_status = resp.status_code
        if resp.status_code == 200:
            return resp.json()

        # 401: bad/expired token. We always pass a fresh access_token so
        # this means the underlying refresh_token is dead. Surface as
        # _RevokedError so the outer scan can flip revoked_at.
        if resp.status_code == 401:
            raise _RevokedError("Gmail returned 401 with our access token")

        # 403 + reason=insufficientPermissions / userRateLimitExceeded
        # are also treated as rate-limited per Google's docs.
        if resp.status_code == 429 or (
            resp.status_code == 403
            and "userRateLimitExceeded" in resp.text
        ):
            if attempt_429 >= _MAX_429_RETRIES:
                raise _ScannerRetryExhausted(
                    "rate_limited: retries exhausted",
                    last_status=resp.status_code,
                )
            attempt_429 += 1
            log.warning(
                "gmail_429 path=%s retry=%d backoff=%.1fs",
                path,
                attempt_429,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
            continue

        if 500 <= resp.status_code < 600:
            if attempt_5xx >= _MAX_5XX_RETRIES:
                raise _ScannerRetryExhausted(
                    f"server_{resp.status_code}: retries exhausted",
                    last_status=resp.status_code,
                )
            attempt_5xx += 1
            log.warning(
                "gmail_5xx path=%s status=%d retry=%d",
                path,
                resp.status_code,
                attempt_5xx,
            )
            await asyncio.sleep(2.0 * attempt_5xx)
            continue

        # Other 4xx: not retryable.
        try:
            err_body = resp.json()
        except ValueError:
            err_body = {"raw": resp.text}
        raise _ScannerRetryExhausted(
            f"non_retryable: {resp.status_code} {err_body}",
            last_status=resp.status_code,
        )


# ── pagination over messages.list ───────────────────────────────────────────


async def _list_message_ids(
    *,
    http: httpx.AsyncClient,
    access_token: str,
    query: str,
    page_size: int = 100,
) -> list[_MessageStub]:
    """Walk all pages of messages.list, return flat list of stubs.
    Empty result is fine — many users will have zero matching emails."""
    out: list[_MessageStub] = []
    page_token: Optional[str] = None
    while True:
        params: dict[str, Any] = {"q": query, "maxResults": page_size}
        if page_token:
            params["pageToken"] = page_token
        body = await _gmail_get(
            http=http, access_token=access_token, path="/messages", params=params
        )
        for m in body.get("messages", []) or []:
            mid = m.get("id")
            if mid:
                out.append(_MessageStub(id=mid, thread_id=m.get("threadId")))
        page_token = body.get("nextPageToken")
        if not page_token:
            break
    return out


# ── MIME body extraction ────────────────────────────────────────────────────


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+\n")


def _b64url_decode(data: str) -> bytes:
    """Gmail uses base64url with no padding. Re-pad before decoding."""
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except (binascii.Error, ValueError):
        return b""


def _strip_html(html: str) -> str:
    """Crude tag stripper. Good enough for bank notifications, which are
    flat tables. We deliberately don't pull in beautifulsoup — adds 5MB
    to the install for one helper that breaks the same way on weird HTML."""
    text = _TAG_RE.sub(" ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail message payload and return the best plain-text body.
    Prefers text/plain; falls back to stripped text/html. Returns empty
    string if neither is present."""
    plain: Optional[str] = None
    html: Optional[str] = None

    def _walk(part: dict[str, Any]) -> None:
        nonlocal plain, html
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        data = body.get("data")
        if data and mime.startswith("text/"):
            decoded = _b64url_decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain" and plain is None:
                plain = decoded
            elif mime == "text/html" and html is None:
                html = decoded
        for child in part.get("parts") or []:
            _walk(child)

    _walk(payload or {})
    if plain:
        return plain.strip()
    if html:
        return _strip_html(html)
    return ""


def _header_value(payload: dict[str, Any], name: str) -> Optional[str]:
    name_low = name.lower()
    for h in (payload or {}).get("headers", []) or []:
        if (h.get("name") or "").lower() == name_low:
            return h.get("value")
    return None


# ── dedupe ───────────────────────────────────────────────────────────────────


async def _already_seen(
    *, db: AsyncSession, user_id: uuid.UUID, message_id: str
) -> bool:
    row = await db.execute(
        select(GmailMessageSeen.gmail_message_id).where(
            GmailMessageSeen.user_id == user_id,
            GmailMessageSeen.gmail_message_id == message_id,
        )
    )
    return row.scalar_one_or_none() is not None


async def _mark_seen(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    message_id: str,
    outcome: str,
    transaction_id: Optional[uuid.UUID],
    ingestion_run_id: Optional[uuid.UUID],
    error: Optional[dict[str, Any]] = None,
) -> None:
    """Insert into gmail_messages_seen. If a row already exists (race
    between concurrent scans) the ON CONFLICT DO NOTHING is the right
    behavior — first writer wins."""
    stmt = pg_insert(GmailMessageSeen).values(
        user_id=user_id,
        gmail_message_id=message_id,
        outcome=outcome,
        transaction_id=transaction_id,
        ingestion_run_id=ingestion_run_id,
        error=error,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["user_id", "gmail_message_id"]
    )
    await db.execute(stmt)


# ── ingestion run lifecycle ──────────────────────────────────────────────────


async def _create_run(
    *, db: AsyncSession, user_id: uuid.UUID, mode: RunMode
) -> GmailIngestionRun:
    run = GmailIngestionRun(user_id=user_id, mode=mode)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _finalize_run(
    *, db: AsyncSession, run: GmailIngestionRun, result: ScanResult
) -> None:
    run.finished_at = datetime.now(timezone.utc)
    run.messages_scanned = result.messages_scanned
    run.transactions_created = result.transactions_created
    run.transactions_matched = result.transactions_matched
    run.errors = {"items": result.errors} if result.errors else None
    await db.commit()


# ── scanner entry point ─────────────────────────────────────────────────────


async def scan_user_inbox(
    *,
    user_id: uuid.UUID,
    since: datetime,
    until: Optional[datetime] = None,
    mode: RunMode = "manual",
    db: AsyncSession,
    llm_client: Optional[LLMClient] = None,
    http: Optional[httpx.AsyncClient] = None,
) -> ScanResult:
    """Scan one user's inbox between `since` and `until`.

    Args:
        db: AsyncSession to use for DB writes. The caller owns the
            session; the scanner commits incrementally so partial
            progress survives a crash mid-loop.
        llm_client: optional override of the LLM client. Tests pass a
            FixtureLLMClient; production lazy-constructs an
            AnthropicLLMClient from settings.anthropic_api_key.
        http: optional httpx.AsyncClient (tests pass one with a
            MockTransport). Production constructs a fresh one bound to
            the run.
    """
    started_at = datetime.now(timezone.utc)
    result = ScanResult(
        user_id=user_id,
        mode=mode,
        started_at=started_at,
    )

    log.info(
        "scan_started user=%s mode=%s since=%s until=%s",
        user_id,
        mode,
        since.isoformat(),
        until.isoformat() if until else "now",
    )

    # 1. Resolve access token (this also marks revoked_at if invalid_grant).
    access_token = await _resolve_access_token(user_id=user_id, db=db)
    if access_token is None:
        result.revoked = True
        result.finished_at = datetime.now(timezone.utc)
        log.warning("scan_aborted user=%s reason=no_credential_or_revoked", user_id)
        return result

    # 2. Read whitelist. Empty → record run, do nothing.
    senders = [s.sender_email for s in await wl_svc.list_active(db=db, user_id=user_id)]
    if not senders:
        run = await _create_run(db=db, user_id=user_id, mode=mode)
        result.run_id = run.id
        result.no_whitelist = True
        result.finished_at = datetime.now(timezone.utc)
        await _finalize_run(db=db, run=run, result=result)
        log.warning("scan_no_whitelist user=%s — nothing to query", user_id)
        return result

    # 3. Open run row.
    run = await _create_run(db=db, user_id=user_id, mode=mode)
    result.run_id = run.id

    # 4. Build query.
    query = _build_gmail_query(senders=senders, since=since, until=until)
    log.info(
        "scan_query user=%s q_chars=%d senders=%d",
        user_id,
        len(query),
        len(senders),
    )

    # 5. Scan. Errors per-message don't fail the run.
    close_http = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    llm = llm_client or AnthropicLLMClient(api_key=settings.anthropic_api_key)

    try:
        try:
            stubs = await _list_message_ids(
                http=client, access_token=access_token, query=query
            )
        except _RevokedError:
            await _mark_revoked(db=db, user_id=user_id)
            result.revoked = True
            result.finished_at = datetime.now(timezone.utc)
            await _finalize_run(db=db, run=run, result=result)
            return result
        except _ScannerRetryExhausted as e:
            log.exception("scan_list_failed user=%s err=%s", user_id, e)
            result.errors.append({"phase": "list", "error": str(e)})
            result.finished_at = datetime.now(timezone.utc)
            await _finalize_run(db=db, run=run, result=result)
            return result

        log.info(
            "scan_listed user=%s candidates=%d",
            user_id,
            len(stubs),
        )

        for stub in stubs:
            await _process_one_message(
                db=db,
                http=client,
                access_token=access_token,
                user_id=user_id,
                run_id=run.id,
                stub=stub,
                llm_client=llm,
                result=result,
            )
            result.messages_scanned += 1

    finally:
        if close_http:
            await client.aclose()

    result.finished_at = datetime.now(timezone.utc)
    await _finalize_run(db=db, run=run, result=result)
    log.info(
        "scan_finished user=%s scanned=%d created=%d matched=%d skipped=%d errs=%d",
        user_id,
        result.messages_scanned,
        result.transactions_created,
        result.transactions_matched,
        result.transactions_skipped,
        len(result.errors),
    )
    return result


async def _mark_revoked(*, db: AsyncSession, user_id: uuid.UUID) -> None:
    cred = (
        await db.execute(
            select(GmailCredential).where(GmailCredential.user_id == user_id)
        )
    ).scalar_one_or_none()
    if cred is not None and cred.revoked_at is None:
        cred.revoked_at = datetime.now(timezone.utc)
        await db.commit()


async def _process_one_message(
    *,
    db: AsyncSession,
    http: httpx.AsyncClient,
    access_token: str,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    stub: _MessageStub,
    llm_client: LLMClient,
    result: ScanResult,
) -> None:
    """One message lifecycle. Errors are caught and recorded as
    `outcome='failed'` in gmail_messages_seen so a later debugging pass
    can see what choked."""
    if await _already_seen(db=db, user_id=user_id, message_id=stub.id):
        # Already processed in a prior run. Don't refetch — saves quota.
        return

    try:
        msg = await _gmail_get(
            http=http,
            access_token=access_token,
            path=f"/messages/{stub.id}",
            params={"format": "full"},
        )
    except _ScannerRetryExhausted as e:
        log.exception(
            "scan_get_failed user=%s msg=%s err=%s", user_id, stub.id, e
        )
        result.errors.append(
            {"phase": "get", "msg_id": stub.id, "error": str(e)}
        )
        await _mark_seen(
            db=db,
            user_id=user_id,
            message_id=stub.id,
            outcome="failed",
            transaction_id=None,
            ingestion_run_id=run_id,
            error={"reason": str(e), "status": e.last_status},
        )
        await db.commit()
        return
    except _RevokedError:
        # Re-raise to abort the whole scan via the outer handler — but we
        # don't have one downstream of this function. Mark seen as failed
        # and let the next iteration's _gmail_get on a fresh URL also raise.
        log.warning("scan_revoked_mid_loop user=%s msg=%s", user_id, stub.id)
        result.errors.append(
            {"phase": "get", "msg_id": stub.id, "error": "revoked"}
        )
        await _mark_seen(
            db=db,
            user_id=user_id,
            message_id=stub.id,
            outcome="failed",
            transaction_id=None,
            ingestion_run_id=run_id,
            error={"reason": "revoked"},
        )
        await db.commit()
        result.revoked = True
        return

    payload = msg.get("payload", {}) or {}
    body = _extract_body(payload)
    subject = _header_value(payload, "Subject") or ""
    from_addr = _header_value(payload, "From") or ""

    if not body:
        log.info(
            "scan_msg_empty_body user=%s msg=%s subject=%r", user_id, stub.id, subject
        )
        await _mark_seen(
            db=db,
            user_id=user_id,
            message_id=stub.id,
            outcome="skipped",
            transaction_id=None,
            ingestion_run_id=run_id,
            error={"reason": "empty_body"},
        )
        result.transactions_skipped += 1
        await db.commit()
        return

    try:
        candidate = await extract_from_email_body(
            body=body,
            client=llm_client,
            model=settings.llm_extraction_model,
        )
    except EmailExtractionError as e:
        log.warning(
            "scan_extract_failed user=%s msg=%s err=%s", user_id, stub.id, e
        )
        await _mark_seen(
            db=db,
            user_id=user_id,
            message_id=stub.id,
            outcome="failed",
            transaction_id=None,
            ingestion_run_id=run_id,
            error={"reason": "extract_failed", "detail": str(e)[:200]},
        )
        result.errors.append(
            {"phase": "extract", "msg_id": stub.id, "error": str(e)[:200]}
        )
        await db.commit()
        return

    if candidate.confidence < _MIN_SCAN_CONFIDENCE:
        log.info(
            "scan_msg_low_conf user=%s msg=%s conf=%.2f type=%s",
            user_id,
            stub.id,
            candidate.confidence,
            candidate.transaction_type,
        )
        await _mark_seen(
            db=db,
            user_id=user_id,
            message_id=stub.id,
            outcome="skipped",
            transaction_id=None,
            ingestion_run_id=run_id,
            error=None,
        )
        result.transactions_skipped += 1
        await db.commit()
        return

    # Hand off to reconciler.
    outcome, txn = await reconcile_mod.reconcile(
        db=db,
        user_id=user_id,
        candidate=candidate,
        gmail_message_id=stub.id,
        email_subject=subject,
        email_from=from_addr,
    )

    # Map ReconcileOutcome → gmail_messages_seen.outcome (CHECK in 0011
    # admits matched | created | created_shadow | skipped | failed |
    # rejected_by_user).
    seen_outcome = {
        reconcile_mod.ReconcileOutcome.MATCHED_EXISTING: "matched",
        reconcile_mod.ReconcileOutcome.CREATED_NEW: "created",
        reconcile_mod.ReconcileOutcome.CREATED_SHADOW: "created_shadow",
        reconcile_mod.ReconcileOutcome.DUPLICATE_GMAIL: "skipped",
        reconcile_mod.ReconcileOutcome.SKIPPED_LOW_CONFIDENCE: "skipped",
    }[outcome]

    await _mark_seen(
        db=db,
        user_id=user_id,
        message_id=stub.id,
        outcome=seen_outcome,
        transaction_id=txn.id if txn is not None else None,
        ingestion_run_id=run_id,
    )

    if outcome == reconcile_mod.ReconcileOutcome.MATCHED_EXISTING:
        result.transactions_matched += 1
    elif outcome in {
        reconcile_mod.ReconcileOutcome.CREATED_NEW,
        reconcile_mod.ReconcileOutcome.CREATED_SHADOW,
    }:
        result.transactions_created += 1
        if txn is not None:
            result.created_transaction_ids.append(txn.id)
    else:
        result.transactions_skipped += 1

    log.info(
        "scan_msg_done user=%s msg=%s outcome=%s amount=%s currency=%s",
        user_id,
        stub.id,
        outcome.value,
        candidate.amount,
        candidate.currency,
    )

    await db.commit()
