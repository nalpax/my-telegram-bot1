from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event, inspect, text
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
    # every connection. WAL + busy_timeout keep concurrent handlers from
    # failing with "database is locked".
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


# Columns added after the first release. create_all() never alters existing
# tables, so these are added by hand on startup — keeps old databases working.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("orders", "delivered_content", "TEXT"),
    ("orders", "charge_id", "VARCHAR(255)"),
]


def _apply_light_migrations(sync_conn) -> None:  # noqa: ANN001
    inspector = inspect(sync_conn)
    for table, column, ddl in _ADDED_COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("Migrated: added %s.%s", table, column)
    # Backfill the delivered-content snapshot for orders made before it existed.
    sync_conn.execute(
        text(
            "UPDATE orders SET delivered_content = "
            "(SELECT content FROM digital_items WHERE digital_items.id = orders.digital_item_id) "
            "WHERE delivered_content IS NULL AND digital_item_id IS NOT NULL"
        )
    )


async def init_models() -> None:
    """Create tables that don't exist yet and add new columns. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_light_migrations)
    logger.info("Database tables ensured")
