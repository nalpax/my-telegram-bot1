"""
"💰 Цены" — all products on one screen, tap one and type a new price.
The quickest way for an admin to manage prices without digging through
categories.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import admin_prices_kb
from bot.keyboards.user import back_kb
from bot.states.states import PriceListStates
from bot.utils.formatting import esc, format_price, parse_price
from bot.utils.ui import render

router = Router(name="admin_prices")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _price_example() -> str:
    return "50" if get_settings().uses_stars else "199 или 199.90"


async def _prices_text(session: AsyncSession) -> tuple[str, list]:
    products = await repo.list_all_products(session)
    if not products:
        return "💰 <b>Цены</b>\n\nТоваров пока нет. Добавьте их в разделе «📦 Товары».", products
    return (
        "💰 <b>Цены</b>\n─────────────────\n"
        "Нажмите на товар и отправьте новую цену сообщением.",
        products,
    )


@router.callback_query(F.data.regexp(r"^adm:prices(:\d+)?$"))
async def cb_prices(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    text, products = await _prices_text(session)
    await render(callback, text, admin_prices_kb(products, page))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:prices:set:\d+$"))
async def cb_price_set(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    product = await repo.get_product(session, int(callback.data.split(":")[3]))
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await state.set_state(PriceListStates.waiting_price)
    await state.update_data(product_id=product.id)
    await render(
        callback,
        f"💰 <b>{esc(product.name)}</b>\n\nТекущая цена: <b>{format_price(product.price)}</b>\n\n"
        f"Отправьте новую цену (например, {_price_example()}):",
        back_kb("adm:prices", "❌ Отмена"),
    )
    await callback.answer()


@router.message(PriceListStates.waiting_price)
async def process_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    price = parse_price(message.text)
    if price is None:
        await message.answer(
            f"Не понял цену. Отправьте положительное число, например {_price_example()}.",
            reply_markup=back_kb("adm:prices", "❌ Отмена"),
        )
        return
    data = await state.get_data()
    await state.clear()
    product = await repo.get_product(session, data.get("product_id", 0))
    if product is None:
        await message.answer("Товар не найден.")
        return
    await repo.update_product_field(session, product.id, price=price)
    await session.refresh(product)
    text, products = await _prices_text(session)
    await message.answer(
        f"✅ Цена «{esc(product.name)}»: <b>{format_price(price)}</b>\n\n{text}",
        reply_markup=admin_prices_kb(products),
    )
