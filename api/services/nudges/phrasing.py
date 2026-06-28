"""LLM phrasing for nudges.

The LLM turns a structured payload into one to two conversational
Spanish sentences (voseo CR). It never chooses WHAT to nudge or whether
to nudge — the evaluators + orchestrator + delivery filters already
decided. The LLM is a pure phrasing layer.

Why this lives separate from api.services.llm_extractor:
    The extractor uses forced tool-use + a structured schema. Nudge
    phrasing is plain text completion. Protocols differ enough that
    sharing the client would muddy the extractor's contract.

Model: same as the extractor (LLM_EXTRACTION_MODEL) by design — one model
to cache, one model to evaluate drift against.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import AsyncAnthropic
from anthropic import APIError as AnthropicAPIError
from anthropic import APITimeoutError as AnthropicTimeoutError


class PhrasingClientError(RuntimeError):
    """Raised when the LLM call fails in a way the delivery worker should
    surface. Delivery counts these as `failed`; the nudge stays pending."""


class NudgePhrasingClient(Protocol):
    async def phrase(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout_s: float = 12.0,
    ) -> str: ...


class AnthropicPhrasingClient:
    """Real Anthropic client for nudge phrasing. Single text completion,
    prompt caching on the system prompt (same pattern as the extractor
    uses for its tool/system blocks).
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise PhrasingClientError(
                "ANTHROPIC_API_KEY missing; cannot run nudge delivery."
            )
        self._client = AsyncAnthropic(api_key=api_key)

    async def phrase(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout_s: float = 12.0,
    ) -> str:
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        try:
            resp = await self._client.messages.create(
                model=model,
                max_tokens=250,
                system=system_blocks,
                messages=[{"role": "user", "content": user_prompt}],
                timeout=timeout_s,
            )
        except AnthropicTimeoutError as e:
            raise PhrasingClientError(f"phrasing_timeout: {e}") from e
        except AnthropicAPIError as e:
            raise PhrasingClientError(f"phrasing_api_error: {e}") from e

        for block in getattr(resp, "content", []):
            if getattr(block, "type", None) == "text":
                return (getattr(block, "text", "") or "").strip()
        raise PhrasingClientError(
            f"phrasing_no_text_block: stop_reason={resp.stop_reason!r}"
        )


@dataclass
class FixturePhrasingClient:
    """Test double. Returns `canned_text` no matter what, and records
    every call for assertions. Tests use this to exercise the delivery
    pipeline without hitting Anthropic."""

    canned_text: str = "Nudge de prueba — ¿confirmás?"
    calls: list[dict[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    async def phrase(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        timeout_s: float = 12.0,
    ) -> str:
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "model": model}
        )
        return self.canned_text


# ── prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Sos un asistente financiero personal para alguien en Costa Rica.

Reglas de estilo (no negociables):
- Siempre voseo costarricense. Usá "vos", "mandá", "tenés", "podés", "avisame".
  NUNCA uses "tú", "tienes", "puedes".
- Tono conversacional, cálido, directo. No sonás robot ni formal.
- Máximo DOS oraciones. Terminá siempre con una pregunta o un call-to-action
  claro.
- No inventés cifras, fechas, nombres ni montos que no estén en el contexto
  que te pasan. Si el contexto dice "₡5,000", no escribas "unos cinco mil" ni
  "alrededor de ₡5 mil".
- No hagás cálculos — transmití la información tal cual.

