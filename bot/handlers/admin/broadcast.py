from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import broadcast_confirm_kb
from bot.keyboards.user import back_kb, cancel_kb
from bot.states.states import BroadcastStates
from bot.utils.ui import render

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "adm:broadcast:start")
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.waiting_message)
    await render(
        callback,
        "📢 <b>Рассылка</b>\n\nОтправьте сообщение для рассылки — текст, фото, видео, "
        "файл или альбомное фото с подписью. Форматирование сохранится.",
        cancel_kb(),
    )
    await callback.answer()


@router.message(BroadcastStates.waiting_message)
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    if message.media_group_id:
        await message.answer("Альбомы не поддерживаются — отправьте одно сообщение.", reply_markup=cancel_kb())
        return
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    await state.set_state(BroadcastStates.waiting_confirm)
    await message.answer("👆 <b>Так будет выглядеть рассылка.</b>\n\nОтправить всем пользователям?",
                         reply_markup=broadcast_confirm_kb())


@router.callback_query(F.data == "adm:broadcast:cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, "❌ Рассылка отменена.", back_kb("adm:menu", "◀️ В админ-панель"))
    await callback.answer()


async def _copy(bot: Bot, chat_id: int, from_chat_id: int, message_id: int) -> None:
    try:
        await bot.copy_message(chat_id, from_chat_id, message_id)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        await bot.copy_message(chat_id, from_chat_id, message_id)


@router.callback_query(F.data == "adm:broadcast:confirm")
async def cb_broadcast_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    from_chat_id = data.get("source_chat_id")
    message_id = data.get("source_message_id")
    await state.clear()
    if not from_chat_id or not message_id:
        await callback.answer("Сообщение потеряно, попробуйте снова.", show_alert=True)
        return

    await callback.answer()
    await render(callback, "📢 Рассылка запущена… Отчёт придёт по завершении.")

    settings = get_settings()
    telegram_ids = await repo.get_all_user_telegram_ids(session)

    sent = blocked = failed = 0
    for telegram_id in telegram_ids:
        try:
            await _copy(callback.bot, telegram_id, from_chat_id, message_id)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1  # user blocked the bot — expected, not worth logging loudly
        except Exception:  # noqa: BLE001
            logger.warning("Broadcast failed for user %s", telegram_id, exc_info=True)
            failed += 1
        await asyncio.sleep(settings.broadcast_delay_seconds)

    await callback.bot.send_message(
        callback.from_user.id,
        f"✅ <b>Рассылка завершена</b>\n\n📨 Доставлено: {sent}\n🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибки: {failed}",
        reply_markup=back_kb("adm:menu", "◀️ В админ-панель"),
    )
