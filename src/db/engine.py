"""Async SQLAlchemy engine + session factory.

`expire_on_commit=False` keeps loaded objects usable after commit, which
simplifies REST/SSE handlers that return data they just mutated.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.settings import Settings, get_settings


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine_for_settings(settings: Settings) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        # Lazy init for scripts (alembic env, tests not using lifespan).
        return create_engine_for_settings(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker
