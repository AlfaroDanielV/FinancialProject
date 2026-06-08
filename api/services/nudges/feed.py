"""Native in-app nudge feed (pull surface).

Phase 5d delivers nudges to Telegram by *pushing* them: a worker phrases each
pending nudge with the LLM and sends it, marking the row `sent`. The native app
needs the opposite — a surface the user *pulls* open. Two consequences shape
this module:

1. **Render deterministically, not via the LLM.** The push path phrases with
   Haiku at send time and never persists the text. Coupling a screen the user
   opens to a live Anthropic call (latency, 12s timeout, uptime) would make the
   alerts list fragile. The payload already carries the real, deterministic
   numbers the evaluators computed, so we template them here. The push path
   keeps its LLM phrasing; this is the one surface that renders in code. (The
   numbers are identical either way — only the prose differs.)

2. **Push-pacing does NOT gate a pull.** Rate limit and quiet hours exist to
   avoid *interrupting* the user; they have no meaning on a screen the user
   chose to open. Per-type **silence** still applies — "stop showing me this"
   is the user's standing intent regardless of channel.

Read-only: building the feed never mutates a nudge (no status change, no LLM,
no commit), so the endpoint is a safe GET. State changes flow through the
existing `/nudges/{id}/act` and `/nudges/{id}/dismiss` actions.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user_nudge import UserNudge, UserNudgeSilence
from .delivery import NudgeButton, buttons_for


@dataclass
class NudgeFeedEntry:
    id: uuid.UUID
    nudge_type: str
    priority: str
    text: str
    created_at: datetime
    buttons: list[NudgeButton] = field(default_factory=list)


# ── deterministic Spanish (voseo CR) rendering ───────────────────────────────


def _money(currency: str, raw: Any) -> str:
    """Format a payload money string ("100000.00") for display. CRC uses the
    CR thousands separator (₡100.000); USD keeps two decimals ($1,234.56)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "—"
    if (currency or "CRC").upper() == "USD":
        return f"${value:,.2f}"
    return "₡" + f"{int(round(value)):,}".replace(",", ".")


def render_nudge_text(nudge_type: str, payload: dict[str, Any]) -> str:
    """One-to-two sentence CR-voseo card text built from the structured
    payload. Defensive: every field has a fallback so a malformed payload
    still renders something rather than raising on the feed."""
    if nudge_type == "over_commitment":
        currency = payload.get("currency") or "CRC"
        ratio = payload.get("committed_ratio_pct", 0)
        disposable = _money(currency, payload.get("monthly_disposable"))
        return (
            f"Tus gastos fijos y pagos de deuda ya consumen cerca del {ratio}% "
            f"de tu ingreso y te queda poco margen (≈{disposable} disponible al "
            "mes). ¿Querés revisar dónde aflojar?"
        )

    if nudge_type == "missing_income":
        txn = payload.get("txn_count_last_7d", 0)
        return (
            f"Registraste {txn} gastos hace poco pero no veo ningún ingreso del "
            "último mes. ¿Agregás tu ingreso para darte mejores consejos?"
        )

    if nudge_type == "stale_pending_confirmation":
        proposed = payload.get("proposed_action") or {}
        summary = proposed.get("summary_es") or "una propuesta de transacción"
        return (
            f"Tenés una propuesta sin confirmar: {summary} ¿La agregamos, la "
            "descartamos, o la dejás para después?"
        )

    if nudge_type == "upcoming_bill":
        snap = payload.get("snapshot") or {}
        name = snap.get("bill_name") or snap.get("title") or "un pago"
        currency = snap.get("currency") or "CRC"
        amount = snap.get("amount_expected") or snap.get("amount")
        due = payload.get("due_date", "")
        monto = _money(currency, amount) if amount is not None else "monto variable"
        tail = f" el {due}" if due else ""
        return f"Se viene {name} por {monto}{tail}. ¿Ya lo pagaste?"

    return "Tenés una notificación pendiente."


# ── feed builder ─────────────────────────────────────────────────────────────


async def _silenced_types(
    session: AsyncSession, *, user_id: uuid.UUID, now: datetime
) -> set[str]:
    stmt = select(UserNudgeSilence.nudge_type).where(
        UserNudgeSilence.user_id == user_id,
        UserNudgeSilence.silenced_until > now,
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def build_feed(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> list[NudgeFeedEntry]:
    """Pending nudges for the user, HIGH first then oldest, with actively
    silenced types removed and each rendered to display text + buttons.
    Read-only — never mutates a nudge."""
    effective_now = now or datetime.now(timezone.utc)

    silenced = await _silenced_types(session, user_id=user_id, now=effective_now)

    stmt = (
        select(UserNudge)
        .where(and_(UserNudge.user_id == user_id, UserNudge.status == "pending"))
        .order_by(
            (UserNudge.priority != "high"),  # 'high' → 0, 'normal' → 1
            UserNudge.created_at.asc(),
        )
    )
    result = await session.execute(stmt)

    feed: list[NudgeFeedEntry] = []
    for nudge in result.scalars().all():
        if nudge.nudge_type in silenced:
            continue
        feed.append(
            NudgeFeedEntry(
                id=nudge.id,
                nudge_type=nudge.nudge_type,
                priority=nudge.priority,
                text=render_nudge_text(nudge.nudge_type, nudge.payload),
                created_at=nudge.created_at,
                buttons=buttons_for(nudge.nudge_type),
            )
        )
    return feed
