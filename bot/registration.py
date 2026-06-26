"""Phase 8 B1 — Telegram cold-start registration.

When an UNKNOWN telegram_user_id sends /start, the bot offers to create the
account right there: Telegram already authenticates the telegram_user_id,
so no pairing code is needed in this direction (codes exist only for the
API→Telegram direction, to prove ownership of a pre-existing user row).

Deterministic mini-flow in Redis (`telegram:registration:{tg_id}`, TTL 15
min — keyed by telegram id because the user row doesn't exist yet),
mirroring the Phase 6d B9 account-creation pattern:

    /start (unknown) → email → nombre → confirmación (ToS + consent) →
    create user (shared api/services/users.py path, telegram bound in the
    same insert) + `core_service` consent grant (Phase 7e ledger, source
    'telegram') → shortcut token delivered in-chat → /setup.

The flow never touches an LLM — it's control state, like account creation.
/cancel clears it. The shortcut token lands in the chat history by design
(same exposure class as the magic-link/`/login` messages); the user can
rotate it any time via the API.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.services.consents import record_consent
from api.services.users import create_user_with_defaults

from . import messages_es
from .deep_link import mint_native_deep_link
from .redis_keys import REGISTRATION_TTL_S, registration_key

# Bump when the ToS/consent copy shown in REGISTER_CONFIRM changes.
TERMS_VERSION = "2026-06.v1"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_NON_WORD_RE = re.compile(r"[^\wáéíóúüñ]+", re.UNICODE)

_YES = {"si", "sí", "s", "dale", "ok", "confirmo", "confirmar", "yes"}
_NO = {"no", "n", "cancelar", "cancela"}


def _normalize(text: str) -> str:
    return _NON_WORD_RE.sub(" ", text.strip().lower()).strip()


async def load_registration(
    *, telegram_user_id: int, redis: Redis
) -> Optional[dict]:
    raw = await redis.get(registration_key(telegram_user_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def _save(telegram_user_id: int, state: dict, redis: Redis) -> None:
    await redis.setex(
        registration_key(telegram_user_id),
        REGISTRATION_TTL_S,
        json.dumps(state),
    )


async def clear_registration(*, telegram_user_id: int, redis: Redis) -> None:
    await redis.delete(registration_key(telegram_user_id))


def _reprompt(state: dict) -> str:
    step = state.get("step")
    if step == "name":
        return messages_es.REGISTER_ASK_NAME
    if step == "confirm":
        return messages_es.REGISTER_CONFIRM.format(
            name=state.get("name", ""), email=state.get("email", "")
        )
    return messages_es.REGISTER_START


async def begin_registration(
    *, telegram_user_id: int, redis: Redis
) -> str:
    """Entry point for /start from an unknown Telegram account. Re-entrant:
    an in-flight flow re-prompts its current step instead of restarting."""
    state = await load_registration(
        telegram_user_id=telegram_user_id, redis=redis
    )
    if state is not None:
        return _reprompt(state)
    await _save(telegram_user_id, {"step": "email"}, redis)
    return messages_es.REGISTER_START


async def handle_registration_text(
    *,
    telegram_user_id: int,
    text: str,
    db: AsyncSession,
    redis: Redis,
) -> Optional[str]:
    """Advance the flow with the user's message. Returns the reply, or None
    when no registration is in flight (caller falls back to PAIR_PROMPT)."""
    state = await load_registration(
        telegram_user_id=telegram_user_id, redis=redis
    )
    if state is None:
        return None

    step = state.get("step")

    if step == "email":
        email = text.strip().lower()
        if not _EMAIL_RE.match(email):
            return messages_es.REGISTER_EMAIL_INVALID
        existing = (
            await db.execute(
                select(User.id).where(func.lower(User.email) == email)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return messages_es.REGISTER_EMAIL_TAKEN
        state.update(step="name", email=email)
        await _save(telegram_user_id, state, redis)
        return messages_es.REGISTER_ASK_NAME

    if step == "name":
        name = text.strip()
        if not (2 <= len(name) <= 120):
            return messages_es.REGISTER_NAME_INVALID
        state.update(step="confirm", name=name)
        await _save(telegram_user_id, state, redis)
        return messages_es.REGISTER_CONFIRM.format(
            name=name, email=state["email"]
        )

    if step == "confirm":
        answer = _normalize(text)
        if answer in _NO:
            await clear_registration(
                telegram_user_id=telegram_user_id, redis=redis
            )
            return messages_es.REGISTER_CANCELLED
        if answer not in _YES:
            return messages_es.REGISTER_CONFIRM_HINT
        try:
            user = await create_user_with_defaults(
                db,
                email=state["email"],
                full_name=state["name"],
                telegram_user_id=telegram_user_id,
            )
            # Phase 7e consent ledger: confirming the summary IS the
            # core_service grant (the confirm copy says so explicitly).
            await record_consent(
                db,
                user_id=user.id,
                purpose="core_service",
                granted=True,
                version=TERMS_VERSION,
                source="telegram",
            )
            await db.commit()
        except IntegrityError:
            # Race on email (or a telegram re-bind oddity): don't burn the
            # flow — send them back to the email step.
            await db.rollback()
            state.update(step="email")
            state.pop("email", None)
            await _save(telegram_user_id, state, redis)
            return messages_es.REGISTER_EMAIL_TAKEN
        await clear_registration(
            telegram_user_id=telegram_user_id, redis=redis
        )
        done = messages_es.REGISTER_DONE.format(
            name=user.full_name, token=user.shortcut_token
        )
        # Phase 8 B2: deep-link the new registrant straight into the app so they
        # skip the /login code dance. Swallow-on-fail → a /login fallback line.
        deep_link = await mint_native_deep_link(
            db, user_id=user.id, purpose="onboarding"
        )
        if deep_link:
            await db.commit()  # mint persisted a magic_link_tokens row
            done += (
                "\n\nTocá este link desde tu teléfono para entrar "
                "(válido 30 min, un solo uso):\n" + deep_link
            )
        else:
            done += (
                "\n\nPara entrar a la app, mandá /login y te doy un código."
            )
        return done

    # Unknown step (stale/corrupt state): reset cleanly.
    await clear_registration(telegram_user_id=telegram_user_id, redis=redis)
    return messages_es.REGISTER_START
