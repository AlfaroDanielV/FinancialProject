"""Two-step analyzer for user-supplied bank notification samples.

Step 1 (vision, optional): if the sample arrives as an image, run Haiku
vision to extract the plain text of the email. The prompt asks the
model to act as an OCR transcriber, NOT to interpret the contents. The
output is raw text that step 2 parses.

Step 2 (text): given the raw text, ask Haiku to identify
    - sender_email     (the From: address)
    - bank_name        (Promerica / BAC / Davivienda / unknown)
    - format_signature (jsonb dict of distinctive substrings/regex
                        we can use to recognize this format later)
    - confidence       (0..1)

Output is `SampleAnalysis`. Confidence < 0.7 means the bot should ask
for another sample. Step 2 uses tool-use for structured output — same
trick as the Phase 5b extractor, different schema.

Why two steps and not one big multimodal call: vision tokens are
expensive ($3/MTok input vs $0.80 for Haiku text). By default we try
the cheap step alone when the user pastes text; vision only fires when
they actually send a photo. Splitting also makes the analyzer testable
without real Anthropic calls (we mock per step).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from anthropic import AsyncAnthropic
from anthropic import APIError as AnthropicAPIError
from anthropic import APITimeoutError as AnthropicTimeoutError


log = logging.getLogger("api.services.gmail.sample_analyzer")


# ── result type ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SampleAnalysis:
    raw_text: str
    sender_email: Optional[str]
    bank_name: Optional[str]
    format_signature: dict[str, Any]
    confidence: float


# ── protocol so tests can swap out the real Anthropic client ─────────────────


class SampleAnalyzerClient(Protocol):
    async def extract_text_from_image(
        self, image_bytes: bytes, *, mime_type: str = "image/jpeg"
    ) -> str: ...

    async def analyze_text(self, raw_text: str) -> SampleAnalysis: ...


class SampleAnalyzerError(RuntimeError):
    """Wraps every failure mode so the bot can map to one Spanish reply."""


# ── prompts ──────────────────────────────────────────────────────────────────

# We deliberately keep these short and CR-anchored. The bank list is
# the realistic shortlist for the personal MVP — not exhaustive. The
# tool schema constrains output even when the model goes off-script.

_VISION_INSTRUCTION = (
    "Sos un OCR. Extraé exactamente el texto plano del correo en la "
    "imagen, incluyendo encabezados visibles (De, Para, Asunto). No "
    "interpretes, no resumas, no agregues información. Si no se ve "
    "claro, marcá las partes ilegibles con [ilegible]. Responde sólo "
    "con el texto extraído, sin comentarios."
)

_TEXT_SYSTEM = (
    "Sos un clasificador de correos bancarios de Costa Rica. Recibís el "
    "texto plano de un correo y devolvés (vía la herramienta) el "
    "remitente, el banco, una firma de formato (claves o regex que "
    "identifiquen este tipo de correo) y un nivel de confianza entre 0 "
    "y 1.\n\n"
    "Bancos comunes en Costa Rica: BAC Credomatic, Promerica, Davivienda, "
    "Banco Nacional, Banco de Costa Rica, Scotiabank, Banco Popular. "
    "Si no podés identificar el banco con certeza razonable, devolvé "
    "bank_name=null y confidence < 0.7.\n\n"
    "format_signature debe ser un objeto JSON con claves útiles para "
    "detectar el formato más adelante: por ejemplo "
    '{"subject_pattern": "Notificación de transacción", '
    '"key_phrases": ["compra realizada", "monto", "comercio"], '
    '"amount_label": "Monto"}.'
)

_TEXT_TOOL = {
    "name": "report_sample_analysis",
    "description": "Reporta el resultado del análisis del sample de correo bancario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sender_email": {
                "type": ["string", "null"],
                "description": "La dirección de correo del remitente, o null si no se distingue.",
            },
            "bank_name": {
                "type": ["string", "null"],
                "description": "Nombre canónico del banco (BAC, Promerica, etc.) o null.",
            },
            "format_signature": {
                "type": "object",
                "description": "Diccionario JSON con campos útiles para reconocer el formato.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "required": [
            "sender_email",
            "bank_name",
            "format_signature",
            "confidence",
        ],
    },
}


# ── real Anthropic-backed client ─────────────────────────────────────────────


class AnthropicSampleAnalyzer:
    """Production client. One Anthropic SDK instance per process.

    Uses Haiku for both steps because vision-on-Haiku is cheap and the
    classification task is simple. If accuracy proves poor on actual CR
    bank samples, swap vision step to Sonnet 4.6 — vision quality
    matters more there than for the text classification.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-haiku-4-5",
        timeout_s: float = 12.0,
    ) -> None:
        if not api_key:
            raise SampleAnalyzerError("ANTHROPIC_API_KEY missing")
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._timeout_s = timeout_s

    async def extract_text_from_image(
        self, image_bytes: bytes, *, mime_type: str = "image/jpeg"
    ) -> str:
        import base64

        b64 = base64.b64encode(image_bytes).decode("ascii")
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": _VISION_INSTRUCTION},
                        ],
                    }
                ],
                timeout=self._timeout_s,
            )
        except AnthropicTimeoutError as e:
            raise SampleAnalyzerError(f"vision_timeout: {e}") from e
        except AnthropicAPIError as e:
            raise SampleAnalyzerError(f"vision_api_error: {e}") from e

        text_blocks = [
            getattr(b, "text", "")
            for b in getattr(resp, "content", [])
            if getattr(b, "type", None) == "text"
        ]
        text = "\n".join(t for t in text_blocks if t).strip()
        if not text:
            raise SampleAnalyzerError("vision_returned_empty")
        return text

    async def analyze_text(self, raw_text: str) -> SampleAnalysis:
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=600,
                system=_TEXT_SYSTEM,
                tools=[_TEXT_TOOL],
                tool_choice={"type": "tool", "name": _TEXT_TOOL["name"]},
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Acá va el texto del correo a clasificar:\n\n"
                            + raw_text[:8000]  # safety cap for token cost
                        ),
                    }
                ],
                timeout=self._timeout_s,
            )
        except AnthropicTimeoutError as e:
            raise SampleAnalyzerError(f"text_timeout: {e}") from e
        except AnthropicAPIError as e:
            raise SampleAnalyzerError(f"text_api_error: {e}") from e

        tool_input = _first_tool_input(resp)
        if tool_input is None:
            raise SampleAnalyzerError(
                f"text_no_tool_use: stop_reason={getattr(resp, 'stop_reason', '?')}"
            )
        return _coerce_analysis(raw_text, tool_input)


