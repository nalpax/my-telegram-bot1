"""
Middlewares shared by every router.

DbSessionMiddleware: opens one AsyncSession per update and injects it into
the handler as `session=...`, closing it afterwards no matter what happens.

UserRegisterMiddleware: silently upserts the User row for whoever sent the
update, so handlers can assume `data["db_user"]` exists.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from bot.database.engine import async_session_factory
from bot.database.repo import get_or_create_user

logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class UserRegisterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        session = data.get("session")
        if user is not None and session is not None and not user.is_bot:
            try:
                db_user = await get_or_create_user(
                    session, telegram_id=user.id, username=user.username, full_name=user.full_name
                )
                data["db_user"] = db_user
            except Exception:  # noqa: BLE001
                logger.exception("Failed to upsert user %s", user.id)
        return await handler(event, data)


class ErrorLoggingMiddleware(BaseMiddleware):
    """Catches any exception that escapes a handler so the bot never crashes."""

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled error while processing update: %s", event)
            return None
