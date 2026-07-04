"""P10 B9 (+B7) — narration integration + the two CI gates.

CI Gate 1 — **no-number-in-principle (D5)**: no live principle's narratable
text (framing_template, core_idea, hope_anchor, cultural_flags) contains or
implies a figure — CR-aware (digits, ₡, %, «millones», «mil», «por ciento»).
`excluded_tactics` is exempt (it legitimately holds «10%», «401(k)» — the
tactics a principle must NEVER surface).

CI Gate 2 — **human-review completeness (Gate E)**: a live record missing
source / reviewed_by / scope / applies_when / forbidden_when / hope_anchor
FAILS the merge.

Plus: `get_framing_principles` derives financial_state SERVER-SIDE, emits
zero numbers, traces kind=advisory_assessment; and the B7 memory modifiers
(archetype + risk posture insights) re-RANK matched principles without ever
selecting (D9/O1).

Prereqs (DB tests): `docker compose up -d db redis` + `alembic upgrade head`.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.data.principle_library_cr import PRINCIPLES_CR

# ── CI Gate 1: no-number-in-principle ────────────────────────────────────────

# The B11 runtime word-number rule is the single source for spelled-out
# magnitudes — the CI gate stays at parity with it (adversarial-review fix:
# the two scorers had drifted).
from app.queries.advisory.orchestrator import _WORD_NUMBER_RE  # noqa: E402

# CR-aware figure detector. Word-bounded so «familia»/«similar» never match.
_NUMBER_PATTERNS = [
    re.compile(r"[0-9]"),
    re.compile(r"₡"),
    re.compile(r"%"),
    re.compile(r"\bpor\s+ciento\b", re.IGNORECASE),
    _WORD_NUMBER_RE,  # mil(es)/millón(es)/cien(tos)/quinientos/…
    # A spelled small number attached to a time unit is a TIMELINE figure
    # («seis meses», «dos años») — principles must stay timeless.
    re.compile(
        r"\b(un[oa]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
        r"\s+(mes(?:es)?|añ[oa]s?|semanas?|d[ií]as?|quincenas?)\b",
        re.IGNORECASE,
    ),
]


def _find_number(text: str) -> str | None:
    for pat in _NUMBER_PATTERNS:
        m = pat.search(text)
        if m:
            return f"{pat.pattern!r} matched {text[max(0, m.start()-20):m.end()+20]!r}"
    return None


def _narratable_texts(principle: dict) -> list[tuple[str, str]]:
    """Every text field the narrator could surface — excluded_tactics exempt."""
    out = [
        ("framing_template", principle["framing_template"]),
        ("core_idea", principle["core_idea"]),
        ("hope_anchor", principle["hope_anchor"]),
    ]
    if principle["cultural_flags"]:
        for key, value in principle["cultural_flags"].items():
            out.append((f"cultural_flags.{key}", value))
    return out


def test_ci_gate_no_number_in_any_live_principle():
    violations = []
    for p in PRINCIPLES_CR:
        for field, text in _narratable_texts(p):
            hit = _find_number(text)
            if hit:
                violations.append(f"{p['principle_id']}.{field}: {hit}")
    assert not violations, "\n".join(violations)


def test_scorer_detects_cr_figures():
    # The scorer itself must catch the CR-specific shapes — including the
    # spelled-out ones the B11 runtime rule catches (parity, review fix).
    for bad in (
        "ahorrá ₡50.000 al mes",
        "guardá el 10% del salario",
        "dos millones en seis meses",
        "unos quinientos mil colones",
        "en 6 meses lo lográs",
        "medio millón de colchón",
        "apartá unos cientos de miles",
        "en seis meses lo lográs",
        "dale dos años al plan",
        "guardá el diez por ciento",
    ):
        assert _find_number(bad) is not None, bad
    # And must NOT flag figure-free framing prose.
    for good in (
        "validá la lógica histórica detrás de su aversión al riesgo",
        "una familia similar a la suya, sin milagros",
        "presentá el siguiente paso como un hábito a mantener",
    ):
        assert _find_number(good) is None, good


# ── CI Gate 2: human-review completeness (Gate E) ─────────────────────────────


def test_ci_gate_every_live_record_is_review_complete():
    problems = []
    for p in PRINCIPLES_CR:
        pid = p["principle_id"]
        if not (p["source"].get("title") and p["source"].get("author")):
            problems.append(f"{pid}: source incomplete")
        if not p["provenance"].get("reviewed_by"):
            problems.append(f"{pid}: reviewed_by missing")
        if not p["provenance"].get("distilled_by"):
            problems.append(f"{pid}: distilled_by missing")
        if p["scope"] not in ("coaching", "clinical_boundary"):
            problems.append(f"{pid}: scope invalid")
        if not p["applies_when"].get("financial_state"):
            problems.append(f"{pid}: applies_when.financial_state empty")
        if not isinstance(p["forbidden_when"], list):
            problems.append(f"{pid}: forbidden_when not a list")
        if not isinstance(p["requires_positive_state"], bool):
            problems.append(f"{pid}: requires_positive_state not bool")
        if not p["hope_anchor"]:
            problems.append(f"{pid}: hope_anchor missing")
        if not p["framing_template"]:
            problems.append(f"{pid}: framing_template missing")
        if p["centrality"] not in ("core", "supporting", "peripheral"):
            problems.append(f"{pid}: centrality invalid")
    assert not problems, "\n".join(problems)


def test_staging_records_would_fail_the_completeness_gate():
    """Staging is inert by construction — but if someone pasted a staging
    record into the live module unreviewed, Gate 2 must catch it."""
    staging = json.loads(
        open("scripts/principles/staging.json", encoding="utf-8").read()
    )
    assert staging, "staging.json vacío — corré scripts/import_principles.py"
    for rec in staging:
        assert not rec["provenance"].get("reviewed_by")  # would fail Gate 2


# ── get_framing_principles (B9 dynamic half + B7 modifiers) ───────────────────


def _insight_row(user_id, insight_type: str, content: dict, dedup: str):
    from api.models.user_insight import UserInsight

    now = datetime.now(timezone.utc)
    return UserInsight(
        id=uuid.uuid4(),
        user_id=user_id,
        insight_type=insight_type,
        content=content,
        confidence=Decimal("0.90"),
        source="computed",
        valid_until=now + timedelta(days=30),
        user_locked=False,
        dedup_key=dedup,
        reinforcement_count=1,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_framing_tool_state_is_server_derived_and_number_free(db_with_user):
    from api.models.advice_event import AdviceEvent
    from app.queries.tools.framing import get_framing_principles

    session, user_id = db_with_user
    payload = await get_framing_principles(user_id=user_id)

    # Fresh user: no income → the single owner classifies income stress.
    assert payload["financial_state"] == "irregular_income_stress"
    assert payload["matched_count"] >= 1
    ids = [p["principle_id"] for p in payload["principles"]]
    assert "paradoja_esperanza_loteria" in ids

    # ZERO figures anywhere in what the narrator receives.
    for p in payload["principles"]:
        for field in ("framing_template", "core_idea"):
            assert _find_number(p[field]) is None, (p["principle_id"], field)
        if p["cultural_flags"]:
            for v in p["cultural_flags"].values():
                assert _find_number(v) is None

    # Advice-traced with the matched ids (D11).
    rows = (
        await session.execute(
            select(AdviceEvent).where(
                AdviceEvent.user_id == user_id,
                AdviceEvent.kind == "advisory_assessment",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].result["matched_principle_ids"] == ids
    assert rows[0].inputs["financial_state"] == "irregular_income_stress"


@pytest.mark.asyncio
async def test_b7_insights_rerank_but_never_select(db_with_user):
    from app.queries.tools.framing import get_framing_principles

    session, user_id = db_with_user

    # Without insights: paradoja (3 states) is more specific than the broad
    # validacion (6 states) at equal core weight → paradoja leads.
    before = await get_framing_principles(user_id=user_id)
    assert before["principles"][0]["principle_id"] == "paradoja_esperanza_loteria"

    # money_avoidance + conservative risk boost validacion (+0.5 +0.5) past
    # paradoja (+0.5) — a RE-RANK among already-matched peers (D9)...
    session.add_all(
        [
            _insight_row(
                user_id,
                "archetype",
                {
                    "type": "archetype",
                    "primary": "money_avoidance",
                    "secondary": None,
                    "confidence": "0.85",
                    "evidence_summary": "evita revisar sus finanzas",
                },
                "archetype",
            ),
            _insight_row(
                user_id,
                "risk_posture",
                {
                    "type": "risk_posture",
                    "posture": "conservative",
                    "evidence_basis": "observed",
                },
                "risk_posture",
            ),
        ]
    )
    await session.commit()

    after = await get_framing_principles(user_id=user_id)
    assert after["financial_state"] == "irregular_income_stress"
    assert (
        after["principles"][0]["principle_id"]
        == "validacion_logica_individual_historica"
    )
    # ...and NEVER a selection: the matched set is state-bound either way.
    ids_after = {p["principle_id"] for p in after["principles"]}
    assert "vulnerabilidad_del_intelecto_sin_control" not in ids_after
    assert "primacia_comportamiento_disciplinado" not in ids_after
