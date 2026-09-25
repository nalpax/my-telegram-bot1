from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.database.models import DigitalItem
from bot.keyboards.user import back_kb, orders_list_kb
from bot.utils.formatting import esc, format_dt, format_price, status_label
from bot.utils.ui import render

router = Router(name="user_orders")


@router.callback_query(F.data.regexp(r"^menu:orders(:\d+)?$"))
async def cb_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    orders = await repo.get_user_orders(session, db_user.id, limit=200) if db_user else []
    if not orders:
        await render(
            callback,
            "🧾 <b>Мои заказы</b>\n─────────────────\nПока пусто — самое время заглянуть в каталог 🛍",
            back_kb(),
        )
        await callback.answer()
        return
    await render(
        callback,
        f"🧾 <b>Мои заказы</b>\n─────────────────\nВсего: {len(orders)}\nВыберите заказ, чтобы увидеть товар 👇",
        orders_list_kb(orders, page),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^order_u:\d+(:\d+)?$"))
async def cb_order_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    order_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    order = await repo.get_order(session, order_id)
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    if order is None or db_user is None or order.user_id != db_user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    text = (
        f"🧾 <b>Заказ #{order.id}</b>\n"
        f"─────────────────\n"
        f"📦 Товар: {esc(order.product_name)}\n"
        f"💰 Сумма: {format_price(order.price)}\n"
        f"📊 Статус: {status_label(order.status)}\n"
        f"📅 Дата: {format_dt(order.paid_at or order.created_at)}\n"
    )
    content = order.delivered_content
    if content is None and order.digital_item_id:
        item = await session.get(DigitalItem, order.digital_item_id)
        content = item.content if item else None
    if content:
        text += f"─────────────────\n🔑 Ваш товар:\n<code>{esc(content)}</code>"
    elif order.status == "failed":
        text += "─────────────────\n⚠️ Заказ не выполнен. Если оплата прошла — обратитесь в поддержку."
    await render(callback, text, back_kb(f"menu:orders:{page}", "◀️ К заказам"))
    await callback.answer()
