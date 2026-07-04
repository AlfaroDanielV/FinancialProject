"""P10 B11 — Option C: `run_advisory` — structural "no invented number".

Option A (B2/B3) trusts prompt discipline: the agentic loop holds read-only
tools and the persona says "never invent a number". Option C makes that a
STRUCTURAL guarantee:

1. **Freeze a deterministic context-pack** — `Finding{label, value,
   source_engine}` rows gathered from the single-owner engines
   (financial_state, cashflow, net worth, matched principles as a `framing`
   field). No LLM anywhere in the gathering.
2. **Narrate with NO DB tools** — one completion; the narrator physically
   cannot fetch or recompute anything (`tools=[]`).
3. **Gate-D scorer** — every numeric token in the narration must exist in
   the pack (small structural integers up to twelve are allowed: list
   ordinals, months of the year). A violation is a guardrail MISS, not a
   user-facing error…
4. **…so it degrades to the template fallback**: a deterministic voseo
   rendering of the honest numbers — never a fabricated verdict, never a
   bare error.
5. **One `advice_events` row** (kind=advisory_assessment, surface=
   run_advisory) + the standard `llm_query_dispatches` telemetry row — the
   same audit choke points as Option A.

Routing: `bot/pipeline._route_extraction` sends advisory turns here when
`settings.advisory_option_c_enabled` is on; Option A otherwise. Same per-turn
resolver, same feature flag family, write path untouched either way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from api.config import settings
from api.data.principle_library_cr import match_principles
from api.models.llm_query_dispatch import LLMQueryDispatch
from api.models.user import User
from api.services.advice_trace import record_advice_event
from api.services.budget import assert_within_budget
from api.services.finance.cashflow import compute_monthly_cashflow
from api.services.finance.financial_state import classify_for_user
from api.services.finance.net_worth import compute_net_worth

from ..delivery import BudgetExceeded, handle_query_error
from ..dispatcher import DispatchOutcome, get_query_llm_client
from ..dateutil import build_date_context
from ..prompts.system import (
    _ADVISORY_PERSONA,
    _PRINCIPLE_FRAMING,
    _RULES,
    _date_block,
    _first_name_from,
    _persona,
)
from ..session import AsyncSessionLocal

log = logging.getLogger("app.queries.advisory")

# Small structural integers the narrator may use freely (ordinals, months of
# the year, "las tres bandas"). Anything larger must trace to the pack.
_FREE_INT_CEILING = 12


@dataclass(frozen=True)
class Finding:
    label: str
    value: str
    source_engine: str


@dataclass(frozen=True)
class ContextPack:
    findings: tuple[Finding, ...]
    framing: tuple[dict[str, Any], ...]  # matched principles (HOW, no numbers)
    financial_state: str
    currency: str


# ── the static narrator contract (number-free, brace-free) ────────────────────

_PACK_CONTRACT = """\
Vas a recibir un paquete de hallazgos deterministas (etiqueta, valor, motor \
de origen) y, aparte, principios de encuadre. Reglas absolutas:
- Narrá usando SOLO los números del paquete, con el formato que gustés. Si \
un dato que necesitás no está en el paquete, decí honestamente que no lo \
tenés — jamás lo estimés ni lo completés.
- No tenés herramientas este turno: no podés consultar nada más.
- Los principios de encuadre dan tono y estructura, nunca cifras ni \
veredictos.
- Cerrá con un siguiente paso concreto anclado a los hallazgos."""


def _build_narrator_prompt(user: User, now: datetime) -> str:
    first_name = _first_name_from(user.full_name)
    ctx = build_date_context(user.timezone, now)
    return "\n\n".join(
        [
            _persona(first_name),
            _ADVISORY_PERSONA,
            _date_block(ctx),
            _RULES,
            _PRINCIPLE_FRAMING,
            _PACK_CONTRACT,
        ]
    )


# ── pack building (deterministic, single owners only) ─────────────────────────


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


async def build_context_pack(db, *, user: User) -> ContextPack:
    reading = await classify_for_user(db, user=user)
    cashflow = await compute_monthly_cashflow(db, user=user)
    net = await compute_net_worth(db, user=user)

    findings: list[Finding] = [
        Finding("estado_financiero", reading.state.value, "financial_state"),
    ]
    if cashflow.income_known:
        findings.append(
            Finding("ingreso_mensual", _money(cashflow.monthly_income), "cashflow")
        )
    findings.extend(
        [
            Finding(
                "comprometido_mensual_sobres",
                _money(cashflow.committed_outflows),
                "cashflow",
            ),
            Finding("sobrante_mensual", _money(cashflow.surplus), "cashflow"),
            Finding(
                "cuotas_deuda_mensuales", _money(cashflow.debt_payments), "cashflow"
            ),
        ]
    )
    if cashflow.gate_reason:
        findings.append(Finding("gate_reason", cashflow.gate_reason, "cashflow"))
    for entry in net.entries:
        findings.extend(
            [
                Finding(
                    f"activos_{entry.currency}", _money(entry.assets), "net_worth"
                ),
                Finding(
                    f"pasivos_{entry.currency}",
                    _money(entry.liabilities),
                    "net_worth",
                ),
                Finding(
                    f"patrimonio_{entry.currency}", _money(entry.net), "net_worth"
                ),
            ]
        )

    matched = match_principles(reading.state.value)
    framing = tuple(
        {
            "principle_id": p["principle_id"],
            "framing_template": p["framing_template"],
            "source": f"{p['source']['title']} — {p['source']['author']}",
        }
        for p in matched
    )
    return ContextPack(
        findings=tuple(findings),
        framing=framing,
        financial_state=reading.state.value,
        currency=cashflow.currency,
    )


# ── Gate-D scorer ─────────────────────────────────────────────────────────────

_NUM_TOKEN_RE = re.compile(r"\d[\d.,]*")


def _normalize_digits(token: str) -> str:
    return re.sub(r"[^\d]", "", token)


def _allowed_tokens(pack: ContextPack, now: datetime) -> set[str]:
    allowed: set[str] = {str(now.year)}
    for f in pack.findings:
        for raw in _NUM_TOKEN_RE.findall(f.value):
            digits = _normalize_digits(raw)
            if not digits:
                continue
            allowed.add(digits)
            # Common re-formattings of the same figure: with/without the
            # two decimal places, and the sign-free magnitude.
            try:
                dec = Decimal(raw.replace(",", ""))
            except InvalidOperation:
                continue
            if dec == dec.to_integral_value():
                allowed.add(str(int(abs(dec))))
            allowed.add(_normalize_digits(f"{abs(dec):.2f}"))
            allowed.add(_normalize_digits(f"{abs(dec):.0f}"))
    return allowed


def score_narration(narration: str, pack: ContextPack, *, now: datetime) -> list[str]:
    """Return the numeric tokens that do NOT trace to the pack (empty = pass)."""
    allowed = _allowed_tokens(pack, now)
    violations: list[str] = []
    for raw in _NUM_TOKEN_RE.findall(narration):
        digits = _normalize_digits(raw)
        if not digits:
            continue
        try:
            if int(digits) <= _FREE_INT_CEILING:
                continue
        except ValueError:
            pass
        if digits not in allowed:
            violations.append(raw)
    return violations


# ── template fallback (deterministic, honest numbers) ─────────────────────────

_STATE_ES = {
    "negative_surplus": "tus gastos comprometidos superan tu ingreso",
    "high_interest_debt": "estás cargando deuda cara",
    "irregular_income_stress": "no tengo un ingreso registrado para vos",
    "recent_overspend": "este mes se pasó al menos un sobre",
    "first_time_saving": "estás empezando a construir tu colchón",
    "stable_building": "vas construyendo sobre base estable",
}


def render_fallback(pack: ContextPack) -> str:
    lines = [
        "Te dejo el panorama con tus números reales "
        f"({_STATE_ES.get(pack.financial_state, pack.financial_state)}):",
    ]
    for f in pack.findings:
        if f.label in ("estado_financiero", "gate_reason"):
            continue
        lines.append(f"- {f.label.replace('_', ' ')}: {f.value}")
    gate = next((f.value for f in pack.findings if f.label == "gate_reason"), None)
    if gate == "no_income":
        lines.append(
            "Para darte un veredicto confiable necesito tu ingreso: "
            "registralo y lo vemos con calma."
        )
    elif gate == "no_budget":
        lines.append(
            "Para darte un veredicto confiable armá tus sobres primero — "
            "ahí sale tu sobrante real."
        )
    elif gate == "under_coverage":
        lines.append(
            "Tenés obligaciones sin sobre asignado — asignalas y el "
            "panorama queda confiable."
        )
    lines.append("¿Querés que veamos algo puntual de esta lista?")
    return "\n".join(lines)


# ── the orchestrator ──────────────────────────────────────────────────────────


def _pack_message(message_text: str, pack: ContextPack) -> str:
    payload = {
        "hallazgos": [
            {"etiqueta": f.label, "valor": f.value, "motor": f.source_engine}
            for f in pack.findings
        ],
        "encuadre": list(pack.framing),
        "moneda": pack.currency,
    }
    return (
        f"{message_text}\n\n"
        "[PAQUETE DETERMINISTA — única fuente de números]\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )


async def run_advisory(
    *,
    user_id: uuid.UUID,
    message_text: str,
    telegram_chat_id: int | None = None,
) -> DispatchOutcome:
    """Option C advisory turn. Same outward contract as `run_dispatch`."""
    del telegram_chat_id  # parity with run_dispatch's signature
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return DispatchOutcome(
                text="No encontré tu usuario. Probá /start.",
                error_category="user_not_found",
            )
        tz_name = getattr(user, "timezone", None) or "America/Costa_Rica"
        try:
            await assert_within_budget(user_id=user_id, db=db, tz_name=tz_name)
        except BudgetExceeded as e:
            return DispatchOutcome(
                text=handle_query_error(e, user_id=user_id),
                error_category="budget",
            )

        pack = await build_context_pack(db, user=user)
        system_prompt = _build_narrator_prompt(user, now)

        row = LLMQueryDispatch(
            user_id=user_id,
            message_hash=hashlib.sha256(message_text.encode("utf-8")).hexdigest(),
            tools_used=[{"name": "advisory_context_pack", "args_summary": {}}],
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)

        fallback_used = False
        violations: list[str] = []
        try:
            result = await get_query_llm_client().run_query_loop(
                system_prompt=system_prompt,
                user_message=_pack_message(message_text, pack),
                user_id=user_id,
                tools=[],  # ← the structural guarantee: NO DB tools
                tool_executor=_no_tools_executor,
                model=settings.llm_query_model,
                max_iterations=1,
            )
            narration = result.text
            violations = score_narration(narration, pack, now=now)
            if violations or not narration.strip():
                # Guardrail miss → honest numbers, never a fabricated verdict.
                log.warning(
                    "advisory_gate_d_violation user_id=%s tokens=%s",
                    user_id,
                    violations[:5],
                )
                narration = render_fallback(pack)
                fallback_used = True
            row.total_iterations = result.total_iterations
            row.total_input_tokens = result.total_input_tokens
            row.total_output_tokens = result.total_output_tokens
            row.final_response_chars = len(narration)
            row.duration_ms = int((time.perf_counter() - started) * 1000)
            await db.commit()
        except Exception as e:  # noqa: BLE001 — degrade to honest numbers
            log.exception("run_advisory_llm_failed user_id=%s", user_id)
            narration = render_fallback(pack)
            fallback_used = True
            row.error = str(e)[:500]
            row.duration_ms = int((time.perf_counter() - started) * 1000)
            await db.commit()

    await record_advice_event(
        user_id=user_id,
        kind="advisory_assessment",
        verdict=f"state:{pack.financial_state}"[:30],
        inputs={
            "financial_state": pack.financial_state,
            "matched_principle_ids": [f["principle_id"] for f in pack.framing],
            "option": "C",
            "gate_d_violations": violations[:5],
            "fallback_used": fallback_used,
        },
        result={
            "findings": [
                {"label": f.label, "value": f.value, "source": f.source_engine}
                for f in pack.findings
            ],
        },
        surface="run_advisory",
    )
    return DispatchOutcome(
        text=narration,
        dispatch_id=None,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


async def _no_tools_executor(name: str, args: dict, user_id: uuid.UUID) -> Any:
    raise RuntimeError(
        f"Option C narrator requested tool {name!r} — it has none by design."
    )
