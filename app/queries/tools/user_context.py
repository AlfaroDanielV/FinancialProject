from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import desc, select

from api.models.user_insight import UserInsight
from api.schemas.insights import InsightContent, validate_insight_content
from app.queries.session import AsyncSessionLocal

from .base import is_tool_registered, query_tool

log = logging.getLogger(__name__)

_MONTH_NAMES_ES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "setiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}


def register_user_context_tool() -> None:
    if is_tool_registered("get_user_context"):
        return

    @query_tool(
        name="get_user_context",
        description=(
            "Memoria del usuario en lenguaje natural, filtrada por tipo y "
            "ordenada por relevancia."
        ),
    )
    async def get_user_context(
        user_id: uuid.UUID,
        insight_types: list[str] | None = None,
        max_insights: int = 10,
    ) -> str:
        if max_insights <= 0:
            return "No tengo memoria activa relevante para eso."

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            stmt = (
                select(UserInsight)
                .where(
                    UserInsight.user_id == user_id,
                    UserInsight.valid_until > now,
                )
                .order_by(
                    desc(UserInsight.confidence),
                    desc(UserInsight.updated_at),
                )
                .limit(max_insights)
            )
            if insight_types:
                stmt = stmt.where(UserInsight.insight_type.in_(insight_types))

            rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            return "No tengo memoria activa relevante para eso."

        bullets: list[str] = []
        for row in rows:
            try:
                content = validate_insight_content(row.content)
            except Exception as exc:  # noqa: BLE001 - defensive read path
                log.warning(
                    "user_context_invalid_insight user_id=%s insight_id=%s err=%s",
                    user_id,
                    row.id,
                    type(exc).__name__,
                )
                continue
            bullets.append(f"- [{content.type}] {_describe_insight(content)}")

        if not bullets:
            return "No tengo memoria activa relevante para eso."
        return "\n".join(bullets)


# Spanish (voseo, CR) labels for the money_personality archetype. Mirrors
# PERSONALITY_LABELS_ES in api/services/finance/money_personality.py (kept
# inline to avoid pulling that engine's heavy imports into the query tool).
_PERSONALITY_LABELS_ES: dict[str, str] = {
    "spender": "Gastador",
    "avoider": "Evasor",
    "saver": "Ahorrador",
    "investor": "Inversionista",
}


def _describe_insight(content: InsightContent) -> str:
    match content.type:
        case "spending_pattern":
            return _describe_spending_pattern(content)
        case "recurring_drift":
            return (
                f"La factura {content.bill_id} cambió de "
                f"{_format_crc(content.baseline_amount)} a "
                f"{_format_crc(content.current_amount)} "
                f"({_format_signed_pct(content.drift_pct)})."
            )
        case "cash_flow_stability":
            parts = [
                f"Tu flujo de caja tiene un score de {content.score_0_100}/100",
                f"variación mensual de {_format_pct(content.monthly_variance_pct)}",
                f"y entran ingresos de {content.income_sources_count} fuentes",
                f"tasa de ahorro mensual de {_format_signed_pct_value(content.savings_rate_pct)}",
            ]
            if content.last_income_at is not None:
                parts.append(
                    f"último ingreso {_format_date(content.last_income_at)}"
                )
            return "; ".join(parts) + "."
        case "debt_load":
            return (
                f"Tu deuda representa {_format_pct(content.debt_to_income_ratio * 100)} "
                f"de tu ingreso; servicio mensual "
                f"{_format_crc(content.total_monthly_debt_service_crc)} y "
                f"{content.num_active_debts} deudas activas."
            )
        case "emergency_fund":
            return (
                f"Tu fondo de emergencia cubre {_format_decimal(content.months_of_expenses_covered, 1)} "
                f"meses; tenés {_format_crc(content.liquid_balance_crc)} líquidos "
                f"para {_format_crc(content.monthly_essential_expenses_crc)} de "
                f"gastos esenciales al mes ({content.sufficiency})."
            )
        case "cr_seasonal_pattern":
            return (
                f"En {_MONTH_NAMES_ES[content.expected_month]} suele aparecer "
                f"{content.event}; histórico "
                f"{_format_crc(content.historical_amount_crc)}; última vez vista "
                f"{content.last_observed_year if content.last_observed_year is not None else 'sin año registrado'}."
            )
        case "stated_preference":
            topic = _topic_label(content.topic)
            return (
                f"Decís que {content.stance} cuando hablamos de {topic}. "
                f"Cita: “{_truncate(content.raw_quote, 140)}”."
            )
        case "stated_goal":
            goal = _truncate(content.goal_text, 140)
            amount = (
                f" por {_format_crc(content.target_amount)}"
                if content.target_amount is not None
                else ""
            )
            when = (
                f" para {_format_date(content.target_date)}"
                if content.target_date is not None
                else ""
            )
            return (
                f"Tu meta es {goal}{amount}{when}; estado {_goal_status(content.status)}."
            )
        case "behavioral_flag":
            return (
                f"Hay señal de {_flag_label(content.flag_type)} en "
                f"{content.evidence_window}; fuerza "
                f"{_format_decimal(content.strength_score, 2)}."
            )
        case "archetype":
            secondary = (
                f" y un rasgo secundario {content.secondary.replace('_', ' ')}"
                if content.secondary
                else ""
            )
            return (
                f"Tu patrón general parece {content.primary.replace('_', ' ')}"
                f"{secondary}; confianza {_format_pct(content.confidence * 100)}."
            )
        case "risk_posture":
            return (
                f"Tu postura al riesgo parece {content.posture}; la evidencia es "
                f"{content.evidence_basis}."
            )
        case "decision_style":
            return (
                f"Tomás decisiones de forma {content.style}; evidencia: "
                f"{_truncate(content.evidence, 180)}."
            )
        case "financial_literacy":
            return (
                f"Tu nivel de alfabetización financiera parece {content.level}; "
                f"evidencia: {_truncate(content.evidence, 180)}."
            )
        case "money_personality":
            return (
                f"Tu personalidad financiera parece ser "
                f"{_PERSONALITY_LABELS_ES.get(content.personality, content.personality)}: "
                f"{_truncate(content.evidence_summary, 220)}"
            )
        case "stated_observed_gap":
            return _truncate(content.description, 220)
        case _:
            return f"Insight de tipo {content.type}."


