from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.keyboards.user import main_menu_kb
from bot.middlewares import check_subscription

logger = logging.getLogger(__name__)
router = Router(name="subscription")

WELCOME_TEXT = (
    "🛍 <b>Добро пожаловать в магазин!</b>\n"
    "─────────────────\n"
    "Выберите раздел в меню ниже 👇"
)


@router.callback_query(F.data == "subcheck:verify")
async def cb_check_subscription(callback: CallbackQuery, session: AsyncSession) -> None:
    subscribed = await check_subscription(callback.bot, session, callback.from_user.id)
    if subscribed is False:
        await callback.answer("❌ Вы ещё не подписались на канал. Подпишитесь и нажмите проверку ещё раз.", show_alert=True)
        return

    # subscribed is True or None (gate disabled/misconfigured) — let them in either way
    await callback.answer("✅ Подписка подтверждена!")
    settings = get_settings()
    is_admin = callback.from_user.id in settings.admin_id_list
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001
        pass
    await callback.bot.send_message(callback.message.chat.id, WELCOME_TEXT, reply_markup=main_menu_kb(is_admin))
