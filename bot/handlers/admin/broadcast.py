from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import broadcast_confirm_kb
from bot.keyboards.user import cancel_kb
from bot.states.states import BroadcastStates
from bot.utils.ui import render

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "adm:broadcast:start")
async def cb_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.waiting_message)
    await render(callback, "📢 Отправьте сообщение для рассылки (текст):", cancel_kb())
    await callback.answer()


@router.message(BroadcastStates.waiting_message)
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, отправьте текстовое сообщение.", reply_markup=cancel_kb())
        return
    await state.update_data(broadcast_text=message.text)
    await state.set_state(BroadcastStates.waiting_confirm)
    await message.answer(
        f"📢 <b>Предпросмотр рассылки:</b>\n\n{message.text}\n\nОтправить всем пользователям?",
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(F.data == "adm:broadcast:cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, "❌ Рассылка отменена.", None)
    await callback.answer()


@router.callback_query(F.data == "adm:broadcast:confirm")
async def cb_broadcast_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    text = data.get("broadcast_text")
    await state.clear()
    if not text:
        await callback.answer("Сообщение потеряно, попробуйте снова.", show_alert=True)
        return

    await callback.answer()
    try:
        if callback.message.text:
            await callback.message.edit_text("📢 Рассылка запущена…")
    except Exception:  # noqa: BLE001
        pass

    settings = get_settings()
    telegram_ids = await repo.get_all_user_telegram_ids(session)

    sent = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await callback.bot.send_message(telegram_id, text)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await callback.bot.send_message(telegram_id, text)
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except TelegramForbiddenError:
            # user blocked the bot — not an error worth logging loudly
            failed += 1
        except Exception:  # noqa: BLE001
            logger.exception("Broadcast failed for user %s", telegram_id)
            failed += 1
        await asyncio.sleep(settings.broadcast_delay_seconds)

    await callback.bot.send_message(
        callback.from_user.id, f"✅ Рассылка завершена.\n\nОтправлено: {sent}\nНе удалось: {failed}"
    )
