"""P10 B8 — principle library: corpus integrity + deterministic matcher.

Locks: the data-module financial_state mirror stays in sync with the frozen
service enum; the matcher selects on financial_state ALONE (archetype/risk
only re-weight — D9/O1); requires_positive_state + forbidden_when are
machine-enforced (L8); un-reviewed records are inert (Gate E); top-k caps the
flood; the import script never stages a live principle.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import api.data.principle_library_cr as lib
from api.data.principle_library_cr import (
    FINANCIAL_STATE_LABELS,
    NEGATIVE_STATES,
    PRINCIPLES_CR,
    match_principles,
)

REPO = Path(__file__).resolve().parent.parent


def test_state_labels_mirror_the_frozen_service_enum():
    from api.services.finance.financial_state import ALL_FINANCIAL_STATES

    assert FINANCIAL_STATE_LABELS == ALL_FINANCIAL_STATES
    assert NEGATIVE_STATES <= FINANCIAL_STATE_LABELS


def test_every_live_principle_targets_only_frozen_states():
    for p in PRINCIPLES_CR:
        assert set(p["applies_when"]["financial_state"]) <= FINANCIAL_STATE_LABELS, (
            p["principle_id"]
        )
        assert set(p["forbidden_when"]) <= FINANCIAL_STATE_LABELS, p["principle_id"]


def test_negative_state_matches_the_bad_state_principles():
    matched = match_principles("negative_surplus")
    ids = [p["principle_id"] for p in matched]
    assert "paradoja_esperanza_loteria" in ids
    # requires_positive_state + forbidden_when both bar this one:
    assert "vulnerabilidad_del_intelecto_sin_control" not in ids
    # primacia is forbidden_when=[negative_surplus]:
    assert "primacia_comportamiento_disciplinado" not in ids
    assert len(matched) <= 2  # top-k flood control


def test_archetype_reweights_but_never_selects():
    # paradoja targets money_avoidance but NOT stable_building — the archetype
    # alone must never pull it in (D9: state dominates).
    matched = match_principles("stable_building", archetype="money_avoidance")
    ids = [p["principle_id"] for p in matched]
    assert "paradoja_esperanza_loteria" not in ids
    # But within matched peers the archetype re-orders: money_status boosts
    # primacia above the broad validacion.
    boosted = match_principles("stable_building", archetype="money_status")
    assert boosted[0]["principle_id"] == "primacia_comportamiento_disciplinado"


def test_intent_boost_outranks_archetype_boost():
    matched = match_principles(
        "first_time_saving", intent="plan_retirement", archetype="money_avoidance"
    )
    # inexperiencia (core + intent 1.0 = 4.0) beats paradoja
    # (core + archetype 0.5 = 3.5) — behavioral signal above archetype.
    assert matched[0]["principle_id"] == "inexperiencia_evolutiva_modernidad"


def test_risk_signal_reweights_validacion():
    matched = match_principles("stable_building", risk_signal="low_tolerance")
    assert matched[0]["principle_id"] == "validacion_logica_individual_historica"


def test_unreviewed_record_is_inert(monkeypatch):
    ghost = dict(PRINCIPLES_CR[0])
    ghost["principle_id"] = "ghost_sin_revision"
    ghost["forbidden_when"] = []
    ghost["provenance"] = {**ghost["provenance"], "reviewed_by": None}
    monkeypatch.setattr(lib, "PRINCIPLES_CR", PRINCIPLES_CR + [ghost])
    for state in FINANCIAL_STATE_LABELS:
        ids = [p["principle_id"] for p in lib.match_principles(state, top_k=99)]
        assert "ghost_sin_revision" not in ids


def test_clinical_boundary_scope_is_never_selected(monkeypatch):
    clinical = dict(PRINCIPLES_CR[0])
    clinical["principle_id"] = "ghost_clinico"
    clinical["forbidden_when"] = []
    clinical["scope"] = "clinical_boundary"
    monkeypatch.setattr(lib, "PRINCIPLES_CR", PRINCIPLES_CR + [clinical])
    for state in FINANCIAL_STATE_LABELS:
        ids = [p["principle_id"] for p in lib.match_principles(state, top_k=99)]
        assert "ghost_clinico" not in ids


def test_matcher_is_deterministic():
    a = match_principles("first_time_saving", archetype="money_avoidance")
    b = match_principles("first_time_saving", archetype="money_avoidance")
    assert [p["principle_id"] for p in a] == [p["principle_id"] for p in b]


def test_import_script_is_idempotent_and_preserves_live(tmp_path):
    staging = tmp_path / "staging.json"
    cmd = [
        sys.executable,
        str(REPO / "scripts" / "import_principles.py"),
        "--raw-dir", str(REPO / "scripts" / "principles" / "raw"),
        "--staging", str(staging),
    ]
    first = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "live-preserved 5" in first.stdout  # the 5 Housel live records
    records = json.loads(staging.read_text())
    staged_ids = {r["principle_id"] for r in records}
    live_ids = {p["principle_id"] for p in PRINCIPLES_CR}
    assert staged_ids.isdisjoint(live_ids)
    # Human-only fields are never auto-filled in staging.
    for r in records:
        assert r["provenance"]["reviewed_by"] is None
        assert r["hope_anchor"] is None or "raw" in json.dumps(r["_import"])
        assert r["scope"] is None
    # Second run: nothing new, everything re-distilled in place.
    second = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "new 0" in second.stdout
    assert len(json.loads(staging.read_text())) == len(records)
