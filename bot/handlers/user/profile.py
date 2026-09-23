from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.keyboards.user import back_kb
from bot.utils.formatting import format_price
from bot.utils.ui import render

router = Router(name="user_profile")


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery, session: AsyncSession) -> None:
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    orders = await repo.get_user_orders(session, db_user.id, limit=1000) if db_user else []
    completed = [o for o in orders if o.status == "completed"]
    favorites = await repo.list_favorite_products(session, db_user.id) if db_user else []
    total_spent = sum((o.price for o in completed), start=Decimal("0"))

    text = (
        f"👤 <b>Профиль</b>\n"
        f"─────────────────\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"👤 Имя: {callback.from_user.full_name}\n"
        f"─────────────────\n"
        f"🧾 Всего заказов: {len(orders)}\n"
        f"✅ Успешных покупок: {len(completed)}\n"
        f"💰 Всего потрачено: {format_price(total_spent)}\n"
        f"⭐ В избранном: {len(favorites)}\n"
    )
    await render(callback, text, back_kb())
    await callback.answer()
