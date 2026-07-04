"""P10 B8 — curated behavioral-finance principle library (CR-localized).

Follows the curated-static-data precedent (`api/data/__init__.py`,
`bank_directory_cr.py`): a literal, version-controlled Python module — the PR
IS the human-review gate (Gate A/E). NOT a DB table, NOT RAG (D10/L1).

THE ONE RULE (D5/L2): a principle shapes HOW a deterministic finding is
narrated (tone, framing, sequencing) — it NEVER produces, alters, or implies
a financial figure. `framing_template` is an INSTRUCTION to the narrator
(tone-neutral framing intent, voseo applied at narration), never final copy
and never a number. The CI no-number scorer enforces this on `core_idea` +
`framing_template` (`excluded_tactics` is exempt — it legitimately holds the
numeric/US tactics a principle must never surface).

`financial_state` labels come EXCLUSIVELY from the frozen enum in
`api/services/finance/financial_state.py` (D8) — the matcher consumes them,
never computes them. The money archetype is a RANKING MODIFIER, never a
selector (D9/L9); absent signals degrade gracefully (O1).

Inert-until-reviewed (Gate E): a record whose `provenance.reviewed_by` is
falsy can NEVER be selected — the matcher filters it structurally. Un-reviewed
distillations live in `scripts/principles/staging.json`, not here.

`source_illustration` holds the book's anecdote for AUDIT ONLY — it is never
narrated (copyright + the abstraction discipline).
"""
from __future__ import annotations

from typing import Optional, TypedDict

# The frozen financial_state labels (mirror of
# api/services/finance/financial_state.FinancialState — data modules stay
# dependency-free of the service layer; a test locks the two in sync).
FINANCIAL_STATE_LABELS: frozenset[str] = frozenset(
    {
        "negative_surplus",
        "high_interest_debt",
        "irregular_income_stress",
        "recent_overspend",
        "first_time_saving",
        "stable_building",
    }
)

# States where hope must be earned, not manufactured (D6). A principle with
# `requires_positive_state=True` never fires on these — and the Gate-D
# tie-break prefers confront-the-numbers framings here.
NEGATIVE_STATES: frozenset[str] = frozenset(
    {
        "negative_surplus",
        "high_interest_debt",
        "irregular_income_stress",
        "recent_overspend",
    }
)

_CENTRALITY_WEIGHT = {"core": 3, "supporting": 2, "peripheral": 1}


class Principle(TypedDict):
    principle_id: str
    source: dict  # {title, author, locator} — human-attributed
    centrality: str  # core | supporting | peripheral
    core_idea: str  # the abstracted principle, our words — NOT the anecdote
    source_illustration: Optional[str]  # book anecdote — AUDIT ONLY
    mechanism: dict  # named psychological mechanisms
    applies_when: dict  # {financial_state:[...], money_archetype:[...], risk_signal:[...], intent:[...]}
    framing_template: str  # tone-neutral framing INTENT — instruction, not copy
    hope_anchor: str  # grounded-encouragement rationale + when NOT to use
    requires_positive_state: bool  # L8 machine-readable guardrail
    forbidden_when: list  # financial_state labels where this must NOT fire
    excluded_tactics: list  # tactics this principle must never surface (exempt from the no-number scorer)
    cultural_flags: Optional[dict]  # CR localization (L4)
    scope: str  # coaching | clinical_boundary
    provenance: dict  # {distilled_by, reviewed_by, localized}


# ─────────────────────────────────────────────────────────────────────────────
# Live corpus — only REVIEWED records. applies_when for these five was locked
# by the operator in the plan (§4.2 starter set, 2026-07-01); the remaining
# human fields were drafted at B8 and ride the same B8 sign-off gate.
# ─────────────────────────────────────────────────────────────────────────────

_HOUSEL = {"title": "The Psychology of Money", "author": "Morgan Housel"}
_PROVENANCE = {
    "distilled_by": "NotebookLM (scripts/principles/raw/psychology_of_money.json)",
    "reviewed_by": "Daniel — applies_when per plan §4.2 (2026-07-01); resto en gate B8",
    "localized": True,
}

