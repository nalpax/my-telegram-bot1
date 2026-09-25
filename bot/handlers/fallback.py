"""
Catch-all handlers — registered last, so they only see updates nobody else
handled: stray text, stickers, stale buttons from old messages, etc.
Without this the bot would just stay silent, which looks broken.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.handlers.user.start import welcome_text
from bot.keyboards.user import main_menu_kb

router = Router(name="fallback")


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query()
async def cb_unknown(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Откройте меню заново: /start", show_alert=True)


@router.message(F.chat.type == "private")
async def msg_unknown(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    is_admin = message.from_user is not None and message.from_user.id in get_settings().admin_id_list
    await message.answer(
        "🤔 Не понял команду. Воспользуйтесь меню 👇\n\n" + await welcome_text(session),
        reply_markup=main_menu_kb(is_admin),
    )
