from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import admin_menu_kb
from bot.keyboards.user import back_kb
from bot.utils.formatting import format_price, status_label
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


@router.callback_query(F.data == "adm:orders")
async def cb_admin_orders(callback: CallbackQuery, session: AsyncSession) -> None:
    orders = await repo.get_recent_orders(session, limit=15)
    if not orders:
        await render(callback, "🧾 Заказов пока нет.", back_kb("adm:menu"))
        await callback.answer()
        return

    lines = ["🧾 <b>Последние заказы</b>\n"]
    for o in orders:
        uname = o.user.username or o.user.full_name or o.user.telegram_id
        lines.append(
            f"#{o.id} — {o.product_name} — {format_price(o.price)} — "
            f"{status_label(o.status)} — @{uname}"
        )
    await render(callback, "\n".join(lines), back_kb("adm:menu"))
    await callback.answer()
