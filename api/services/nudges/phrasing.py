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
                max_tokens=200,
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
