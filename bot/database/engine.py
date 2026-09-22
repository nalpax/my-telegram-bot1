from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import Base

logger = logging.getLogger(__name__)

settings = get_settings()
_db_url = settings.effective_database_url

if _db_url.startswith("sqlite"):
    # SQLite needs the parent directory to exist before it can create the
    # file — most hosts that give you a persistent DATA_DIR create it for
    # you, but this is cheap and harmless either way.
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    logger.info("Using local SQLite database at %s", _db_url)
else:
    logger.info("Using external database (host hidden from logs)")

engine = create_async_engine(_db_url, echo=False, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

if engine.dialect.name == "sqlite":
    # SQLite does NOT enforce foreign keys by default, which would silently
    # break our ON DELETE CASCADE / SET NULL behavior (e.g. deleting a
    # category should delete its products). This turns enforcement on for
    # every connection.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def init_models() -> None:
    """Create tables that don't exist yet. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")
