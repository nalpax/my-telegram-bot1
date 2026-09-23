"""
Middlewares shared by every router.

DbSessionMiddleware: opens one AsyncSession per update and injects it into
the handler as `session=...`, closing it afterwards no matter what happens.

UserRegisterMiddleware: silently upserts the User row for whoever sent the
update, so handlers can assume `data["db_user"]` exists.

SubscriptionGateMiddleware: if the admin has configured a required channel,
blocks every user-facing message/callback until Telegram itself confirms
(via getChatMember — never just "the button was pressed") that the user is
a member of that channel. Admins are always exempt, so they can never lock
themselves out of their own admin panel.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject, Update

from bot.config import get_settings
from bot.database.engine import async_session_factory
from bot.database.repo import get_or_create_user, get_setting

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


async def check_subscription(bot, session, telegram_id: int) -> bool | None:
    """
    Returns True if subscribed, False if confirmed not subscribed, or None
    if the gate is disabled / misconfigured (treated as "let them through").
    """
    channel_id = await get_setting(session, "required_channel_id")
    if not channel_id:
        return None
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=telegram_id)
    except TelegramBadRequest:
        # Bot isn't (or is no longer) a member/admin of the channel, or the
        # channel id is wrong — fail open rather than lock everyone out.
        logger.warning("Could not check membership for channel %s — check bot is admin there", channel_id)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected error checking channel subscription")
        return None
    return member.status not in ("left", "kicked")


class SubscriptionGateMiddleware(BaseMiddleware):
    _EXEMPT_CALLBACK_DATA = {"subcheck:verify"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        session = data.get("session")
        bot = data.get("bot")

        if user is None or session is None or bot is None:
            return await handler(event, data)

        settings = get_settings()
        if user.id in settings.admin_id_list:
            return await handler(event, data)

        # Never gate payment events — by the time a user reaches checkout
        # they already passed the gate to open the catalog.
        if isinstance(event, PreCheckoutQuery):
            return await handler(event, data)
        if isinstance(event, Message) and event.successful_payment is not None:
            return await handler(event, data)

        cb_data = event.data if isinstance(event, CallbackQuery) else None
        if cb_data in self._EXEMPT_CALLBACK_DATA:
            return await handler(event, data)

        subscribed = await check_subscription(bot, session, user.id)
        if subscribed is not False:
            return await handler(event, data)

        # Not subscribed — show the gate instead of running the handler.
        from bot.keyboards.user import subscription_gate_kb  # local import avoids a cycle

        channel_url = await get_setting(session, "required_channel_url")
        if not channel_url:
            channel_id = await get_setting(session, "required_channel_id") or ""
            channel_url = f"https://t.me/{channel_id.lstrip('@')}"

        text = (
            "🔒 <b>Нужна подписка на канал</b>\n"
            "─────────────────\n"
            "Чтобы пользоваться магазином, подпишитесь на наш канал и нажмите "
            "«Проверить подписку»."
        )
        chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
        if isinstance(event, CallbackQuery):
            await event.answer()
        try:
            await bot.send_message(chat_id, text, reply_markup=subscription_gate_kb(channel_url))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send subscription gate message")
        return None


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
