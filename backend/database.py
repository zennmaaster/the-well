import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./well.db")

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    from backend.models import Frame, Commit, Prior, Translation, CheckIn, Thread, Message, Practice  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrate: add agent_name columns if missing (SQLite doesn't support IF NOT EXISTS for columns)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn):
    """Add columns that were added after initial schema creation."""
    import sqlite3
    raw = conn.connection.dbapi_connection
    if not isinstance(raw, sqlite3.Connection):
        # aiosqlite wraps the real connection
        raw = getattr(raw, '_conn', raw)
    cursor = raw.cursor()
    for table, column in [("commits", "agent_name"), ("checkins", "agent_name")]:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR(200)")
        except Exception:
            pass  # column already exists


async def get_db():
    async with SessionLocal() as session:
        yield session
