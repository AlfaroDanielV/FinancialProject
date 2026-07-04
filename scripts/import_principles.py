#!/usr/bin/env python3
"""P10 B8 (Gate E) — import raw NotebookLM distillations into staging.

    uv run python scripts/import_principles.py [--raw-dir scripts/principles/raw]
                                               [--staging scripts/principles/staging.json]

Pipeline position (library doc §5 Gate E):

    book → NotebookLM distillation → THIS IMPORT (raw → staging) →
    human review (fills human-only fields, confirms LLM-proposed triggers,
    localizes) → promotion BY HAND into api/data/principle_library_cr.py
    (the PR is the review).

Invariants:
- **Additive to staging, keyed by (principle_id, source title).** Re-running
  replaces the same-key STAGING record (staging is unreviewed by
  construction) and preserves everything else.
- **Never overwrites a record already in the live module** — a raw id that
  exists in `PRINCIPLES_CR` logs "already reviewed" and is skipped. Reviewed
  work is structurally unloseable.
- **Human-only fields are never auto-filled** (`hope_anchor`, `scope`,
  `requires_positive_state`, `forbidden_when`; `applies_when` is
  LLM-proposed → human-confirmed). They land as null/absent + are listed in
  `_import.human_only_missing`.
- **Inert-until-reviewed is free:** staging never ships; the matcher reads
  only the live module, and a live record needs `provenance.reviewed_by`.

The four raw export shapes handled:
1. plan-schema (psychology_of_money): strategic_context/analytical_synthesis/
   behavioral_mechanisms/cultural_flags/tactic_excluded/framing_template.
2. mostly-cooked (total_money_makeover): source dict, core_idea, mechanism
   str, applies_when.situation prose, final-copy framing_template (usted).
3. near-runtime (your_money_or_your_life): source str, applies_when as a
   list of NON-enum states → moved to `_import.unmapped_states` for human
   mapping against the frozen financial_state enum.
4. chapter-summary (i_will_teach_you_to_be_rich): seccion/prosa_analitica —
   NOT atomic principles; staged whole with `needs_atomization: true`
   (library doc §4 granularity note: 1 chapter → N principles precedes
   mapping, a human/LLM-assisted step outside this script).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.data.principle_library_cr import (  # noqa: E402
    FINANCIAL_STATE_LABELS,
    PRINCIPLES_CR,
)

HUMAN_ONLY = ["hope_anchor", "scope", "requires_positive_state", "forbidden_when"]

_BOOK_TITLES = {
    "psychology_of_money": ("The Psychology of Money", "Morgan Housel"),
    "total_money_makeover": ("The Total Money Makeover", "Dave Ramsey"),
    "your_money_or_your_life": ("Your Money or Your Life", "Vicki Robin & Joe Dominguez"),
    "i_will_teach_you_to_be_rich": ("I Will Teach You to Be Rich", "Ramit Sethi"),
}


def _slug(text: str) -> str:
    folded = "".join(
        ch
        for ch in unicodedata.normalize("NFD", text.strip().lower())
        if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_")[:64]


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_dict(value, key: str) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return {key: str(value)}


def _skeleton(principle_id: str, source: dict, source_file: str) -> dict:
    return {
        "principle_id": principle_id,
        "source": source,
        "centrality": None,
        "core_idea": None,
        "source_illustration": None,
        "mechanism": None,
        "applies_when": {
            "financial_state": [],
            "money_archetype": [],
            "risk_signal": [],
            "intent": [],
        },
        "framing_template": None,
        "hope_anchor": None,
        "requires_positive_state": None,
        "forbidden_when": None,
        "excluded_tactics": [],
        "cultural_flags": None,
        "scope": None,
        "provenance": {
            "distilled_by": f"NotebookLM ({source_file})",
            "reviewed_by": None,
            "localized": False,
        },
        "_import": {
            "source_file": source_file,
            "review_status": "pending",
            "needs_atomization": False,
            "human_only_missing": list(HUMAN_ONLY) + ["applies_when (confirmar)"],
            "notes": [],
        },
    }


def _normalize_record(rec: dict, source_file: str, book_key: str) -> dict:
    title, author = _BOOK_TITLES.get(book_key, (book_key, "?"))

    # Shape 4 — chapter summary (needs atomization first).
    if "seccion" in rec:
        out = _skeleton(_slug(rec["seccion"]), {"title": title, "author": author, "locator": rec["seccion"]}, source_file)
        out["_import"]["needs_atomization"] = True
        out["_import"]["review_status"] = "needs_atomization"
        out["_import"]["raw"] = rec
        out["_import"]["notes"].append(
            "Resumen de capítulo, no principio atómico — atomizar (1 capítulo → N principios) antes del mapeo (doc §4)."
        )
        if isinstance(rec.get("framing_template"), str):
            out["framing_template"] = rec["framing_template"]
            out["_import"]["notes"].append(
                "framing_template es copy final — reescribir como INTENT tone-neutral en la revisión."
            )
        if rec.get("hope_anchor"):
            out["_import"]["notes"].append("hope_anchor crudo presente en _import.raw (validar humano).")
        if rec.get("cultural_flags"):
            out["cultural_flags"] = _as_dict(rec["cultural_flags"], "cr_flag")
        if rec.get("tactic_excluded"):
            out["excluded_tactics"] = _as_list(rec["tactic_excluded"])
        return out

    # Shape 1 — the plan-schema raw (Housel).
    if "strategic_context" in rec or "analytical_synthesis" in rec:
        out = _skeleton(rec["principle_id"], {"title": title, "author": author, "locator": None}, source_file)
        out["mechanism"] = _as_dict(rec.get("behavioral_mechanisms"), "named_mechanism")
        out["cultural_flags"] = _as_dict(rec.get("cultural_flags"), "cr_flag")
        out["provenance"]["localized"] = rec.get("cultural_flags") is not None
        out["excluded_tactics"] = _as_list(rec.get("tactic_excluded"))
        out["framing_template"] = rec.get("framing_template")
        out["_import"]["raw_reshape"] = {
            "strategic_context": rec.get("strategic_context"),
            "analytical_synthesis": rec.get("analytical_synthesis"),
        }
        out["_import"]["notes"].extend(
            [
                "core_idea: abstraer de strategic_context + analytical_synthesis; anécdotas → source_illustration.",
                "framing_template: reescribir de copy final → INTENT tone-neutral (voseo en narración).",
                "transition descartado (acopla el arco narrativo del libro).",
            ]
        )
        return out

    # Shapes 2 y 3 — mostly-cooked / near-runtime.
    src = rec.get("source")
    if isinstance(src, dict):
        source = {"title": src.get("title", title), "author": src.get("author", author), "locator": src.get("locator")}
    elif isinstance(src, str):
        source = {"title": title, "author": author, "locator": src}
    else:
        source = {"title": title, "author": author, "locator": None}

    out = _skeleton(rec["principle_id"], source, source_file)
    out["core_idea"] = rec.get("core_idea")
    out["mechanism"] = _as_dict(rec.get("mechanism"), "named_mechanism")
    out["cultural_flags"] = _as_dict(rec.get("cultural_flags"), "cr_flag")
    out["provenance"]["localized"] = rec.get("cultural_flags") is not None
    out["excluded_tactics"] = _as_list(rec.get("tactic_excluded"))
    out["framing_template"] = rec.get("framing_template")
    if rec.get("framing_template"):
        out["_import"]["notes"].append(
            "framing_template puede ser copy final (usted) — reescribir como INTENT tone-neutral."
        )
    if rec.get("hope_anchor"):
        out["_import"]["raw_hope_anchor"] = rec["hope_anchor"]
        out["_import"]["notes"].append(
            "hope_anchor crudo en _import.raw_hope_anchor — campo humano: validar/reescribir, nunca copiar a ciegas."
        )
    # centrality: LLM-proposed values allowed only if canonical; else keep raw.
    if rec.get("centrality") in ("core", "supporting", "peripheral"):
        out["centrality"] = rec["centrality"]
    elif rec.get("centrality"):
        out["_import"]["raw_centrality"] = rec["centrality"]
        out["_import"]["notes"].append("centrality no canónica — mapear a core|supporting|peripheral.")
    # applies_when: only frozen-enum states pass; the rest go to unmapped.
    raw_applies = rec.get("applies_when")
    unmapped: list[str] = []
    if isinstance(raw_applies, list):
        for state in raw_applies:
            if state in FINANCIAL_STATE_LABELS:
                out["applies_when"]["financial_state"].append(state)
            else:
                unmapped.append(str(state))
    elif isinstance(raw_applies, dict):
        out["_import"]["raw_applies_when"] = raw_applies
        unmapped.append("(dict de situación en prosa — mapear al enum)")
    if unmapped:
        out["_import"]["unmapped_states"] = unmapped
        out["_import"]["notes"].append(
            "applies_when trae estados fuera del enum congelado — mapear humano contra financial_state."
        )
    # Extra raw fields worth preserving for the reviewer.
    for extra in ("money_archetype", "risk_signal"):
        if rec.get(extra):
            out["_import"][f"raw_{extra}"] = rec[extra]
    if rec.get("scope"):
        out["_import"]["raw_scope"] = rec["scope"]
        out["_import"]["notes"].append("scope crudo presente — campo humano: confirmar coaching|clinical_boundary.")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="scripts/principles/raw")
    parser.add_argument("--staging", default="scripts/principles/staging.json")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    staging_path = Path(args.staging)
    live_ids = {p["principle_id"] for p in PRINCIPLES_CR}

    staging: dict[str, dict] = {}
    if staging_path.exists():
        for rec in json.loads(staging_path.read_text()):
            staging[f"{rec['principle_id']}|{rec['source'].get('title')}"] = rec

    imported = skipped_reviewed = replaced = 0
    for raw_file in sorted(raw_dir.glob("*.json")):
        book_key = raw_file.stem
        records = json.loads(raw_file.read_text())
        for rec in records:
            normalized = _normalize_record(rec, raw_file.name, book_key)
            pid = normalized["principle_id"]
            if pid in live_ids:
                print(f"  ~ {pid}: already reviewed (live in PRINCIPLES_CR) — preserved, not staged")
                skipped_reviewed += 1
                continue
            key = f"{pid}|{normalized['source'].get('title')}"
            if key in staging:
                replaced += 1
            else:
                imported += 1
            normalized["_import"]["imported_at"] = datetime.now(timezone.utc).isoformat()
            staging[key] = normalized

    staging_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(staging.values(), key=lambda r: (r["source"].get("title") or "", r["principle_id"]))
    staging_path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n")
    print(
        f"staging: {len(ordered)} records → {staging_path} "
        f"(new {imported}, re-distilled {replaced}, live-preserved {skipped_reviewed})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
