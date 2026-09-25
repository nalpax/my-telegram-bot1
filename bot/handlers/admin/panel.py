from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.database.models import OrderStatus
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import ADMIN_ORDERS_PAGE, admin_menu_kb, admin_order_kb, admin_orders_kb
from bot.keyboards.user import back_kb
from bot.utils.formatting import esc, format_dt, format_price, status_label, user_mention
from bot.utils.ui import render

router = Router(name="admin_panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

ADMIN_TEXT = "🛠 <b>Админ-панель</b>\n\nВыберите раздел:"


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(ADMIN_TEXT, reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, ADMIN_TEXT, admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:orders(:\d+)?$"))
async def cb_admin_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    # Fetch one extra row to know whether a "next page" exists.
    orders = await repo.get_recent_orders(session, limit=ADMIN_ORDERS_PAGE + 1, offset=page * ADMIN_ORDERS_PAGE)
    has_next = len(orders) > ADMIN_ORDERS_PAGE
    orders = orders[:ADMIN_ORDERS_PAGE]
    if not orders and page == 0:
        await render(callback, "🧾 Оплаченных заказов пока нет.", back_kb("adm:menu", "◀️ Назад"))
        await callback.answer()
        return
    await render(
        callback,
        f"🧾 <b>Заказы</b> (стр. {page + 1})\n\nНеоплаченные счета не показываются.",
        admin_orders_kb(orders, page, has_next),
    )
    await callback.answer()


async def _show_order(callback: CallbackQuery, session: AsyncSession, order_id: int, page: int) -> None:
    order = await repo.get_order_with_user(session, order_id)
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    u = order.user
    text = (
        f"🧾 <b>Заказ #{order.id}</b>\n"
        f"─────────────────\n"
        f"📦 Товар: {esc(order.product_name)}\n"
        f"💰 Сумма: {format_price(order.price)}"
        + (f" (скидка {format_price(order.discount_amount)})" if order.discount_amount else "")
        + f"\n📊 Статус: {status_label(order.status)}\n"
        f"👤 Покупатель: {user_mention(u.telegram_id, u.username, u.full_name)} (<code>{u.telegram_id}</code>)\n"
        f"📅 Создан: {format_dt(order.created_at)}\n"
        f"💳 Оплачен: {format_dt(order.paid_at)}\n"
    )
    if order.charge_id:
        text += f"🔖 Платёж: <code>{esc(order.charge_id)}</code>\n"
    if order.delivered_content:
        text += f"─────────────────\n🔑 Выдано:\n<code>{esc(order.delivered_content)}</code>"
    await render(callback, text, admin_order_kb(order, page))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:order:\d+:\d+$"))
async def cb_admin_order(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, order_id, page = callback.data.split(":")
    await _show_order(callback, session, int(order_id), int(page))


@router.callback_query(F.data.regexp(r"^adm:order:refunded:\d+:\d+$"))
async def cb_admin_order_refunded(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, _, order_id, page = callback.data.split(":")
    order = await repo.get_order(session, int(order_id))
    if order is not None and order.status == OrderStatus.failed:
        await repo.set_order_status(session, order.id, OrderStatus.refunded)
    await _show_order(callback, session, int(order_id), int(page))
