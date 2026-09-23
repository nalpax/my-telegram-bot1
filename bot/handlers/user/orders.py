from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.database.models import DigitalItem
from bot.keyboards.user import back_kb, orders_list_kb
from bot.utils.formatting import format_price, status_label
from bot.utils.ui import render

router = Router(name="user_orders")


@router.callback_query(F.data == "menu:orders")
async def cb_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    orders = await repo.get_user_orders(session, db_user.id) if db_user else []
    if not orders:
        await render(
            callback,
            "🧾 <b>Мои заказы</b>\n─────────────────\nПока пусто — самое время заглянуть в каталог 🛍",
            back_kb(),
        )
        await callback.answer()
        return
    await render(callback, f"🧾 <b>Мои заказы</b>\n─────────────────\nВсего: {len(orders)}", orders_list_kb(orders))
    await callback.answer()


@router.callback_query(F.data.startswith("order_u:"))
async def cb_order_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await repo.get_order(session, order_id)
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    if order is None or db_user is None or order.user_id != db_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    text = (
        f"🧾 <b>Заказ #{order.id}</b>\n"
        f"─────────────────\n"
        f"📦 Товар: {order.product_name}\n"
        f"💰 Цена: {format_price(order.price)}\n"
        f"📊 Статус: {status_label(order.status)}\n"
    )
    if order.digital_item_id:
        item = await session.get(DigitalItem, order.digital_item_id)
        if item:
            text += f"─────────────────\n🔑 Ваш товар:\n<code>{item.content}</code>"
    await render(callback, text, back_kb("menu:orders"))
    await callback.answer()
