"""Phase 6d B9 conversational account creation flow.

This is Redis-backed durable bot state, not aiogram FSM. It is deliberately
small: collect the fields the existing accounts endpoint needs, validate
each turn, then call the same backend contract used by the SPA.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.account import Account
from api.models.user import User
from api.routers.accounts import create_account
from api.schemas.account import AccountCreate

from .redis_keys import (
    ACCOUNT_CREATION_TTL_S,
    account_creation_key,
)

AccountCreationStep = Literal[
    "awaiting_start",
    "asking_name",
    "asking_type",
    "asking_currency",
    "asking_balance",
    "confirming",
]

ACCOUNT_TYPE_LABELS = {
    "checking": "corriente",
    "savings": "ahorros",
    "credit": "crédito",
    "investment": "inversión",
}

_BALANCE_RE = re.compile(r"-?\d+(?:[.,]\d+)*")


@dataclass
class AccountCreationState:
    step: AccountCreationStep
    name: Optional[str] = None
    account_type: Optional[str] = None
    currency: Optional[str] = None
    initial_balance: Optional[str] = None
    origin_extraction: Optional[dict[str, Any]] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "AccountCreationState":
        return cls(**json.loads(raw))


@dataclass(frozen=True)
class AccountCreationOutcome:
    text: str
    account: Account | None = None
    origin_extraction: Optional[dict[str, Any]] = None


async def save_account_creation(
    *, user_id: uuid.UUID, state: AccountCreationState, redis: Redis
) -> None:
    await redis.setex(
        account_creation_key(user_id), ACCOUNT_CREATION_TTL_S, state.to_json()
    )


async def load_account_creation(
    *, user_id: uuid.UUID, redis: Redis
) -> Optional[AccountCreationState]:
    raw = await redis.get(account_creation_key(user_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return AccountCreationState.from_json(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def clear_account_creation(*, user_id: uuid.UUID, redis: Redis) -> None:
    await redis.delete(account_creation_key(user_id))


async def save_lazy_account_creation(
    *,
    user_id: uuid.UUID,
    suggested_name: str,
    origin_extraction: dict[str, Any],
    redis: Redis,
) -> None:
    await save_account_creation(
        user_id=user_id,
        redis=redis,
        state=AccountCreationState(
            step="awaiting_start",
            name=suggested_name,
            origin_extraction=origin_extraction,
        ),
    )


def parse_account_creation_start(text: str) -> tuple[bool, Optional[str]]:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    for prefix in ("crear cuenta", "crear una cuenta", "nueva cuenta"):
        if lowered == prefix:
            return True, None
        if lowered.startswith(prefix + " "):
            return True, normalized[len(prefix):].strip() or None
    return False, None


# Affirmatives that answer the soft lazy-detection "¿La creamos?" prompt
# (step=awaiting_start). Any other input means the user moved on, so the
# pipeline abandons the prompt and reprocesses the message as fresh input
# instead of trapping them in the account dialog.
_AWAITING_START_ANSWERS = frozenset({"crear", "crearla", "si", "sí", "dale", "ok"})


def is_awaiting_start_answer(text: str) -> bool:
    return _normalize(text) in _AWAITING_START_ANSWERS


async def start_account_creation(
    *, user_id: uuid.UUID, redis: Redis, name: Optional[str] = None
) -> str:
    step: AccountCreationStep = "asking_type" if name else "asking_name"
    await save_account_creation(
        user_id=user_id,
        redis=redis,
        state=AccountCreationState(step=step, name=name),
    )
    if name:
        return _ask_type(name)
    return "Dale. ¿Cómo se llama la cuenta? Ej: BAC ahorros."


async def handle_account_creation_reply(
    *,
    user: User,
    text: str,
    state: AccountCreationState,
    db: AsyncSession,
    redis: Redis,
) -> AccountCreationOutcome:
    reply = text.strip()
    lowered = _normalize(reply)

    if state.step == "awaiting_start":
        # Only reached with an affirmative — the pipeline abandons this soft
        # prompt and reprocesses the message on any other input (so a new
        # expense isn't swallowed by a stale "¿La creamos?" prompt).
        state.step = "asking_type"
        await save_account_creation(user_id=user.id, state=state, redis=redis)
        return AccountCreationOutcome(text=_ask_type(state.name or "esa cuenta"))

    if state.step == "asking_name":
        if len(reply) < 2:
            return AccountCreationOutcome(text="Poné al menos 2 caracteres para el nombre.")
        state.name = reply
        state.step = "asking_type"
        await save_account_creation(user_id=user.id, state=state, redis=redis)
        return AccountCreationOutcome(text=_ask_type(reply))

    if state.step == "asking_type":
        account_type = _parse_account_type(reply)
        if account_type is None:
            return AccountCreationOutcome(
                text=(
                    "No reconocí ese tipo. Podés decir: corriente, ahorros, "
                    "crédito o inversión."
                )
            )
        state.account_type = account_type
        state.step = "asking_currency"
        await save_account_creation(user_id=user.id, state=state, redis=redis)
        return AccountCreationOutcome(text="¿En qué moneda está? CRC o USD.")

    if state.step == "asking_currency":
        currency = _parse_currency(reply)
        if currency is None:
            return AccountCreationOutcome(text="La moneda puede ser CRC o USD.")
        state.currency = currency
        state.step = "asking_balance"
        await save_account_creation(user_id=user.id, state=state, redis=redis)
        return AccountCreationOutcome(
            text="¿Con qué saldo inicial arranca? Podés poner 0."
        )

    if state.step == "asking_balance":
        balance = _parse_balance(reply)
        if balance is None:
            return AccountCreationOutcome(
                text="No pude leer ese monto. Probá con algo como 0, 50000 o -25000."
            )
        if state.account_type != "credit" and balance < 0:
            return AccountCreationOutcome(
                text="Solo una cuenta de crédito puede arrancar en negativo."
            )
        state.initial_balance = str(balance)
        state.step = "confirming"
        await save_account_creation(user_id=user.id, state=state, redis=redis)
        return AccountCreationOutcome(text=_confirmation_text(state))

    if state.step == "confirming":
        if lowered in {"no", "cancelar", "cancela"}:
            await clear_account_creation(user_id=user.id, redis=redis)
            return AccountCreationOutcome(text="Cancelado, no creé la cuenta.")
        if lowered not in {"si", "sí", "dale", "ok", "confirmar", "listo"}:
            return AccountCreationOutcome(text="Respondeme sí para crearla o no para cancelar.")
        account = await _create_account(user=user, state=state, db=db)
        await clear_account_creation(user_id=user.id, redis=redis)
        return AccountCreationOutcome(
            text=f"Listo, creé la cuenta {account.name}.",
            account=account,
            origin_extraction=state.origin_extraction,
        )

    return AccountCreationOutcome(text="Algo quedó raro con esta creación. Mandá /cancel.")


async def _create_account(
    *, user: User, state: AccountCreationState, db: AsyncSession
) -> Account:
    if (
        state.name is None
        or state.account_type is None
        or state.currency is None
        or state.initial_balance is None
    ):
        raise RuntimeError("account creation state incomplete")
    try:
        created = await create_account(
            payload=AccountCreate(
                name=state.name,
                account_type=state.account_type,
                currency=state.currency,  # type: ignore[arg-type]
                initial_balance=Decimal(state.initial_balance),
            ),
            db=db,
            user=user,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "No pude crear la cuenta."
        raise AccountCreationError(detail) from exc
    return created


class AccountCreationError(Exception):
    pass


def _ask_type(name: str) -> str:
    return (
        f"La creo como {name}. ¿Qué tipo es? "
        "Podés decir: corriente, ahorros, crédito o inversión."
    )


def _confirmation_text(state: AccountCreationState) -> str:
    account_type = ACCOUNT_TYPE_LABELS.get(state.account_type or "", state.account_type)
    return (
        "Voy a crear esta cuenta:\n"
        f"• Nombre: {state.name}\n"
        f"• Tipo: {account_type}\n"
        f"• Moneda: {state.currency}\n"
        f"• Saldo inicial: {state.initial_balance}\n\n"
        "¿Seguimos?"
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _parse_account_type(text: str) -> Optional[str]:
    normalized = _normalize(text)
    if normalized in {"corriente", "cuenta corriente", "checking"}:
        return "checking"
    if normalized in {"ahorro", "ahorros", "savings"}:
        return "savings"
    if normalized in {"credito", "crédito", "tarjeta", "credit"}:
        return "credit"
    if normalized in {"inversion", "inversión", "investment"}:
        return "investment"
    return None


def _parse_currency(text: str) -> Optional[str]:
    normalized = _normalize(text)
    if normalized in {"crc", "colon", "colón", "colones", "₡"}:
        return "CRC"
    if normalized in {"usd", "dolar", "dólar", "dolares", "dólares", "$"}:
        return "USD"
    return None


def _parse_balance(text: str) -> Optional[Decimal]:
    cleaned = (
        text.lower()
        .replace("₡", "")
        .replace("$", "")
        .replace("crc", "")
        .replace("usd", "")
        .replace("colones", "")
        .replace("colón", "")
        .replace("colon", "")
        .strip()
    )
    match = _BALANCE_RE.search(cleaned)
    if not match:
        return None
    token = match.group(0)
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        if len(token.rsplit(",", 1)[-1]) == 2:
            token = token.replace(",", ".")
        else:
            token = token.replace(",", "")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    try:
        return Decimal(token)
    except Exception:
        return None
