#!/usr/bin/env python3
"""
One-time script to create the single MVP user and print the UUID
to add to .env as DEFAULT_USER_ID.

Usage:
    python scripts/create_user.py --name "Daniel"
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import settings
from api.models.user import User


async def create_user(name: str) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_factory() as session:
        user = User(name=name)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    await engine.dispose()

    print(f"\nUsuario creado exitosamente:")
    print(f"  Nombre : {user.name}")
    print(f"  ID     : {user.id}")
    print(f"\nAgrega esto a tu .env:")
    print(f"  DEFAULT_USER_ID={user.id}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crear usuario inicial del sistema.")
    parser.add_argument("--name", required=True, help="Tu nombre")
    args = parser.parse_args()
    asyncio.run(create_user(args.name))


if __name__ == "__main__":
    main()
