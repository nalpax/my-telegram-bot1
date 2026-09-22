from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import confirm_delete_kb, digital_items_kb
from bot.keyboards.user import cancel_kb
from bot.states.states import DigitalItemStates
from bot.utils.ui import render

router = Router(name="admin_digital_items")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data.regexp(r"^adm:items:\d+$"))
async def cb_items_menu(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    product_id = int(callback.data.split(":")[2])
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    available = await repo.count_available_items(session, product_id)
    total = await repo.count_total_items(session, product_id)
    text = f"📦 <b>Позиции товара «{product.name}»</b>\n\n📊 Доступно: {available} / {total}"
    await render(callback, text, digital_items_kb(product_id, available, total))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:items:add:"))
async def cb_items_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[3])
    await state.set_state(DigitalItemStates.waiting_items)
    await state.update_data(product_id=product_id)
    await render(
        callback,
        "📦 Отправьте цифровые позиции — каждая с новой строки.\n\n"
        "Например:\nKEY-001\nKEY-002\nKEY-003\n\nПустые строки будут проигнорированы.",
        cancel_kb(),
    )
    await callback.answer()


@router.message(DigitalItemStates.waiting_items)
async def process_items_add(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    product = await repo.get_product(session, product_id) if product_id else None
    if product is None:
        await state.clear()
        await message.answer("Товар не найден.")
        return

    lines = (message.text or "").split("\n")
    added = await repo.add_digital_items(session, product_id, lines)
    await state.clear()

    if added == 0:
        await message.answer("Не удалось распознать ни одной позиции. Попробуйте ещё раз.", reply_markup=cancel_kb())
        return

    available = await repo.count_available_items(session, product_id)
    total = await repo.count_total_items(session, product_id)
    await message.answer(
        f"✅ Добавлено позиций: {added}\n📊 Доступно: {available} / {total}",
        reply_markup=digital_items_kb(product_id, available, total),
    )


@router.callback_query(F.data.startswith("adm:items:delconfirm:"))
async def cb_items_delete_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    available = await repo.count_available_items(session, product_id)
    await render(
        callback,
        f"❗️ Удалить все ({available}) непроданные позиции этого товара?\n\nВы уверены?",
        confirm_delete_kb(f"adm:items:delete:{product_id}", f"adm:items:{product_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:items:delete:"))
async def cb_items_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    deleted = await repo.delete_available_items(session, product_id)
    available = await repo.count_available_items(session, product_id)
    total = await repo.count_total_items(session, product_id)
    await render(
        callback,
        f"🗑 Удалено позиций: {deleted}\n📊 Доступно: {available} / {total}",
        digital_items_kb(product_id, available, total),
    )
    await callback.answer()
