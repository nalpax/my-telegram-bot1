from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.user import back_kb
from bot.utils.formatting import format_price
from bot.utils.ui import render

router = Router(name="admin_stats")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    users = await repo.count_users(session)
    products = await repo.count_products(session)
    categories = await repo.count_categories(session)
    orders = await repo.count_orders(session)
    completed = await repo.count_completed_orders(session)
    sales = await repo.sum_sales(session)

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"📦 Товаров: {products}\n"
        f"📁 Категорий: {categories}\n"
        f"🧾 Заказов: {orders}\n"
        f"✅ Успешных заказов: {completed}\n"
        f"💰 Продаж: {format_price(sales)}\n"
    )
    await render(callback, text, back_kb("adm:menu"))
    await callback.answer()
