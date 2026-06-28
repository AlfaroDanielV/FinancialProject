"""Turn a rich semantic statement extraction into a deterministic reconcile plan.

`build_reconcile_plan` runs the whole type-agnostic pipeline:

  NORMALIZE → DEDUP → VALIDATE (conservation) → POLICY → RESOLVE (ledger)

and returns a `ReconcilePlan` of one `LegPlan` per (account × currency leg). The
plan COLLAPSES to the legacy per-target `StatementReconcileItem` list
(`auto_includable_items`) before the write, and PROJECTS to the back-compat
`StatementExtraction` (`plan_to_extraction`) for the form/chat. The only writer
is `reconcile_products`; this module never commits.

Pure given its inputs (the candidate accounts/debts are passed in). No LLM.

Key correctness points:
- **Bug 1 (wrong field):** the anchored balance is the policy-tagged role
  candidate, copied verbatim; conservation only VALIDATES it (and validates the
  LLM's role tag — a mistagged role won't reconcile).
- **Bug 2 (duplicated account):** identity = (issuer, product, currency leg);
  instruments/PANs are attribution only and collapse into the owner account.
- **Sign orientation:** conservation is computed signed in asset orientation; a
  LIABILITY's opening magnitude is negative, so an inflow (a card payment) reduces
  what's owed. We compare MAGNITUDES; the storage sign is applied only by the
  writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from .dispatch.lazy_detection import _compact, match_account_hint
from .statement_guardrails import verdict_for_debt
from .statement_policy import AccountPolicy, policy_for, select_target
from ..schemas.statements import (
    LegPlan,
    ReconcilePlan,
    ResolvedTarget,
    StatementExtraction,
    StatementExtractionV2,
    StatementProduct,
    StatementReconcileItem,
    StmtAccount,
    StmtCurrencyLeg,
    StmtVerification,
)

_CENT = Decimal("0.01")
_TOL = Decimal("0.05")  # absolute floor (5 céntimos / 5 US-cents)
_REL = Decimal("0.000001")  # relative floor for large balances


def _is_credit(account: Any) -> bool:
    return getattr(account, "account_type", None) == "credit"


def _norm_id(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").upper()


# ── NORMALIZE / DEDUP ─────────────────────────────────────────────────────────


@dataclass
class _Group:
    account_type: str
    bank: Optional[str]
    issuer: Optional[str]
    label: Optional[str]
    accounts: list[StmtAccount] = field(default_factory=list)
    legs_by_currency: dict[str, StmtCurrencyLeg] = field(default_factory=dict)


def _stmt_identity_sig(acct: StmtAccount) -> Optional[tuple]:
    """A stable identity signature for one StmtAccount, in priority order
    (IBAN → account_number → last4/PAN). Used as the grouping key so that two
    StmtAccounts the LLM split for the SAME physical card (sharing a PAN/IBAN)
    merge, while two genuinely DIFFERENT accounts never collapse. Returns None
    when the account carries no identifier at all."""
    for ident in acct.identifiers:
        if ident.kind == "iban" and ident.value.strip():
            return ("iban", _norm_id(ident.value))
    for ident in acct.identifiers:
        if ident.kind == "account_number" and ident.value.strip():
            return ("an", _norm_id(ident.value))
    for ident in acct.identifiers:
        if ident.kind in ("last4", "masked_pan"):
            digits = re.sub(r"\D", "", ident.value)
            if len(digits) >= 4:
                return ("l4", digits[-4:])
    for inst in acct.instruments:
        digits = re.sub(r"\D", "", inst.masked_pan or "")
        if len(digits) >= 4:
            return ("l4", digits[-4:])
    return None


def _group_accounts(stmt_accounts: list[StmtAccount], bank: Optional[str]) -> list[_Group]:
    """Collapse only StmtAccounts that share an identity signature (Bug 2 — one
    card the LLM split into several entries). Identity-less accounts get a unique
    per-index key so two distinct same-named accounts NEVER collapse (and no leg
    is silently dropped). One leg per currency within a group."""
    groups: dict[tuple, _Group] = {}
    order: list[tuple] = []
    for idx, acct in enumerate(stmt_accounts):
        sig = _stmt_identity_sig(acct)
        key: tuple = sig if sig is not None else ("__idx__", idx)
        grp = groups.get(key)
        if grp is None:
            grp = _Group(
                account_type=acct.account_type,
                bank=bank,
                issuer=acct.issuer or bank,
                label=acct.product_name or acct.issuer,
            )
            groups[key] = grp
            order.append(key)
        grp.accounts.append(acct)
        for leg in acct.currency_legs:
            cur = (leg.currency or "CRC").upper()
            existing = grp.legs_by_currency.get(cur)
            # Keep the leg that actually carries a closing candidate.
            if existing is None or (
                not existing.closing_candidates and leg.closing_candidates
            ):
                grp.legs_by_currency[cur] = leg
    return [groups[k] for k in order]


def _group_identity(group: _Group) -> dict[str, Optional[str]]:
    iban = account_number = last4 = None
    for acct in group.accounts:
        for ident in acct.identifiers:
            if ident.kind == "iban" and not iban:
                iban = ident.value
            elif ident.kind == "account_number" and not account_number:
                account_number = ident.value
            elif ident.kind == "last4" and not last4:
                last4 = re.sub(r"\D", "", ident.value)[-4:] or None
            elif ident.kind == "masked_pan" and not last4:
                digits = re.sub(r"\D", "", ident.value)
                if len(digits) >= 4:
                    last4 = digits[-4:]
    if not last4:
        for acct in group.accounts:
            for inst in acct.instruments:
                digits = re.sub(r"\D", "", inst.masked_pan or "")
                if len(digits) >= 4:
                    last4 = digits[-4:]
                    break
            if last4:
                break
    return {"iban": iban, "account_number": account_number, "last4": last4}


def _group_instruments(group: _Group) -> list[str]:
    out: list[str] = []
    for acct in group.accounts:
        for inst in acct.instruments:
            if not inst.masked_pan:
                continue
            label = inst.masked_pan
            if inst.holder_name:
                label = f"{inst.masked_pan} ({inst.holder_name})"
            out.append(label)
    return out


def _dec(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _group_verification(group: _Group) -> Optional[StmtVerification]:
    """The first non-None per-product verification judgment in the group (the
    per-product pass attaches it; the inventory-only path leaves it None)."""
    for acct in group.accounts:
        if acct.verification is not None:
            return acct.verification
    return None


def _verification_flag(
    verification: StmtVerification, reconcile_value: Decimal
) -> Optional[str]:
    """Cross-check the role-tagged loan anchor against the focused verification
    judgment. Returns a loan review reason or None. (Numbers only decide here;
    the LLM merely judged.)"""
    vca = verification.current_balance_amount
    if vca is not None:
        vca_d = Decimal(str(vca)).copy_abs().quantize(_CENT)
        diff = (reconcile_value - vca_d).copy_abs()
        tol = max(_TOL, (vca_d * Decimal("0.02")).quantize(_CENT))
        if diff > tol:
            # The role tags and the focused read disagree on the current balance.
            return "loan_verification_disagreement"
    if verification.is_original_amount or verification.includes_future_interest:
        # The model itself says the available current balance looks like the
        # original/disbursed amount or bakes in future interest.
        return "loan_role_suspect"
    return None


def _effective_account_type(group: _Group) -> str:
    """The LLM-tagged account_type, unless the per-product verification
    confidently re-types the product as a loan/line_of_credit. Only overrides
    TOWARD a loan (the dangerous mistag direction — an asset policy would anchor
    the wrong ledger, e.g. a loan mis-typed `investment`), never away from one
    (which could hide a real loan). No-op until the per-product pass runs."""
    tagged = group.account_type
    if tagged in ("loan", "line_of_credit"):
        return tagged
    verification = _group_verification(group)
    if verification is not None and verification.account_type_confirmed in (
        "loan",
        "line_of_credit",
    ):
        return verification.account_type_confirmed  # type: ignore[return-value]
    return tagged


# ── VALIDATE (conservation) ───────────────────────────────────────────────────


def check_conservation(
    leg: StmtCurrencyLeg, policy: AccountPolicy, target: Optional[Decimal]
) -> tuple[Optional[Decimal], Optional[bool], Optional[Decimal]]:
    """Validate the policy `target` magnitude against `opening + Σ signed
    non-contingent flows`. Returns (expected_magnitude, ok, delta):

    - ok is None  → cannot verify (no opening or no flows, or no target role).
    - ok is True  → the role candidate matches the flow reconstruction.
    - ok is False → mismatch → the leg needs manual review.

    Asset orientation throughout (inflow = +). A LIABILITY's opening magnitude is
    negative (you owe it), so a card payment (inflow) reduces it. We compare
    magnitudes; the storage sign is the writer's job. Contingent flows (e.g.
    current-period interest charged only if you don't pay de contado) are excluded
    — that is what makes `payoff` come out right (never derive it arithmetically).
    """
    if leg.opening_balance is None or not leg.flows:
        return None, None, None

    sign_factor = Decimal("-1") if policy.sign == "liability" else Decimal("1")
    opening_signed = sign_factor * Decimal(str(leg.opening_balance))
    flow_sum = Decimal("0")
    for flow in leg.flows:
        if flow.contingent:
            continue
        amt = Decimal(str(flow.amount))
        flow_sum += amt if flow.direction == "inflow" else -amt
    expected = (opening_signed + flow_sum).quantize(_CENT)
    expected_mag = expected.copy_abs()

    if target is None:
        return expected_mag, None, None
    delta = (target - expected_mag).copy_abs()
    tol = max(_TOL, (expected_mag * _REL).quantize(_CENT))
    return expected_mag, (delta <= tol), delta


# ── RESOLVE (ledger) ──────────────────────────────────────────────────────────


def _resolve(
    group: _Group,
    leg: StmtCurrencyLeg,
    policy: AccountPolicy,
    accounts: list[Any],
    debts: list[Any],
    identity: dict[str, Optional[str]],
) -> ResolvedTarget:
    """Resolve a leg to a ledger account/debt by identifier priority
    (IBAN → account_number → last4) then issuer+product fuzzy, within the
    policy-filtered + currency-filtered candidate set. Unresolved → the user
    picks (candidate_ids surfaced)."""
    kind = policy.reconcile_kind
    if policy.ledger_entity == "debt":
        pool = list(debts)
    else:
        want_credit = kind == "credit"
        pool = [a for a in accounts if _is_credit(a) == want_credit]
    cur = (leg.currency or "CRC").upper()
    cands = [c for c in pool if (getattr(c, "currency", None) or "CRC").upper() == cur]
    ids = [c.id for c in cands]

    def _matched(target: Any, conf: float) -> ResolvedTarget:
        return ResolvedTarget(
            kind=kind,
            target_id=target.id,
            target_name=getattr(target, "name", None),
            confidence=conf,
            candidate_ids=ids,
        )

    # Identity priority — a unique exact hit wins outright; ambiguous ties fall
    # through to the next signal.
    for attr in ("iban", "account_number", "last4"):
        val = identity.get(attr)
        if not val:
            continue
        norm = _norm_id(val)
        hits = [
            c
            for c in cands
            if getattr(c, attr, None) and _norm_id(getattr(c, attr)) == norm
        ]
        if len(hits) == 1:
            return _matched(hits[0], 1.0)

    if len(cands) == 1:
        return _matched(cands[0], 0.99)

    hint = " ".join(p for p in (group.issuer, group.label) if p).strip()
    result = match_account_hint(hint or None, cands)  # type: ignore[arg-type]
    if result.status == "matched" and result.account is not None:
        return _matched(result.account, result.score or 0.9)

    return ResolvedTarget(kind=kind, target_id=None, candidate_ids=ids)


# ── build_reconcile_plan ──────────────────────────────────────────────────────


def build_reconcile_plan(
    extraction: StatementExtractionV2,
    *,
    accounts: list[Any],
    debts: list[Any],
) -> ReconcilePlan:
    legs_out: list[LegPlan] = []
    debts_by_id = {getattr(d, "id", None): d for d in debts}
    for group in _group_accounts(extraction.accounts, extraction.bank):
        eff_type = _effective_account_type(group)
        policy = policy_for(eff_type)
        identity = _group_identity(group)
        instruments = _group_instruments(group)

        # An account the LLM tagged but left with no currency leg must still be
        # surfaced for review — never silently dropped.
        if not group.legs_by_currency:
            legs_out.append(
                LegPlan(
                    account_type=eff_type,  # type: ignore[arg-type]
                    currency="CRC",
                    label=group.label,
                    last4=identity.get("last4"),
                    iban=identity.get("iban"),
                    account_number=identity.get("account_number"),
                    sign=policy.sign if policy else "asset",
                    needs_review=True,
                    review_reason="no_balance",
                    attributed_instruments=instruments,
                    resolution=ResolvedTarget(
                        kind=policy.reconcile_kind if policy else "deposit"
                    ),
                )
            )
            continue

        for currency, leg in group.legs_by_currency.items():
            if policy is None:
                legs_out.append(
                    LegPlan(
                        account_type=eff_type,  # type: ignore[arg-type]
                        currency=currency,
                        label=group.label,
                        sign="asset",
                        needs_review=True,
                        review_reason="unknown_account_type",
                        attributed_instruments=instruments,
                        resolution=ResolvedTarget(kind="deposit"),
                    )
                )
                continue

            target_mag, matched_role, ambiguous = select_target(leg, policy)
            expected, ok, delta = check_conservation(leg, policy, target_mag)

            needs_review = False
            review_reason: Optional[str] = None
            reconcile_value: Optional[Decimal] = None
            if target_mag is None:
                needs_review = True
                review_reason = "no_target_role"
            else:
                reconcile_value = target_mag
                if ambiguous and ok is not True:
                    # The target role was printed twice with DIFFERENT amounts and
                    # conservation can't disambiguate — we genuinely don't know
                    # which number to anchor. Block for manual review.
                    needs_review = True
                    review_reason = "ambiguous_role"
                elif ok is False:
                    # Conservation failed, but we DID resolve the policy's single
                    # authoritative printed balance (payoff / closing /
                    # principal_outstanding). That printed figure is the bank's own
                    # number; flow-level reconciliation is fragile on dense
                    # dual-currency / many-line statements (the LLM must tag 30+
                    # interleaved movements perfectly to hit it within 5 céntimos),
                    # so a mismatch here is far more often noisy flows than a wrong
                    # field. Trust the printed balance: AUTO-INCLUDE it
                    # (needs_review stays False) with a SOFT "no verificado"
                    # caution instead of a hard "revisar" block — the reconcile is
                    # always user-confirmed, so a human still sees + approves the
                    # number, and conservation_ok=False carries the caution to the
                    # UI. Ambiguity (above) and no_target_role still block.
                    # Operator decision 2026-06-27,
                    # [[Decision - Statement Conservation - Trust Printed Balance]].
                    review_reason = "unverified_balance"

            resolution = _resolve(group, leg, policy, accounts, debts, identity)

            # ── Loan guardrails (the "massive debt" net) ────────────────────
            # Runs AFTER resolution so we have the matched Debt to compare the
            # anchored balance against. LOANS ONLY; deposits/cards keep the soft
            # "trust printed balance" path. A flag (needs_review) defaults the
            # row OFF and the UI explains why — the user can still confirm.
            prior_balance = orig_amount = expected_outstanding = None
            printed_label: Optional[str] = None
            if (
                policy.reconcile_kind == "loan"
                and reconcile_value is not None
                and resolution.target_id is not None
            ):
                debt = debts_by_id.get(resolution.target_id)
                verification = _group_verification(group)
                if debt is not None:
                    verdict = verdict_for_debt(
                        new_balance=reconcile_value,
                        debt=debt,
                        corte_date=extraction.period_end,
                    )
                    prior_balance = _dec(getattr(debt, "current_balance", None))
                    orig_amount = _dec(getattr(debt, "original_amount", None))
                    expected_outstanding = verdict.expected_outstanding
                    if verdict.flagged:
                        needs_review = True
                        review_reason = verdict.reason
                if verification is not None:
                    if not needs_review:
                        vflag = _verification_flag(verification, reconcile_value)
                        if vflag is not None:
                            needs_review = True
                            review_reason = vflag
                    if verification.printed_label_raw:
                        printed_label = verification.printed_label_raw

            legs_out.append(
                LegPlan(
                    account_type=eff_type,  # type: ignore[arg-type]
                    currency=currency,
                    label=group.label,
                    last4=identity.get("last4"),
                    iban=identity.get("iban"),
                    account_number=identity.get("account_number"),
                    target_role=matched_role if reconcile_value is not None else None,
                    sign=policy.sign,
                    reconcile_value=reconcile_value,
                    opening=(
                        Decimal(str(leg.opening_balance))
                        if leg.opening_balance is not None
                        else None
                    ),
                    conservation_expected=expected,
                    conservation_ok=ok,
                    conservation_delta=delta,
                    needs_review=needs_review,
                    review_reason=review_reason,
                    attributed_instruments=instruments,
                    prior_balance=prior_balance,
                    original_amount=orig_amount,
                    expected_outstanding=expected_outstanding,
                    printed_label=printed_label,
                    resolution=resolution,
                )
            )
    return ReconcilePlan(
        bank=extraction.bank, corte_date=extraction.period_end, legs=legs_out
    )


# ── collapse / project ────────────────────────────────────────────────────────


def is_auto_includable(leg: LegPlan) -> bool:
    """The single include rule: resolved, carries a target balance, not flagged."""
    return (
        not leg.needs_review
        and leg.reconcile_value is not None
        and leg.resolution.target_id is not None
    )


def leg_to_item(leg: LegPlan) -> StatementReconcileItem:
    """Collapse one includable leg → the write item. Identity hints ride along
    ONLY for a confident identity match (confidence >= 1.0), so a by-elimination
    or fuzzy match never self-stamps (and poisons) the resolved account."""
    stampable = leg.resolution.confidence >= 1.0
    return StatementReconcileItem(
        kind=leg.resolution.kind,
        target_id=leg.resolution.target_id,
        closing_balance=leg.reconcile_value,
        currency=leg.currency,
        iban=leg.iban if stampable else None,
        account_number=leg.account_number if stampable else None,
        last4=leg.last4 if stampable else None,
        conservation_ok=leg.conservation_ok,
        needs_review=leg.needs_review,
    )


def auto_includable_items(plan: ReconcilePlan) -> list[StatementReconcileItem]:
    """The single collapse point: a ReconcilePlan → the write items."""
    return [leg_to_item(leg) for leg in plan.legs if is_auto_includable(leg)]


def plan_to_extraction(plan: ReconcilePlan, *, confidence: float) -> StatementExtraction:
    """Back-compat projection for the parse endpoint / chat. One enriched
    `StatementProduct` per leg, derived FROM the plan (never recomputed).
    `closing_balance` stays None for a leg with no resolvable target so a
    review row can't be toggled on to anchor ₡0. `match_confidence` lets the
    native form gate identity self-stamping to a confident, unchanged match."""
    products: list[StatementProduct] = []
    for leg in plan.legs:
        kind = leg.resolution.kind
        products.append(
            StatementProduct(
                label=leg.label,
                account_last4=leg.last4,
                iban=leg.iban,
                account_number=leg.account_number,
                currency=leg.currency,
                kind=kind,
                closing_balance=(
                    float(leg.reconcile_value)
                    if leg.reconcile_value is not None
                    else None
                ),
                suggested_account_id=(
                    leg.resolution.target_id if kind in ("deposit", "credit") else None
                ),
                suggested_debt_id=(
                    leg.resolution.target_id if kind == "loan" else None
                ),
                conservation_ok=leg.conservation_ok,
                needs_review=leg.needs_review,
                review_reason=leg.review_reason,
                target_role=leg.target_role,
                attributed_instruments=leg.attributed_instruments,
                candidate_ids=leg.resolution.candidate_ids,
                match_confidence=leg.resolution.confidence,
                old_balance=(
                    float(leg.prior_balance)
                    if leg.prior_balance is not None
                    else None
                ),
                original_amount=(
                    float(leg.original_amount)
                    if leg.original_amount is not None
                    else None
                ),
                expected_outstanding=(
                    float(leg.expected_outstanding)
                    if leg.expected_outstanding is not None
                    else None
                ),
                printed_label=leg.printed_label,
            )
        )
    return StatementExtraction(
        bank=plan.bank,
        corte_date=plan.corte_date,
        products=products,
        confidence=confidence,
    )