Tu salida es SOLO el texto del mensaje. Nada más. Sin comillas, sin prefijos,
sin sign-off."""


def build_user_prompt(nudge_type: str, payload: dict[str, Any]) -> str:
    """Dispatch to a per-type user-prompt builder. Unknown types fall to
    a minimal template so the delivery worker never crashes — the LLM just
    gets less context and writes something generic."""
    if nudge_type == "missing_income":
        return _prompt_missing_income(payload)
    if nudge_type == "stale_pending_confirmation":
        return _prompt_stale_pending(payload)
    if nudge_type == "upcoming_bill":
        return _prompt_upcoming_bill(payload)
    if nudge_type == "over_commitment":
        return _prompt_over_commitment(payload)
    if nudge_type == "duplicate_transaction":
        return _prompt_duplicate_transaction(payload)
    if nudge_type == "envelope_near_limit":
        return _prompt_envelope_near_limit(payload)
    if nudge_type == "shadow_review_pending":
        return _prompt_shadow_review_pending(payload)
    if nudge_type == "goal_achieved":
        return _prompt_goal_achieved(payload)
    if nudge_type == "debt_paid_off":
        return _prompt_debt_paid_off(payload)
    if nudge_type == "first_full_month":
        return _prompt_first_full_month(payload)
    if nudge_type == "under_budget_month":
        return _prompt_under_budget_month(payload)
    return (
        "Escribí un recordatorio breve al usuario. "
        f"Contexto raw: {payload!r}"
    )


def _prompt_missing_income(payload: dict[str, Any]) -> str:
    txn_count = payload.get("txn_count_last_7d", 0)
    window = payload.get("window_days", 7)
    lookback = payload.get("lookback_days", 30)
    return (
        f"Contexto: el usuario registró {txn_count} gastos en los últimos "
        f"{window} días, pero no tiene NINGÚN ingreso registrado en los "
        f"últimos {lookback} días.\n"
        "\n"
        "Escribí un nudge corto que: (1) le diga que ves los gastos pero "
        "falta info de ingresos, (2) le explique que lo necesitás para dar "
        "consejos útiles, y (3) le preguntes si quiere agregar su ingreso."
    )


def _prompt_stale_pending(payload: dict[str, Any]) -> str:
    proposed = payload.get("proposed_action") or {}
    summary = proposed.get("summary_es") or "una propuesta de transacción"
    created_at = payload.get("created_at", "")
    return (
        f"Contexto: hace más de 48h le propusiste al usuario \"{summary}\" "
        f"(propuesta creada: {created_at}) y nunca respondió ni confirmó ni "
        "rechazó.\n"
        "\n"
        "Escribí un nudge breve: recordale la propuesta usando el mismo "
        "resumen, y preguntale si la agregamos, la descartamos, o la dejamos "
        "para después."
    )


def _prompt_upcoming_bill(payload: dict[str, Any]) -> str:
    snap = payload.get("snapshot") or {}
    name = snap.get("bill_name") or snap.get("title") or "un pago"
    amount = snap.get("amount_expected") or snap.get("amount")
    currency = snap.get("currency") or "CRC"
    due_date = payload.get("due_date", "")
    amount_str = (
        f"{currency} {amount:,.0f}" if isinstance(amount, (int, float))
        else "monto variable"
    )
    return (
        "Contexto: al usuario se le viene un pago próximo.\n"
        f"- Concepto: {name}\n"
        f"- Monto esperado: {amount_str}\n"
        f"- Fecha de vencimiento: {due_date}\n"
        "\n"
        "Escribí un recordatorio breve y amable. Preguntale si ya lo pagó o "
        "si querés que le recordés mañana."
    )


def _prompt_over_commitment(payload: dict[str, Any]) -> str:
    currency = payload.get("currency") or "CRC"
    ratio = payload.get("committed_ratio_pct", 0)

    def _fmt(key: str) -> str:
        raw = payload.get(key)
        try:
            return f"{currency} {float(raw):,.0f}"
        except (TypeError, ValueError):
            return "monto desconocido"

    return (
        "Contexto: los gastos fijos y pagos de deuda del usuario ya consumen "
        f"cerca del {ratio}% de su ingreso mensual, dejándole poco margen.\n"
        f"- Ingreso mensual: {_fmt('monthly_income')}\n"
        f"- Gastos fijos: {_fmt('monthly_fixed_expenses')}\n"
        f"- Pagos de deuda: {_fmt('monthly_debt_payments')}\n"
        f"- Disponible que le queda: {_fmt('monthly_disposable')}\n"
        "\n"
        "Escribí un aviso breve y directo, sin alarmar: (1) decile que sus "
        "compromisos fijos están consumiendo gran parte del ingreso y le queda "
        "poco margen, (2) preguntale si quiere revisar dónde aflojar. No des "
        "consejo numérico ni inventés montos; usá solo las cifras del contexto."
    )


def _prompt_duplicate_transaction(payload: dict[str, Any]) -> str:
    currency = payload.get("currency") or "CRC"
    amount = payload.get("amount")
    try:
        monto = f"{currency} {float(amount):,.0f}"
    except (TypeError, ValueError):
        monto = "monto desconocido"
    merchant = payload.get("merchant") or "sin comercio"
    txn_date = payload.get("transaction_date", "")
    matched_date = payload.get("matched_date", "")
    return (
        "Contexto: el usuario registró un gasto que SE PARECE a uno que ya "
        "tenía guardado (mismo monto, fechas muy cercanas). Puede ser un "
        "duplicado.\n"
        f"- Gasto nuevo: {monto}, comercio \"{merchant}\", fecha {txn_date}\n"
        f"- Gasto parecido ya registrado: fecha {matched_date}\n"
        "\n"
        "Escribí un aviso breve: (1) decile que este gasto parece repetido, "
        "(2) preguntale si lo elimina o lo deja. No afirmes que ES un "
        "duplicado seguro; usá solo las cifras del contexto, sin inventar."
    )


def _prompt_envelope_near_limit(payload: dict[str, Any]) -> str:
    currency = payload.get("currency") or "CRC"
    name = payload.get("name") or "un sobre"
    pct = payload.get("pct", 0)
    stage = payload.get("stage", "near")

    def _fmt(key: str) -> str:
        raw = payload.get(key)
        try:
            return f"{currency} {float(raw):,.0f}"
        except (TypeError, ValueError):
            return "monto desconocido"

    if stage == "over":
        situacion = (
            f"el usuario ya se PASÓ del límite de su sobre \"{name}\": lleva "
            f"gastado {_fmt('spent')} de un límite de {_fmt('limit_amount')} "
            f"({pct}%)."
        )
        instruccion = (
            "Escribí un aviso breve y directo, sin regañar: (1) decile que se "
            "pasó de ese sobre y por cuánto va, (2) preguntale si quiere "
            "revisarlo."
        )
    else:
        situacion = (
            f"el usuario ya casi gasta todo su sobre \"{name}\": lleva "
            f"{_fmt('spent')} de un límite de {_fmt('limit_amount')} ({pct}%), "
            f"le quedan {_fmt('available')}."
        )
        instruccion = (
            "Escribí un aviso breve y amable: (1) decile que ya casi gasta ese "
            "sobre y cuánto le queda, (2) preguntale si quiere revisarlo."
        )

    return (
        f"Contexto: {situacion}\n"
        "\n"
        f"{instruccion} No des consejo numérico ni inventés montos; usá solo "
        "las cifras del contexto."
    )


def _prompt_shadow_review_pending(payload: dict[str, Any]) -> str:
    count = payload.get("count", 0)
    age = payload.get("oldest_age_days", 0)
    merchants = payload.get("sample_merchants") or []
    ejemplos = ", ".join(m for m in merchants[:3] if m)
    detalle = f" (por ejemplo: {ejemplos})" if ejemplos else ""
    return (
        "Contexto: el usuario tiene transacciones que llegaron por correo "
        "(Gmail) y quedaron en modo sombra esperando que las revise y "
        "apruebe.\n"
        f"- Cantidad sin revisar: {count}\n"
        f"- La más vieja lleva {age} días esperando{detalle}\n"
        "\n"
        "Escribí un recordatorio breve y amable: (1) decile cuántos "
        "movimientos de Gmail tiene sin revisar y que llevan días esperando, "
        "(2) preguntale si los revisa ahora. No inventés montos ni comercios "
        "fuera del contexto."
    )


# ── Phase 8 B5 — earned celebrations. These are POSITIVE: celebrate, don't
# warn. Same rule as every other prompt — use ONLY the numbers in the context,
# never invent. The deterministic feed renderer (feed.py) words the same data.


def _fmt_money(payload: dict[str, Any], key: str) -> str:
    currency = payload.get("currency") or "CRC"
    raw = payload.get(key)
    try:
        return f"{currency} {float(raw):,.0f}"
    except (TypeError, ValueError):
        return "monto desconocido"


def _prompt_goal_achieved(payload: dict[str, Any]) -> str:
    name = payload.get("name") or "su meta"
    monto = _fmt_money(payload, "target_amount")
    return (
        "Contexto: ¡el usuario ALCANZÓ una meta de ahorro! Es un logro real "
        "que merece celebrarse.\n"
        f"- Meta: \"{name}\"\n"
        f"- Monto alcanzado: {monto}\n"
        "\n"
        "Escribí un mensaje CORTO y genuinamente celebratorio (no exagerado): "
        "(1) felicitalo por llegar a la meta nombrándola, (2) reconocé el "
        "esfuerzo. Cálido y motivador. Usá solo las cifras del contexto."
    )


def _prompt_debt_paid_off(payload: dict[str, Any]) -> str:
    name = payload.get("name") or "su deuda"
    return (
        "Contexto: ¡el usuario TERMINÓ de pagar una deuda! Quedó en cero. Es "
        "uno de los logros financieros más importantes.\n"
        f"- Deuda saldada: \"{name}\"\n"
        "\n"
        "Escribí un mensaje CORTO y celebratorio: (1) felicitalo por saldar "
        "esa deuda nombrándola, (2) animalo a seguir. Cálido, directo, sin "
        "inventar montos."
    )


def _prompt_first_full_month(payload: dict[str, Any]) -> str:
    periodo = payload.get("period", "")
    return (
        "Contexto: el usuario completó su PRIMER mes llevando sus finanzas en "
        "la app. Es el hábito que hace la diferencia y vale reconocerlo.\n"
        f"- Primer mes registrado: {periodo}\n"
        "\n"
        "Escribí un mensaje CORTO y motivador: (1) felicitalo por completar su "
        "primer mes de seguimiento, (2) animalo a seguir. Sin inventar cifras."
    )


def _prompt_under_budget_month(payload: dict[str, Any]) -> str:
    name = payload.get("name") or "un sobre"
    periodo = payload.get("period", "")
    gastado = _fmt_money(payload, "spent")
    limite = _fmt_money(payload, "limit_amount")
    return (
        "Contexto: el usuario cerró el mes SIN pasarse del límite de uno de "
        "sus sobres (presupuesto). Cumplió su presupuesto.\n"
        f"- Sobre: \"{name}\"\n"
        f"- Mes: {periodo}\n"
        f"- Gastado: {gastado} de un límite de {limite}\n"
        "\n"
        "Escribí un mensaje CORTO y celebratorio: (1) felicitalo por respetar "
        "ese sobre nombrándolo y el mes, (2) reconocé la disciplina. Usá solo "
        "las cifras del contexto, sin inventar."
    )
