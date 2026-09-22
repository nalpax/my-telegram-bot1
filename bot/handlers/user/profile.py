from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.keyboards.user import back_kb
from bot.utils.ui import render

router = Router(name="user_profile")


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery, session: AsyncSession) -> None:
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    orders = await repo.get_user_orders(session, db_user.id, limit=1000) if db_user else []
    completed = [o for o in orders if o.status == "completed"]

    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"👤 Имя: {callback.from_user.full_name}\n"
        f"🧾 Всего заказов: {len(orders)}\n"
        f"✅ Успешных покупок: {len(completed)}\n"
    )
    await render(callback, text, back_kb())
    await callback.answer()
