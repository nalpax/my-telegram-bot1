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
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

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
    if member.status == "restricted":
        return bool(getattr(member, "is_member", False))
    return member.status not in ("left", "kicked")


def _unwrap(event: TelegramObject) -> TelegramObject | None:
    """Update-level middlewares receive the Update wrapper — get the actual event."""
    if isinstance(event, Update):
        return event.event
    return event


class SubscriptionGateMiddleware(BaseMiddleware):
    """
    Registered at the Update level, so it sees every update. Only private
    messages and callback queries are gated; payments, chat-member updates
    etc. always pass (a payment must never be blocked half-way).

    Also silently drops everything from banned users.
    """

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
        inner = _unwrap(event)

        if user is None or session is None or bot is None:
            return await handler(event, data)
        if not isinstance(inner, (Message, CallbackQuery)):
            return await handler(event, data)
        if isinstance(inner, Message) and (
            inner.successful_payment is not None or inner.chat.type != "private"
        ):
            return await handler(event, data)

        settings = get_settings()
        if user.id in settings.admin_id_list:
            return await handler(event, data)

        db_user = data.get("db_user")
        if db_user is not None and db_user.is_banned:
            if isinstance(inner, CallbackQuery):
                await _safe_answer(inner, "⛔️ Доступ к магазину ограничен.", show_alert=True)
            return None

        if isinstance(inner, CallbackQuery) and inner.data in self._EXEMPT_CALLBACK_DATA:
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
        if isinstance(inner, CallbackQuery):
            await _safe_answer(inner)
            chat_id = inner.message.chat.id if inner.message else user.id
        else:
            chat_id = inner.chat.id
        try:
            await bot.send_message(chat_id, text, reply_markup=subscription_gate_kb(channel_url))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send subscription gate message")
        return None


async def _safe_answer(callback: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception:  # noqa: BLE001 - query may be too old to answer
        pass


class ErrorLoggingMiddleware(BaseMiddleware):
    """
    Catches any exception that escapes a handler so the bot never crashes,
    and tells the user something went wrong instead of leaving a button
    spinning forever.
    """

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled error while processing update %s", getattr(event, "update_id", "?"))
            inner = _unwrap(event)
            try:
                if isinstance(inner, CallbackQuery):
                    await _safe_answer(inner, "⚠️ Что-то пошло не так. Попробуйте ещё раз.", show_alert=True)
                elif isinstance(inner, Message) and inner.chat.type == "private":
                    await inner.answer("⚠️ Что-то пошло не так. Попробуйте ещё раз или нажмите /start.")
            except Exception:  # noqa: BLE001
                pass
            return None
