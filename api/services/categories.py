from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user_category import UserCategory


DEFAULT_CATEGORY_META: tuple[dict[str, str], ...] = (
    {
        "name": "alimentación",
        "kind": "expense",
        "color": "#2f855a",
        "icon": "shopping-cart",
    },
    {"name": "transporte", "kind": "expense", "color": "#2b6cb0", "icon": "car"},
    {"name": "servicios", "kind": "expense", "color": "#805ad5", "icon": "plug"},
    {
        "name": "salud",
        "kind": "expense",
        "color": "#c53030",
        "icon": "heart-pulse",
    },
    {"name": "ocio", "kind": "expense", "color": "#dd6b20", "icon": "ticket"},
    {"name": "vivienda", "kind": "expense", "color": "#4a5568", "icon": "home"},
    {
        "name": "ingreso_salario",
        "kind": "income",
        "color": "#047857",
        "icon": "briefcase",
    },
    {
        "name": "ingreso_extra",
        "kind": "income",
        "color": "#0891b2",
        "icon": "wallet",
    },
    {"name": "deudas", "kind": "expense", "color": "#b7791f", "icon": "receipt"},
    {"name": "otros", "kind": "both", "color": "#718096", "icon": "circle-ellipsis"},
)


def normalize_category_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


async def ensure_default_categories(
    db: AsyncSession, user_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(func.lower(UserCategory.name)).where(
            UserCategory.user_id == user_id
        )
    )
    existing = {row[0] for row in result.fetchall()}
    created = False
    for item in DEFAULT_CATEGORY_META:
        if normalize_category_name(item["name"]) in existing:
            continue
        db.add(
            UserCategory(
                user_id=user_id,
                name=item["name"],
                kind=item["kind"],
                color=item["color"],
                icon=item["icon"],
                is_default=True,
            )
        )
        created = True
    if created:
        await db.flush()
    return created


async def has_active_category_named(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    stmt = select(UserCategory.id).where(
        UserCategory.user_id == user_id,
        UserCategory.archived.is_(False),
        func.lower(UserCategory.name) == normalize_category_name(name),
    )
    if exclude_id is not None:
        stmt = stmt.where(UserCategory.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None
