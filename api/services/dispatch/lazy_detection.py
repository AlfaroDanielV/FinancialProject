"""Phase 6d B8 lazy detection helpers.

The LLM only extracts a free-form hint. Matching that hint against the
user's saved accounts stays deterministic so the write path remains
auditable.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Sequence

from rapidfuzz import fuzz, utils

from ...data.bank_directory_cr import BANK_DIRECTORY_CR
from ...models.account import Account

LAZY_DETECTION_THRESHOLD = 0.85
AMBIGUOUS_MATCH_MARGIN = 0.03
GENERIC_ACCOUNT_HINTS = {"banco", "cuenta", "tarjeta", "card", "account"}


@dataclass(frozen=True)
class AccountHintMatch:
    status: Literal["matched", "ambiguous", "unknown", "no_hint"]
    account: Account | None
    score: float | None


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only.lower()).strip()


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalize(value))


def _score(hint: str, account_name: str) -> float:
    hint_norm = _normalize(hint)
    name_norm = _normalize(account_name)
    hint_compact = _compact(hint)
    name_compact = _compact(account_name)

    scores = [
        fuzz.WRatio(hint, account_name, processor=utils.default_process) / 100,
    ]
    if hint_norm and hint_norm == name_norm:
        scores.append(1.0)
    if hint_compact and hint_compact == name_compact:
        scores.append(1.0)
    if hint_norm and hint_norm in name_norm:
        scores.append(0.92)
    if hint_compact and hint_compact in name_compact:
        scores.append(0.92)
    return round(max(scores), 4)


def classify_hint_type(hint: str) -> Literal["account", "bank"]:
    hint_compact = _compact(hint)
    for bank in BANK_DIRECTORY_CR:
        slug = _compact(bank["slug"])
        name = _compact(bank["name"])
        if hint_compact and (
            hint_compact == slug
            or hint_compact == name
            or slug in hint_compact
            or name in hint_compact
        ):
            return "bank"
    return "account"


def match_account_hint(
    hint: str | None,
    accounts: Sequence[Account],
    *,
    threshold: float = LAZY_DETECTION_THRESHOLD,
) -> AccountHintMatch:
    if not hint or not hint.strip():
        return AccountHintMatch(status="no_hint", account=None, score=None)

    normalized = _normalize(hint)
    if normalized in GENERIC_ACCOUNT_HINTS:
        return AccountHintMatch(status="ambiguous", account=None, score=None)

    if not accounts:
        return AccountHintMatch(status="unknown", account=None, score=None)

    # An exact name match wins outright. `_normalize` preserves the currency
    # symbol (only `_compact` strips ₡/$), so dual-currency cards named
    # "Promerica ₡" and "Promerica $" stay distinguishable here. Without this,
    # both compact to "promerica" and tie at 1.0 below → a permanent
    # "ambiguous" loop no reply can break (a tapped button label is routed back
    # as a text hint and re-normalized just like a typed one). The partial
    # unique index accounts(user_id, name) WHERE is_active guarantees at most
    # one active account per exact name.
    exact = [a for a in accounts if _normalize(a.name) == normalized]
    if len(exact) == 1:
        return AccountHintMatch(status="matched", account=exact[0], score=1.0)

    scored = sorted(
        ((_score(hint, account.name), account) for account in accounts),
        key=lambda pair: pair[0],
        reverse=True,
    )
    top_score, top_account = scored[0]
    if top_score < threshold:
        return AccountHintMatch(status="unknown", account=None, score=top_score)
    if len(scored) > 1 and (top_score - scored[1][0]) < AMBIGUOUS_MATCH_MARGIN:
        return AccountHintMatch(status="ambiguous", account=None, score=top_score)
    return AccountHintMatch(status="matched", account=top_account, score=top_score)
