"""Phase 6d B10 onboarding-aware welcome/help copy.

Phase 6f B16: the SPA was retired. The "setup web" magic-link button is
replaced by a native deep link (`ledgercr://exchange?token=...`) delivered
as tappable message text — Telegram inline URL buttons require https, so a
custom-scheme link can't be a button (same constraint `/login` works around).
Tapping it opens the native app and signs in via the silent
`useMagicLinkListener`. Structured onboarding (accounts/incomes/debts/bills)
now happens in the app + chat, not a web form.

Note: a brand-new user without the app installed can't use the deep link —
they fall back to the app + `/login`. App-install onboarding for beta users
is a P8 concern.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.routers.onboarding import onboarding_status
from api.schemas.onboarding import OnboardingStatus

from .deep_link import mint_native_deep_link


@dataclass(frozen=True)
class OnboardingReply:
    text: str


async def _setup_link_line(*, user: User, db: AsyncSession) -> str:
    """A tappable native deep link as message text, or a /login fallback."""
    link = await mint_native_deep_link(
        db, user_id=user.id, purpose="onboarding"
    )
    if link:
        return (
            "Link para entrar a la app (válido 30 min, un solo uso) — tocalo "
            f"desde tu teléfono:\n{link}"
        )
    return "Abrí la app de LedgerCR e iniciá sesión con el código de /login."


async def build_setup_reply(*, user: User, db: AsyncSession) -> OnboardingReply:
    line = await _setup_link_line(user=user, db=db)
    return OnboardingReply(
        text=(
            f"{line}\n\n"
            "Si ya estás dentro y solo querés volver a entrar, mandame /setup."
        )
    )


async def build_onboarding_reply(
    *,
    user: User,
    db: AsyncSession,
    first_name: str = "",
    include_setup_link: bool = False,
    paired_now: bool = False,
) -> OnboardingReply:
    status = await onboarding_status(db=db, user=user)

    if _is_complete(status):
        return OnboardingReply(
            text=_complete_help(first_name=first_name, paired_now=paired_now)
        )

    if _is_empty(status):
        text = _empty_welcome(first_name=first_name, paired_now=paired_now)
    else:
        text = _partial_welcome(
            status=status,
            first_name=first_name,
            paired_now=paired_now,
        )

    if include_setup_link:
        text += "\n\n" + await _setup_link_line(user=user, db=db)
    return OnboardingReply(text=text)


def _is_empty(status: OnboardingStatus) -> bool:
    return (
        not status.has_accounts
        and not status.has_incomes
        and not status.has_debts
        and not status.has_recurring_bills
    )


def _is_complete(status: OnboardingStatus) -> bool:
    return (
        status.has_accounts
        and status.has_incomes
        and status.has_debts
        and status.has_recurring_bills
    )


def _prefix(*, first_name: str, paired_now: bool) -> str:
    name = first_name.strip()
    if paired_now and name:
        return f"Listo, {name}. Ya quedó pareado.\n\n"
    if paired_now:
        return "Listo, ya quedó pareado.\n\n"
    if name:
        return f"Hola, {name}.\n\n"
    return ""


def _empty_welcome(*, first_name: str, paired_now: bool) -> str:
    return (
        _prefix(first_name=first_name, paired_now=paired_now)
        + "Bienvenido. Para arrancar, registrá al menos una cuenta. "
        "Podés hacerlo en la app (cuentas, ingresos, deudas y gastos fijos) "
        "o paso a paso por acá.\n\n"
        "Si preferís chat, decime algo como: crear cuenta BAC.\n"
        "Para volver a entrar a la app, mandá /setup."
    )


def _partial_welcome(
    *,
    status: OnboardingStatus,
    first_name: str,
    paired_now: bool,
) -> str:
    missing = _missing_labels(status)
    missing_text = _join_es(missing)
    return (
        _prefix(first_name=first_name, paired_now=paired_now)
        + f"Ya empezaste. Te falta registrar {missing_text}.\n\n"
        "Podés seguir en la app con /setup, o hacerlo por acá cuando surja. "
        "Para cuentas, decime: crear cuenta BAC."
    )


def _complete_help(*, first_name: str, paired_now: bool) -> str:
    return (
        _prefix(first_name=first_name, paired_now=paired_now)
        + "Ya tenés lo básico registrado.\n\n"
        "Podés decirme:\n"
        "• gasté 5000 en el super\n"
        "• me pagaron 400 mil\n"
        "• cuánto gasté esta semana\n\n"
        "Comandos útiles: /setup, /undo, /cancel, /memoria."
    )


def _missing_labels(status: OnboardingStatus) -> list[str]:
    missing: list[str] = []
    if not status.has_accounts:
        missing.append("cuentas")
    if not status.has_incomes:
        missing.append("ingresos")
    if not status.has_debts:
        missing.append("deudas")
    if not status.has_recurring_bills:
        missing.append("gastos fijos")
    return missing


def _join_es(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} y {items[1]}"
    return f"{', '.join(items[:-1])} y {items[-1]}"
