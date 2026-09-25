from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.handlers.user.start import welcome_text
from bot.keyboards.user import main_menu_kb
from bot.middlewares import check_subscription
from bot.utils.ui import render

logger = logging.getLogger(__name__)
router = Router(name="subscription")


@router.callback_query(F.data == "subcheck:verify")
async def cb_check_subscription(callback: CallbackQuery, session: AsyncSession) -> None:
    subscribed = await check_subscription(callback.bot, session, callback.from_user.id)
    if subscribed is False:
        await callback.answer(
            "❌ Вы ещё не подписались на канал. Подпишитесь и нажмите проверку ещё раз.", show_alert=True
        )
        return

    # subscribed is True or None (gate disabled/misconfigured) — let them in either way
    await callback.answer("✅ Подписка подтверждена!")
    is_admin = callback.from_user.id in get_settings().admin_id_list
    await render(callback, await welcome_text(session), main_menu_kb(is_admin))