def _describe_spending_pattern(content: Any) -> str:
    amount = content.monthly_avg_crc
    currency = "CRC"
    if amount is None:
        amount = content.monthly_avg_usd
        currency = "USD"

    return (
        f"En {content.category}, gastás en promedio {_format_currency(amount, currency)} "
        f"al mes; la tendencia a 3 meses va {_trend_phrase(content.trend_pct_3mo)} "
        f"y la volatilidad es {_format_pct(content.volatility_score * 100)}."
    )


def _trend_phrase(value: Decimal | int | float | str) -> str:
    d = _as_decimal(value)
    if d == 0:
        return "estable"
    direction = "subiendo" if d > 0 else "bajando"
    return f"{direction} {_format_abs_pct(d)}"


def _goal_status(status: str) -> str:
    return {
        "mentioned": "mencionada",
        "committed": "comprometida",
        "abandoned": "abandonada",
    }.get(status, status)


def _flag_label(flag_type: str) -> str:
    return {
        "weekend_overspend": "sobregasto de fin de semana",
        "paycheck_cycle": "ciclo de quincena",
        "impulse_pattern": "compras impulsivas",
    }.get(flag_type, flag_type.replace("_", " "))


def _topic_label(topic: str) -> str:
    return {
        "debt_payoff": "pagar deudas",
        "savings": "ahorrar",
        "risk_tolerance": "tu tolerancia al riesgo",
        "lifestyle": "tu estilo de vida",
    }.get(topic, topic.replace("_", " "))


def _format_currency(value: Decimal | int | float | str | None, currency: str) -> str:
    if value is None:
        return "sin monto"
    number = _as_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if currency == "USD":
        return f"${int(number):,}"
    return "₡" + f"{int(number):,}".replace(",", ".")


def _format_crc(value: Decimal | int | float | str | None) -> str:
    return _format_currency(value, "CRC")


def _format_pct(value: Decimal | int | float | str) -> str:
    d = _as_decimal(value)
    if d == d.to_integral_value():
        return f"{d.quantize(Decimal('1'))}%"
    return f"{d.quantize(Decimal('0.1'))}%"


def _format_abs_pct(value: Decimal | int | float | str) -> str:
    d = _as_decimal(value).copy_abs()
    return _format_pct(d)


def _format_signed_pct(value: Decimal | int | float | str) -> str:
    d = _as_decimal(value)
    if d > 0:
        return f"subiendo {_format_abs_pct(d)}"
    if d < 0:
        return f"bajando {_format_abs_pct(d)}"
    return "estable"


def _format_signed_pct_value(value: Decimal | int | float | str) -> str:
    d = _as_decimal(value)
    if d == d.to_integral_value():
        return f"{d.quantize(Decimal('1'))}%"
    return f"{d.quantize(Decimal('0.1'))}%"


def _format_decimal(value: Decimal | int | float | str, places: int = 1) -> str:
    q = Decimal("1") if places <= 0 else Decimal("0." + ("0" * (places - 1)) + "1")
    return format(_as_decimal(value).quantize(q, rounding=ROUND_HALF_UP), "f")


def _format_date(value: date) -> str:
    return f"{value.day} de {_MONTH_NAMES_ES[value.month]} de {value.year}"


def _truncate(value: str, max_len: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _as_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
