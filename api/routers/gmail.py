"""Gmail OAuth endpoints (Phase 6b — block 4).

Two routes:

    POST /api/v1/gmail/oauth/start
        Auth: current_user (X-Shortcut-Token preferred, X-User-Id shim).
        Body: empty.
        Returns: { auth_url, expires_at, expires_in_seconds }.

    GET /api/v1/gmail/oauth/callback?code=&state= (or ?error=)
        Auth: NONE — Google redirects the user's browser here. The only
        defense is the signed state JWT (HS256 + one-time nonce in Redis).
        Outcomes:
          - 200 + redirect to /static/gmail-connected.html on success.
          - 200 + redirect to /static/gmail-error.html on any failure
            (denied, expired state, exchange failure, etc.). User-facing
            copy is on the static page; the bot picks up the outcome via
            redis pub/sub and continues the onboarding flow.

The Bot subscribes to `gmail_callback:{user_id}` to be notified the
moment the callback completes; the user only ever sees the static page,
then switches back to Telegram.

`GmailCredential` row is upserted at success time. The `kv_secret_name`
points at Key Vault (`gmail-refresh-{user_id}`); the refresh_token
itself never lands in the DB. Activation (when shadow-mode starts) is
NOT done here — it happens after the sample is approved (B6).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import current_user
from ..models.gmail_credential import GmailCredential
from ..models.user import User
from ..redis_client import get_redis
from ..schemas.gmail import GmailStatusResponse, OAuthStartResponse
from ..services.gmail import oauth as oauth_svc
from ..services.gmail.backfill import enqueue_backfill
from ..services.secrets import get_secret_store, kv_name_for_user

from bot.gmail_pubsub import publish_callback


log = logging.getLogger("api.routers.gmail")


router = APIRouter(prefix="/api/v1/gmail", tags=["gmail"])


# ── /oauth/start ──────────────────────────────────────────────────────────────


@router.post("/oauth/start", response_model=OAuthStartResponse)
async def oauth_start(
    user: User = Depends(current_user),
) -> OAuthStartResponse:
    redis = get_redis()
    try:
        url = await oauth_svc.build_auth_url(user_id=user.id, redis=redis)
    except oauth_svc.OAuthStateError as e:
        # Configuration problem (missing secret / client_id). Surface as
        # 503 so the bot can show "service unavailable" instead of
        # leaking the missing-config detail.
        log.warning("oauth_start configuration error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail OAuth no está configurado en el servidor.",
        )

    ttl = settings.gmail_oauth_state_ttl_s
    return OAuthStartResponse(
        auth_url=url,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
        expires_in_seconds=ttl,
    )


# ── /oauth/callback ───────────────────────────────────────────────────────────


_SUCCESS_PAGE = "/static/gmail-connected.html"
_ERROR_PAGE = "/static/gmail-error.html"


def _redirect_error(reason: str) -> RedirectResponse:
    # Pass `reason` as a short opaque code in the query string — the
    # static page doesn't show it to the user, but it's useful in logs
    # and for support.
    return RedirectResponse(
        url=f"{_ERROR_PAGE}?reason={reason}", status_code=303
    )


@router.get("/oauth/callback")
async def oauth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Receive Google's redirect after the user grants (or denies) consent."""
    redis = get_redis()

    # 1. User denied at the consent screen, or Google rejected (e.g. user
    #    is not in the test-users list).
    if error:
        log.info(
            "oauth_callback denied error=%s desc=%s", error, error_description
        )
        # We can still try to recover the user_id from `state` so the bot
        # gets pinged; if state is missing/invalid, just bail.
        try:
            payload = oauth_svc._decode_state(state) if state else None
            if payload:
                await publish_callback(
                    redis=redis,
                    user_id=payload["user_id"],
                    status="denied",
                    detail=error,
                )
        except oauth_svc.OAuthStateError:
            pass
        return _redirect_error("denied")

    # 2. Missing required params (shouldn't happen with a real Google
    #    redirect, but guards against direct hits to the URL).
    if not code or not state:
        log.warning("oauth_callback missing code or state")
        return _redirect_error("invalid_request")

    # 3. Validate state + exchange code for tokens.
    try:
        result = await oauth_svc.exchange_code(
            code=code, state=state, redis=redis
        )
    except oauth_svc.OAuthStateError as e:
        log.info("oauth_callback bad state: %s", e)
        return _redirect_error("invalid_state")
    except oauth_svc.OAuthExchangeError as e:
        log.warning(
            "oauth_callback exchange failed: %s (code=%s)", e, e.code
        )
        # Try to ping the bot if we got far enough to decode the state.
        try:
            payload = oauth_svc._decode_state(state)
            await publish_callback(
                redis=redis,
                user_id=payload["user_id"],
                status="error",
                detail=e.code or "exchange_failed",
            )
        except oauth_svc.OAuthStateError:
            pass
        return _redirect_error("exchange_failed")

    # 4. Persist refresh token in Key Vault, upsert gmail_credentials row.
    user_exists = await db.execute(
        select(User.id).where(User.id == result.user_id)
    )
    if user_exists.scalar_one_or_none() is None:
        # The state JWT signed a user_id that no longer exists in the DB.
        # Should be near-impossible (state TTL is 10 min, users aren't
        # deleted that fast), but bail safely if so.
        log.error(
            "oauth_callback unknown user_id=%s in valid state",
            result.user_id,
        )
        return _redirect_error("unknown_user")

    secret_store = get_secret_store()
    kv_name = kv_name_for_user(result.user_id)
    await secret_store.set(kv_name, result.refresh_token)

    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(GmailCredential)
        .values(
            user_id=result.user_id,
            kv_secret_name=kv_name,
            scopes=result.granted_scopes,
            granted_at=now,
            revoked_at=None,
            last_refresh_at=None,
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_=dict(
                kv_secret_name=kv_name,
                scopes=result.granted_scopes,
                granted_at=now,
                revoked_at=None,
                last_refresh_at=None,
            ),
        )
    )
    await db.execute(stmt)
    await db.commit()

    # 5. Notify the bot. Failure to publish is not fatal — the bot's next
    #    poll of `gmail_onboarding:{user_id}` will see oauth_done.
    try:
        await publish_callback(
            redis=redis, user_id=result.user_id, status="success"
        )
    except Exception:  # pragma: no cover — Redis was already used above
        log.exception("publish_callback failed; bot will catch up via state")

    return RedirectResponse(url=_SUCCESS_PAGE, status_code=303)


