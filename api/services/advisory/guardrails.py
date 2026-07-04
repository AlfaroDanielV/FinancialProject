"""P10 B10 — deterministic distress detector (two tiers, D7/O3).

Runs PRE-LLM in `bot/pipeline.py::process_message` for BOTH channels. The
tier decision is a pattern net — an LLM judgment would be exactly the
non-determinism this architecture exists to avoid (D7: «a deterministic
detector, not an LLM judgment»).

- **Tier 2 (`"crisis"`)** — hopelessness about living / self-harm ideation.
  The pipeline SHORT-CIRCUITS with the warm hand-off to **línea 1322** (24/7,
  also reachable via 911) + **9-1-1** for imminent danger. The bot never
  assesses, diagnoses, or promises confidentiality. (Aquí Estoy is secondary
  only — limited hours — and deliberately not the primary pointer.)
- **Tier 1 (`"financial"`)** — despair ABOUT money (underwater, hopeless
  about debt) with no crisis signal. Ordinary money-stress must NOT be
  escalated to a suicide line: the message flows through normally (the
  advisory answer still helps) and the pipeline APPENDS a warm, non-clinical
  point-to-a-real-person note (someone trusted, an asesor financiero
  acreditado, their asociación solidarista), deduped per day so it never
  nags.

When uncertain the detector stays quiet (None) — a false crisis banner on a
finance question erodes trust faster than a missed vague phrasing; explicit
self-harm language always fires.
"""
from __future__ import annotations

import unicodedata
from typing import Literal, Optional

DistressTier = Literal["financial", "crisis"]


def _fold(text: str) -> str:
    lowered = text.strip().lower()
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(ch) != "Mn"
    )


# Tier 2 — explicit hopelessness-about-living / self-harm language. Kept to
# unambiguous phrasings; the idiom guard below removes the joking CR use.
_CRISIS_PATTERNS: tuple[str, ...] = (
    "no quiero seguir viviendo",
    "no quiero vivir",
    "quitarme la vida",
    "me quiero matar",
    "quiero matarme",
    "matarme",
    "suicid",  # suicidio / suicidarme / suicida
    "no vale la pena vivir",
    "mejor no estar",
    "mejor desaparecer",
    "hacerme dano",
    "acabar con todo",
    "ya no aguanto la vida",
    "terminar con mi vida",
)

# CR idioms that reuse crisis verbs harmlessly («me maté de risa»).
_CRISIS_IDIOM_GUARDS: tuple[str, ...] = (
    "de risa",
    "de la risa",
)

# Tier 1 — despair about the financial situation, no living/self-harm signal.
_FINANCIAL_DISTRESS_PATTERNS: tuple[str, ...] = (
    "no puedo mas con las deudas",
    "no puedo mas con esto",
    "ya no puedo mas",
    "estoy ahogado",
    "estoy ahogada",
    "me estoy ahogando",
    "estoy hundido",
    "estoy hundida",
    "no veo salida",
    "sin salida",
    "no hay salida",
    "estoy desesperado",
    "estoy desesperada",
    "me van a embargar",
    "lo voy a perder todo",
    "voy a perder todo",
    "no le veo sentido a seguir pagando",
    "esto no tiene arreglo",
)


def detect_distress(text: str) -> Optional[DistressTier]:
    """Deterministic two-tier distress classification. Crisis wins."""
    if not text:
        return None
    folded = _fold(text)
    for pattern in _CRISIS_PATTERNS:
        idx = folded.find(pattern)
        if idx == -1:
            continue
        tail = folded[idx + len(pattern): idx + len(pattern) + 24]
        if any(guard in tail for guard in _CRISIS_IDIOM_GUARDS):
            continue  # «me quiero matar de risa» — the CR idiom, not a signal
        return "crisis"
    if any(p in folded for p in _FINANCIAL_DISTRESS_PATTERNS):
        return "financial"
    return None
