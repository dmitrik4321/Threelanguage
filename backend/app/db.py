from __future__ import annotations
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://tl:tl@localhost/tl")
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with SessionLocal() as session:
        yield session

class init_models:
    """create_all для dev; в проде — Alembic."""
    async def __aenter__(self):
        async with engine.begin() as conn:
            from . import models  # noqa: F401 — регистр таблиц
            await conn.run_sync(Base.metadata.create_all)
    async def __aexit__(self, *a):
        return False
