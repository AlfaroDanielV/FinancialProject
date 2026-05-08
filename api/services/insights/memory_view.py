"""Phase 6c B7 — grouping, rendering, and deletion helpers for the
user-facing memory commands (`/memoria`, `/olvidar`, `/editar_memoria`).

Pure functions, no aiogram. The bot handler (and later the B8 privacy
endpoint) call into here. Spanish phrasing is delegated to
`app.queries.tools.user_context._describe_insight` so we have a single
source of natural-language insight descriptions.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user_insight import UserInsight
from api.schemas.insights import (
    USER_EDITABLE_TYPES,
    InsightContent,
    validate_insight_content,
)
from app.queries.tools.user_context import _describe_insight

from .persister import audit

log = logging.getLogger("api.services.insights.memory_view")


# ── grouping ────────────────────────────────────────────────────────────────


GROUP_PATRONES = "patrones"
GROUP_METAS = "metas"
GROUP_CONOZCO = "conozco"
GROUP_BANDERAS = "banderas"

GROUP_LABELS_ES: dict[str, str] = {
    GROUP_PATRONES: "📊 Patrones",
    GROUP_METAS: "🎯 Tus metas",
    GROUP_CONOZCO: "🧭 Cómo te conozco",
    GROUP_BANDERAS: "🚩 Banderas",
}

# Order is the render order: most user-relevant first.
GROUP_ORDER: tuple[str, ...] = (
    GROUP_METAS,
    GROUP_CONOZCO,
    GROUP_PATRONES,
    GROUP_BANDERAS,
)

_TYPE_TO_GROUP: dict[str, str] = {
    # Patrones — computed from the ledger.
    "spending_pattern": GROUP_PATRONES,
    "recurring_drift": GROUP_PATRONES,
    "cash_flow_stability": GROUP_PATRONES,
    "debt_load": GROUP_PATRONES,
    "emergency_fund": GROUP_PATRONES,
    "cr_seasonal_pattern": GROUP_PATRONES,
    "behavioral_flag": GROUP_PATRONES,
    # Metas — what the user has stated they're aiming for.
    "stated_goal": GROUP_METAS,
    # Cómo te conozco — preferences and style profile.
    "stated_preference": GROUP_CONOZCO,
    "archetype": GROUP_CONOZCO,
    "risk_posture": GROUP_CONOZCO,
    "decision_style": GROUP_CONOZCO,
    "financial_literacy": GROUP_CONOZCO,
    # Banderas — derived contradictions surfaced by lifecycle.
    "stated_observed_gap": GROUP_BANDERAS,
}


def group_for(insight_type: str) -> str:
    return _TYPE_TO_GROUP.get(insight_type, GROUP_PATRONES)


# ── rendering ───────────────────────────────────────────────────────────────


# UX rule: anything below this gets a low-confidence disclaimer.
LOW_CONFIDENCE_THRESHOLD = Decimal("0.50")


@dataclass
class RenderedInsight:
    insight_id: uuid.UUID
    insight_type: str
    bullet_html: str
    short_label: str  # for inline buttons; ≤45 chars
    confidence: Decimal
    user_locked: bool
    editable: bool


@dataclass
class MemoryView:
    groups: dict[str, list[RenderedInsight]] = field(default_factory=dict)
    total: int = 0


async def load_active_insights(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
    insight_types: Iterable[str] | None = None,
) -> list[UserInsight]:
    """Return active rows for `user_id` ordered confidence DESC, updated DESC."""
    effective_now = now or datetime.now(timezone.utc)
    stmt = (
        select(UserInsight)
        .where(
            UserInsight.user_id == user_id,
            UserInsight.valid_until > effective_now,
        )
        .order_by(
            desc(UserInsight.confidence),
            desc(UserInsight.updated_at),
        )
    )
    if insight_types is not None:
        types = tuple(insight_types)
        if not types:
            return []
        stmt = stmt.where(UserInsight.insight_type.in_(types))
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


def render_view(insights: list[UserInsight]) -> MemoryView:
    """Group + render a set of insights into Spanish HTML bullets."""
    view = MemoryView()
    for row in insights:
        try:
            content: InsightContent = validate_insight_content(row.content)
        except Exception as exc:  # noqa: BLE001 - defensive read path
            log.warning(
                "memory_view_invalid_insight user_id=%s insight_id=%s err=%s",
                row.user_id,
                row.id,
                type(exc).__name__,
            )
            continue
        confidence = _as_decimal(row.confidence)
        prefix = "⚠️ " if confidence < LOW_CONFIDENCE_THRESHOLD else ""
        description = _describe_insight(content)
        bullet = f"• {prefix}{description}"
        rendered = RenderedInsight(
            insight_id=row.id,
            insight_type=row.insight_type,
            bullet_html=bullet,
            short_label=_short_label(content),
            confidence=confidence,
            user_locked=bool(row.user_locked),
            editable=row.insight_type in USER_EDITABLE_TYPES,
        )
        group = group_for(row.insight_type)
        view.groups.setdefault(group, []).append(rendered)
        view.total += 1
    return view


def render_memoria_text(view: MemoryView, *, footer: str) -> str:
    """Compose the full /memoria reply. Caller handles chunking."""
    if view.total == 0:
        return EMPTY_MEMORIA + "\n\n" + footer
    lines: list[str] = []
    for group_key in GROUP_ORDER:
        items = view.groups.get(group_key) or []
        if not items:
            continue
        lines.append(f"<b>{GROUP_LABELS_ES[group_key]}</b>")
        for item in items:
            lines.append(item.bullet_html)
        lines.append("")
    rendered = "\n".join(lines).rstrip() + "\n\n" + footer
    return rendered


def render_group_summary_lines(view: MemoryView) -> list[str]:
    """One line per non-empty group. Used by the /olvidar todo first
    confirmation so the user sees what they're about to delete."""
    out: list[str] = []
    for group_key in GROUP_ORDER:
        items = view.groups.get(group_key) or []
        if not items:
            continue
        out.append(
            f"• {GROUP_LABELS_ES[group_key]}: {len(items)}"
        )
    return out


# ── deletion ────────────────────────────────────────────────────────────────


async def delete_insights(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    insight_ids: list[uuid.UUID] | None = None,
    deletion_reason: str,
    actor: str = "user",
) -> int:
    """Hard-delete insights for one user, writing a typed audit row each.

    When `insight_ids` is None, every active row for the user is deleted.
    Returns the number of rows deleted.

    Caller owns the transaction; this flushes but does not commit.
    """
    stmt = select(UserInsight).where(UserInsight.user_id == user_id)
    if insight_ids is not None:
        if not insight_ids:
            return 0
        stmt = stmt.where(UserInsight.id.in_(insight_ids))
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return 0

    now = datetime.now(timezone.utc)
    for row in rows:
        snapshot = _redacted_snapshot(row.insight_type, row.content)
        await audit(
            session,
            user_id=user_id,
            insight_id=row.id,
            actor=actor,  # type: ignore[arg-type]
            payload={
                "action": "deleted",
                "insight_type": row.insight_type,
                "dedup_key": row.dedup_key,
                "content_at_deletion": snapshot,
                "confidence_at_deletion": _as_decimal(row.confidence),
                "deletion_reason": deletion_reason,
            },
            created_at=now,
        )
        await session.delete(row)
    await session.flush()
    return len(rows)


def _redacted_snapshot(insight_type: str, content: dict) -> dict:
    """Mirror lifecycle's privacy rule: never persist the raw quote in
    a deletion audit. We keep the rest of the payload because it's
    the user's own data and helps a future undo."""
    if insight_type == "stated_preference" and "raw_quote" in content:
        return {**content, "raw_quote": "[eliminado por privacidad]"}
    return dict(content)


# ── locked audit ────────────────────────────────────────────────────────────


async def emit_locked_audit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    insight_id: uuid.UUID,
    insight_type: str,
    dedup_key: str,
    content: dict,
) -> None:
    """Write a `locked` audit row when /editar_memoria flips a row to
    user_override. Persister doesn't emit this on its own — the
    transition from non-locked to locked is bot-driven."""
    await audit(
        session,
        user_id=user_id,
        insight_id=insight_id,
        actor="user",
        payload={
            "action": "locked",
            "insight_type": insight_type,
            "dedup_key": dedup_key,
            "content": content,
            "locked_via": "editar_memoria",
        },
    )


# ── short labels for inline buttons ─────────────────────────────────────────


def _short_label(content: InsightContent) -> str:
    """≤45 chars. Used as the visible text on /olvidar and /editar_memoria
    inline buttons. We can't put the whole bullet on a button — Telegram
    truncates and looks ugly."""
    match content.type:
        case "spending_pattern":
            return _trim(f"Gasto en {content.category}", 45)
        case "recurring_drift":
            return _trim(
                f"Drift en factura {str(content.bill_id)[:8]}", 45
            )
        case "cash_flow_stability":
            return "Flujo de caja"
        case "debt_load":
            return "Carga de deuda"
        case "emergency_fund":
            return "Fondo de emergencia"
        case "cr_seasonal_pattern":
            return _trim(f"Estacional: {content.event}", 45)
        case "stated_preference":
            return _trim(f"Pref: {content.topic}", 45)
        case "stated_goal":
            return _trim(f"Meta: {content.goal_text}", 45)
        case "behavioral_flag":
            return _trim(f"Bandera: {content.flag_type}", 45)
        case "archetype":
            return _trim(f"Arquetipo: {content.primary}", 45)
        case "risk_posture":
            return _trim(f"Riesgo: {content.posture}", 45)
        case "decision_style":
            return _trim(f"Estilo: {content.style}", 45)
        case "financial_literacy":
            return _trim(f"Nivel: {content.level}", 45)
        case "stated_observed_gap":
            return "Bandera (decís vs hago)"
    return _trim(content.type, 45)


def _trim(text: str, max_len: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# ── empty-state copy (lives here, not in messages_es, so the API can
#    reuse it for the future view-only export) ─────────────────────────────


EMPTY_MEMORIA = (
    "Todavía no aprendí nada concreto sobre vos. Conversá conmigo unos "
    "días y volvé a pegarle a /memoria."
)
