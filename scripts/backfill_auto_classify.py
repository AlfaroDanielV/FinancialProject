"""Opt-in backfill: auto-classify historical unclassified transactions.

Runs the same deterministic classifier used at capture
(`api/services/classification.py`) over existing CONFIRMED, non-archived
rows that are missing a category FK and/or (for expenses) an envelope, in
chronological order — so an early row classified by the user seeds the later
ones, exactly like live capture. Machine-managed rows (transfer legs, goal
flows, ajustes, loan cargos) are skipped by the classifier itself.

Idempotent: applied rows carry the auto flags and already-classified rows are
never touched, so re-running is a no-op.

Usage:
    DRY RUN (default):  python -m scripts.backfill_auto_classify
    APPLY:              python -m scripts.backfill_auto_classify --apply
    One user only:      python -m scripts.backfill_auto_classify --user <uuid>
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Optional

from sqlalchemy import or_, select

from api.database import AsyncSessionLocal
from api.models.transaction import Transaction
from api.services.classification import (
    apply_classification,
    is_classifiable,
    suggest_classification,
)


async def main(apply: bool, user_id: Optional[uuid.UUID]) -> None:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Transaction)
            .where(
                Transaction.status == "confirmed",
                Transaction.archived.is_(False),
                or_(
                    Transaction.category_id.is_(None),
                    (Transaction.amount < 0) & Transaction.envelope_id.is_(None),
                ),
            )
            .order_by(
                Transaction.transaction_date.asc(), Transaction.created_at.asc()
            )
        )
        if user_id is not None:
            stmt = stmt.where(Transaction.user_id == user_id)

        rows = (await db.execute(stmt)).scalars().all()
        if not rows:
            print("Nothing to classify.")
            return

        changed = 0
        for txn in rows:
            if not is_classifiable(txn):
                continue
            if apply:
                applied = await apply_classification(
                    db, user_id=txn.user_id, txn=txn
                )
            else:
                applied = await suggest_classification(
                    db,
                    user_id=txn.user_id,
                    merchant=txn.merchant,
                    amount=txn.amount,
                    category_hint=txn.category,
                    exclude_id=txn.id,
                )
            # In apply mode the service returns exactly what it applied; in
            # dry-run, suggest() may return a suggestion for a field the row
            # already has — count only what would actually be filled.
            got_category = applied.category_id is not None and (
                apply or txn.category_id is None
            )
            got_envelope = applied.envelope_id is not None and (
                apply or (txn.envelope_id is None and float(txn.amount) < 0)
            )
            if not got_category and not got_envelope:
                continue
            changed += 1
            parts = []
            if got_category:
                parts.append(f"categoría → {applied.category_name!r}")
            if got_envelope:
                parts.append(f"sobre → {applied.envelope_name!r}")
            print(
                f"  {txn.transaction_date} {txn.merchant!r} "
                f"({txn.amount} {txn.currency}): " + ", ".join(parts)
                + ("" if apply else "   [dry-run]")
            )

        if apply:
            await db.commit()
            print(f"Applied to {changed} row(s).")
        else:
            print(f"\n{changed} row(s) would change. Re-run with --apply to commit.")


if __name__ == "__main__":
    uid: Optional[uuid.UUID] = None
    if "--user" in sys.argv:
        uid = uuid.UUID(sys.argv[sys.argv.index("--user") + 1])
    asyncio.run(main(apply="--apply" in sys.argv, user_id=uid))