def _first_tool_input(resp: Any) -> Optional[dict[str, Any]]:
    for block in getattr(resp, "content", []):
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    return None


def _coerce_analysis(raw_text: str, payload: dict[str, Any]) -> SampleAnalysis:
    """Defensive coercion of the tool output. The Pydantic-equivalent is
    inlined because we already constrain the schema via tool_use; we
    just need to be tolerant of types Anthropic might wobble on."""
    sender = payload.get("sender_email")
    if isinstance(sender, str):
        sender = sender.strip() or None
    if sender is not None and not isinstance(sender, str):
        sender = None

    bank = payload.get("bank_name")
    if isinstance(bank, str):
        bank = bank.strip() or None
    if bank is not None and not isinstance(bank, str):
        bank = None

    sig = payload.get("format_signature") or {}
    if not isinstance(sig, dict):
        # Last-ditch try: if the model returned a string, parse JSON;
        # otherwise drop to empty dict.
        try:
            sig = json.loads(sig) if isinstance(sig, str) else {}
        except (TypeError, ValueError):
            sig = {}

    try:
        conf = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    return SampleAnalysis(
        raw_text=raw_text,
        sender_email=sender,
        bank_name=bank,
        format_signature=sig,
        confidence=conf,
    )


# ── high-level helper used by the bot handler ────────────────────────────────


CONFIDENCE_THRESHOLD = 0.7
MAX_SAMPLE_ATTEMPTS = 3


async def analyze_text_sample(
    raw_text: str, *, client: SampleAnalyzerClient
) -> SampleAnalysis:
    """Tiny wrapper kept symmetrical with `analyze_image_sample` so the
    bot handler doesn't branch on input type — it just calls the right
    helper."""
    return await client.analyze_text(raw_text)


async def analyze_image_sample(
    image_bytes: bytes,
    *,
    client: SampleAnalyzerClient,
    mime_type: str = "image/jpeg",
) -> SampleAnalysis:
    text = await client.extract_text_from_image(
        image_bytes, mime_type=mime_type
    )
    return await client.analyze_text(text)
