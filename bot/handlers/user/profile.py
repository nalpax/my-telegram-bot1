from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.keyboards.user import back_kb
from bot.utils.formatting import esc, format_dt, format_price
from bot.utils.ui import render

router = Router(name="user_profile")


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery, session: AsyncSession) -> None:
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    if db_user is None:
        await callback.answer("Нажмите /start", show_alert=True)
        return
    total, completed, spent = await repo.user_order_stats(session, db_user.id)
    favorites = await repo.list_favorite_products(session, db_user.id)

    text = (
        f"👤 <b>Профиль</b>\n"
        f"─────────────────\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"👤 Имя: {esc(callback.from_user.full_name)}\n"
        f"📅 С нами с: {format_dt(db_user.created_at)}\n"
        f"─────────────────\n"
        f"✅ Покупок: {completed}\n"
        f"💰 Всего потрачено: {format_price(spent)}\n"
        f"⭐ В избранном: {len(favorites)}\n"
    )
    await render(callback, text, back_kb())
    await callback.answer()
