from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal as _DefaultSessionFactory


class QuerySessionFactory(Protocol):
    def __call__(self) -> Any: ...


_session_factory: QuerySessionFactory = _DefaultSessionFactory


class _SessionFactoryProxy:
    """Callable proxy used by query dispatcher/tools.

    Modules import this as `AsyncSessionLocal` and keep using the familiar
    `async with AsyncSessionLocal() as db` shape. Tests can either monkeypatch
    the module-local name as before, or set a per-test factory globally with
    `set_query_session_factory`.
    """

    def __call__(self) -> AsyncSession:
        return _session_factory()


AsyncSessionLocal = _SessionFactoryProxy()


def set_query_session_factory(factory: QuerySessionFactory | None) -> None:
    """Override the session factory used by query dispatcher/tools.

    Passing None restores the application default. This is primarily for
    tests: each pytest event loop gets a NullPool engine, avoiding stale
    asyncpg connections bound to a closed loop.
    """

    global _session_factory
    _session_factory = factory or _DefaultSessionFactory


def get_query_session_factory() -> QuerySessionFactory:
    return _session_factory