# ── /status (lightweight, read by /estado_gmail in the bot) ───────────────────


# ── /admin/run-backfill (B.4) ────────────────────────────────────────────────
#
# Auth: same `current_user` pattern as the rest of /api/v1/gmail. Daniel
# is currently the sole admin; until a real admin role lands, "admin"
# means "anyone authenticated who knows the URL". Acceptable for the
# personal MVP.


@router.post("/admin/run-backfill")
async def admin_run_backfill(
    days: int = Query(default=30, ge=1, le=90),
    user: User = Depends(current_user),
):
    """Kick off a backfill for the authenticated user. Useful for
    re-trying when the activation-time backfill failed, or for testing
    a deeper window than the default 30 days."""
    enqueue_backfill(user_id=user.id, days=days, mode="manual")
    return {
        "queued": True,
        "user_id": str(user.id),
        "days": days,
    }


@router.post("/admin/run-daily")
async def admin_run_daily(user: User = Depends(current_user)):
    """Trigger the same code path as the daily Container Apps Job, but
    in-process. Use this to test the worker without waiting for cron.
    Auth is the same as the rest of /api/v1/gmail — Daniel-only in
    practice while we don't have a real admin role.

    Note: this iterates ALL active users, not just the caller. If you
    only want to backfill yourself, use /admin/run-backfill.
    """
    import asyncio

    from workers.gmail_daily import run_daily_for_all_users

    # Don't await — let the response return immediately so the admin
    # caller doesn't time out on long scans. asyncio holds a reference
    # internally so Python's GC won't collect the task.
    asyncio.create_task(
        run_daily_for_all_users(), name="admin-run-daily"
    )
    return {"queued": True, "triggered_by": str(user.id)}


@router.get("/status", response_model=GmailStatusResponse)
async def gmail_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> GmailStatusResponse:
    """Read-only status of the user's Gmail integration."""
    row = await db.execute(
        select(GmailCredential).where(GmailCredential.user_id == user.id)
    )
    cred = row.scalar_one_or_none()
    if cred is None or cred.revoked_at is not None:
        return GmailStatusResponse(connected=False)

    return GmailStatusResponse(
        connected=True,
        granted_at=cred.granted_at,
        activated_at=cred.activated_at,
        revoked_at=cred.revoked_at,
        last_refresh_at=cred.last_refresh_at,
        scopes=list(cred.scopes or []),
    )
