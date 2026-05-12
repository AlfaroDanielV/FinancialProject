"""Phase 6d B10 onboarding-aware welcome/help copy."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.routers.onboarding import onboarding_status
from api.schemas.onboarding import OnboardingStatus
from api.services.auth.magic_link import generate_link


SETUP_BUTTON_LABEL = "Abrir setup web"


@dataclass(frozen=True)
class OnboardingReply:
    text: str
    setup_url: str | None = None


async def build_setup_reply(*, user: User, db: AsyncSession) -> OnboardingReply:
    link = await generate_link(db, user_id=user.id, purpose="onboarding")
    return OnboardingReply(
        text=(
            "Acá tenés tu link al setup web. Es válido por 30 minutos y se "
            "usa una sola vez.\n\n"
            "Si lo abrís y después necesitás entrar de nuevo, mandame /setup."
        ),
        setup_url=link.url,
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
    setup_url = None
    if include_setup_link and not _is_complete(status):
        setup_url = (
            await generate_link(db, user_id=user.id, purpose="onboarding")
        ).url

    if _is_empty(status):
        return OnboardingReply(
            text=_empty_welcome(first_name=first_name, paired_now=paired_now),
            setup_url=setup_url,
        )
    if _is_complete(status):
        return OnboardingReply(
            text=_complete_help(first_name=first_name, paired_now=paired_now)
        )
    return OnboardingReply(
        text=_partial_welcome(
            status=status,
            first_name=first_name,
            paired_now=paired_now,
        ),
        setup_url=setup_url,
    )


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
        "Podés abrir el setup web para meter cuentas, ingresos, deudas y "
        "gastos fijos, o hacerlo paso a paso por acá.\n\n"
        "Si preferís chat, decime algo como: crear cuenta BAC.\n"
        "Para abrir el setup después, mandá /setup."
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
        "Podés seguir en el setup web con /setup, o hacerlo por acá cuando "
        "surja. Para cuentas, decime: crear cuenta BAC. Las deudas van mejor "
        "en el setup web."
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