PRINCIPLES_CR: list[Principle] = [
    {
        "principle_id": "primacia_comportamiento_disciplinado",
        "source": {**_HOUSEL, "locator": "Introducción (Read vs. Fuscone)"},
        "centrality": "core",
        "core_idea": (
            "El resultado financiero de largo plazo depende más del "
            "comportamiento sostenido — calma, constancia, disciplina — que "
            "de la capacidad técnica o la inteligencia para analizar números."
        ),
        "source_illustration": (
            "Ronald Read, conserje sin formación, acumuló una fortuna por "
            "disciplina e interés compuesto; Richard Fuscone, ejecutivo de "
            "Merrill Lynch con MBA de Harvard, colapsó por apalancamiento y "
            "soberbia. AUDIT ONLY — nunca narrar la anécdota."
        ),
        "mechanism": {
            "identidad_señalizacion": (
                "El gasto se usa como señal de estatus; la identidad está en "
                "conflicto entre ser rico y parecer rico."
            ),
            "brecha_cognitiva": (
                "Diferencia crítica entre saber qué hacer y lo que pasa en la "
                "mente al intentar hacerlo."
            ),
        },
        "applies_when": {
            "financial_state": ["stable_building", "recent_overspend"],
            "money_archetype": ["money_status", "money_worship"],
            "risk_signal": [],
            "intent": [],
        },
        "framing_template": (
            "Encuadrá la conversación en la conducta sostenida, no en la "
            "sofisticación técnica: reconocé lo que la persona ya hace bien "
            "con constancia y presentá el siguiente paso como un hábito a "
            "mantener cuando el entorno se ponga difícil, no como un cálculo "
            "más inteligente. Sin vergüenza, sin sermón."
        ),
        "hope_anchor": (
            "La esperanza sale de que la disciplina es aprendible y ya hay "
            "evidencia de conducta sostenida en sus propios datos. NO usarlo "
            "para insinuar que un déficit actual es un fallo de carácter — "
            "en estados malos, primero los números y las palancas concretas."
        ),
        "requires_positive_state": False,
        "forbidden_when": ["negative_surplus"],
        "excluded_tactics": [
            "Ahorro del 10% del salario",
            "Fondos de emergencia de 6 meses",
        ],
        "cultural_flags": None,
        "scope": "coaching",
        "provenance": _PROVENANCE,
    },
    {
        "principle_id": "vulnerabilidad_del_intelecto_sin_control",
        "source": {**_HOUSEL, "locator": "Introducción (el ejecutivo y las monedas de oro)"},
        "centrality": "supporting",
        "core_idea": (
            "La pericia técnica sin control emocional es un riesgo, no una "
            "ventaja: el ego convierte la inteligencia en apalancamiento "
            "emocional y en exceso de confianza."
        ),
        "source_illustration": (
            "El ejecutivo tecnológico de Housel que tiraba monedas de oro al "
            "mar por diversión, contrastado con ahorradores promedio "
            "estables. AUDIT ONLY."
        ),
        "mechanism": {
            "exceso_de_confianza": (
                "Creer que el éxito en un área técnica se traduce en dominio "
                "del mercado."
            ),
            "miopia_del_riesgo": (
                "Incapacidad de visualizar el colapso tras una racha de "
                "éxitos fortuitos."
            ),
        },
        "applies_when": {
            "financial_state": ["stable_building"],
            "money_archetype": [],
            "risk_signal": ["high_tolerance"],
            "intent": [],
        },
        "framing_template": (
            "Cuando la persona proyecta mucha confianza técnica o apetito de "
            "riesgo, validá su capacidad y a la vez reencuadrá el rol del "
            "asistente: proteger su seguridad, no validar su ego. Invitá a "
            "que las decisiones pasen por los números reales antes que por "
            "la convicción."
        ),
        "hope_anchor": (
            "Encuadre preventivo para gente que va bien y se siente "
            "invencible. NO usarlo con alguien en déficit o sin ingreso "
            "estable — ahí suena a regaño y no aplica."
        ),
        "requires_positive_state": True,
        "forbidden_when": ["negative_surplus", "irregular_income_stress"],
        "excluded_tactics": [
            "Estrategias de inversión en metales preciosos (oro)",
            "Apalancamiento técnico específico",
        ],
        "cultural_flags": None,
        "scope": "coaching",
        "provenance": _PROVENANCE,
    },
    {
        "principle_id": "validacion_logica_individual_historica",
        "source": {**_HOUSEL, "locator": "Cap. «No One's Crazy»"},
        "centrality": "core",
        "core_idea": (
            "Las decisiones financieras nacen de la historia personal, no de "
            "una hoja de cálculo: la experiencia vivida pesa más que la "
            "educación financiera, y esa lógica individual merece respeto "
            "antes de proponer cualquier cambio."
        ),
        "source_illustration": None,
        "mechanism": {
            "anclaje_emocional": (
                "Fijación en eventos económicos pasados (inflación, crisis) "
                "que dictan el miedo presente."
            ),
            "aversion_a_la_perdida": (
                "La respuesta emocional al riesgo es asimétrica y "
                "profundamente personal."
            ),
        },
        "applies_when": {
            "financial_state": sorted(FINANCIAL_STATE_LABELS),
            "money_archetype": ["money_vigilance", "money_avoidance"],
            "risk_signal": ["low_tolerance"],
            "intent": [],
        },
        # The plan's worked reframe example (§4.2), verbatim in intent.
        "framing_template": (
            "Validá la lógica histórica detrás de su aversión al riesgo: si "
            "la persona mencionó una experiencia económica que vivió, "
            "nombrala; reconocé que esa precaución la protegió entonces y es "
            "razonable, antes de proponer cualquier ajuste. Si no contó "
            "ninguna experiencia, validá la prudencia sin inventarle "
            "historia. Voseo, sin cifras."
        ),
        "hope_anchor": (
            "La esperanza sale de que la precaución que la protegió sigue "
            "siendo un activo — solo se re-dirige. NO usarlo para justificar "
            "la inacción cuando los números piden un cambio concreto."
        ),
        "requires_positive_state": False,
        "forbidden_when": [],
        "excluded_tactics": [
            "Modelos de optimización de varianza media",
            "Test de perfil de riesgo de 10 preguntas",
        ],
        "cultural_flags": {
            "paternalismo_solidarista": (
                "El Solidarismo y sus excedentes anuales crearon una zona de "
                "confort de retornos percibidos como seguros — la "
                "volatilidad externa se vive como amenaza al patrimonio "
                "familiar."
            ),
            "anclaje_del_aguinaldo": (
                "El aguinaldo se usa como alivio emocional de fin de año más "
                "que como capital estratégico, por presión social y familia "
                "extendida."
            ),
        },
        "scope": "coaching",
        "provenance": _PROVENANCE,
    },
    {
        "principle_id": "paradoja_esperanza_loteria",
        "source": {**_HOUSEL, "locator": "Cap. «No One's Crazy» (lotería)"},
        "centrality": "core",
        "core_idea": (
            "El gasto en azar y esperanza no es un error de cálculo sino una "
            "válvula emocional cuando el camino tradicional se siente "
            "impenetrable; juzgarlo rompe la alianza, entenderlo permite "
            "construir una alternativa real."
        ),
        "source_illustration": None,
        "mechanism": {
            "contabilidad_mental": (
                "El dinero destinado a la suerte se segmenta como inversión "
                "en bienestar emocional."
            ),
            "sesgo_de_disponibilidad": (
                "La visibilidad de los ganadores alimenta la narrativa de "
                "que el azar es un camino viable."
            ),
        },
        "applies_when": {
            "financial_state": [
                "irregular_income_stress",
                "negative_surplus",
                "first_time_saving",
            ],
            "money_archetype": ["money_avoidance"],
            "risk_signal": [],
            "intent": [],
        },
        "framing_template": (
            "Si aparece gasto en lotería o azar, no lo juzgués: reconocé que "
            "representa la posibilidad de soñar con algo mejor, y proponé "
            "construir una «suerte programada» — un camino concreto y "
            "verificable hacia esa misma aspiración usando sus números "
            "reales. La aspiración se honra; el vehículo se cambia."
        ),
        "hope_anchor": (
            "Este principio EXISTE para los estados malos: convierte la "
            "esperanza difusa en un plan anclado a números reales sin "
            "quitarle a la persona el derecho a soñar. No aplica donde no "
            "hay señal de gasto aspiracional."
        ),
        "requires_positive_state": False,
        "forbidden_when": [],
        "excluded_tactics": [
            "Análisis de probabilidad de pérdida en juegos de azar",
            "Presupuesto estricto base cero",
        ],
        "cultural_flags": {
            "colectivismo_jps": (
                "El «pedacito» de la JPS es un rito colectivo — se comparte "
                "la suerte con colegas y familia, reforzando el cuido mutuo."
            ),
            "gordo_navideño": (
                "La suerte institucionalizada como el único «gran salto» "
                "económico anual."
            ),
        },
        "scope": "coaching",
        "provenance": _PROVENANCE,
    },
    {
        "principle_id": "inexperiencia_evolutiva_modernidad",
        "source": {**_HOUSEL, "locator": "Cap. «No One's Crazy» (somos novatos)"},
        "centrality": "core",
        "core_idea": (
            "Los sistemas financieros modernos — pensión individual, crédito "
            "de consumo, inversión — son demasiado nuevos para que alguien "
            "sea «malo con la plata»: todos somos novatos aprendiendo "
            "herramientas que nuestros abuelos no conocieron."
        ),
        "source_illustration": None,
        "mechanism": {
            "falta_memoria_historica": (
                "No hay memoria colectiva para instrumentos que no existían "
                "en el imaginario de nuestros abuelos."
            ),
            "paralisis_por_incertidumbre": (
                "Ante la novedad de los sistemas, el cerebro opta por la "
                "inacción."
            ),
        },
        "applies_when": {
            "financial_state": ["first_time_saving"],
            "money_archetype": [],
            "risk_signal": [],
            "intent": ["plan_retirement"],
        },
        "framing_template": (
            "Quitá la vergüenza de la inexperiencia: encuadrá el "
            "desconocimiento de pensiones o inversión como algo normal — el "
            "sistema es más joven que muchos de sus hábitos — y presentá el "
            "primer paso como aprendizaje acompañado, no como corrección de "
            "un error."
        ),
        "hope_anchor": (
            "Especialmente útil al abrir el tema de pensión (la transición "
            "CCSS→ROP es reciente en la psique tica). NO usarlo para "
            "minimizar una urgencia real — si los números piden acción ya, "
            "la normalización acompaña, no reemplaza, la palanca concreta."
        ),
        "requires_positive_state": False,
        "forbidden_when": [],
        "excluded_tactics": [
            "401(k)",
            "Roth IRA",
            "Planes de ahorro educativos específicos de EE.UU.",
        ],
        "cultural_flags": {
            "transicion_ccss_lpt": (
                "El paso del modelo paternalista de la CCSS a la Ley de "
                "Protección al Trabajador y el ROP es muy reciente — el "
                "usuario añora la seguridad del Estado y se siente "
                "desprotegido gestionando su pensión."
            ),
            "incertidumbre_del_rop": (
                "Desconfianza sistémica hacia los fondos complementarios por "
                "falta de historia larga de éxito en el país."
            ),
        },
        "scope": "coaching",
        "provenance": _PROVENANCE,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Matcher (Gate B) — deterministic, in-module, no LLM anywhere.
# ─────────────────────────────────────────────────────────────────────────────


def _is_selectable(p: Principle) -> bool:
    """Inert-until-reviewed + never clinical in normal flow (Gate D pre-filter)."""
    return bool(p["provenance"].get("reviewed_by")) and p["scope"] != "clinical_boundary"


def match_principles(
    financial_state: str,
    *,
    gate_reason: Optional[str] = None,
    archetype: Optional[str] = None,
    risk_signal: Optional[str] = None,
    intent: Optional[str] = None,
    top_k: int = 2,
) -> list[Principle]:
    """Deterministic principle selection for one user state.

    1. FILTER — `financial_state ∈ applies_when.financial_state`, state not in
       `forbidden_when`, scope not clinical, reviewed, and
       `requires_positive_state ⟹ state is not a NEGATIVE_STATE`.
       The deterministic state DOMINATES (D8/D9): archetype/risk NEVER select.
    2. RANK — centrality weight + intent match (+1); archetype (+0.5) and
       risk_signal (+0.5) only RE-WEIGHT, strictly below the intent signal
       and below a centrality tier, so they can re-order peers but never
       out-rank a stronger behavioral match (D9/O1). Absent signals simply
       contribute nothing (graceful degradation).
    3. TOP-K — default two; never flood the narration.
    4. TIE-BREAK (Gate D, anchored to financial_state): on a NEGATIVE state a
       more specific principle (fewer applies_when states — i.e. built FOR
       this situation) beats a broad feel-good one; final order is stable by
       principle_id so selection is reproducible.

    ``gate_reason`` is accepted for audit symmetry (it rides the advice trace)
    but does not alter selection in v1 — the state already encodes it.
    """
    del gate_reason  # v1: encoded in financial_state; kept for signature stability

    scored: list[tuple[float, int, str, Principle]] = []
    for p in PRINCIPLES_CR:
        if not _is_selectable(p):
            continue
        applies = p["applies_when"]
        if financial_state not in applies.get("financial_state", []):
            continue
        if financial_state in p["forbidden_when"]:
            continue
        if p["requires_positive_state"] and financial_state in NEGATIVE_STATES:
            continue

        score = float(_CENTRALITY_WEIGHT.get(p["centrality"], 1))
        if intent and intent in applies.get("intent", []):
            score += 1.0
        if archetype and archetype in applies.get("money_archetype", []):
            score += 0.5
        if risk_signal and risk_signal in applies.get("risk_signal", []):
            score += 0.5

        specificity = len(applies.get("financial_state", []))
        scored.append((score, specificity, p["principle_id"], p))

    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [p for _, _, _, p in scored[:top_k]]
