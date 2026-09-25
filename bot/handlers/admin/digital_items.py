from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import confirm_delete_kb, digital_items_kb, items_input_done_kb
from bot.states.states import DigitalItemStates
from bot.utils.formatting import esc
from bot.utils.ui import render

logger = logging.getLogger(__name__)
router = Router(name="admin_digital_items")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MAX_FILE_SIZE = 5 * 1024 * 1024

ADD_ITEMS_TEXT = (
    "📦 <b>Добавление позиций</b>\n\n"
    "Отправьте цифровые позиции — каждая с новой строки. Можно несколькими "
    "сообщениями подряд или .txt-файлом (по позиции на строку).\n\n"
    "Например:\n<code>KEY-001\nKEY-002\nKEY-003</code>\n\n"
    "Пустые строки игнорируются. Когда закончите — нажмите «✅ Готово»."
)


async def _items_screen_text(session: AsyncSession, product) -> str:  # noqa: ANN001
    available = await repo.count_available_items(session, product.id)
    total = await repo.count_total_items(session, product.id)
    return (
        f"📦 <b>Позиции товара «{esc(product.name)}»</b>\n\n"
        f"📊 Доступно: {available} / {total} (продано: {total - available})"
    )


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
    await render(callback, await _items_screen_text(session, product), digital_items_kb(product_id, available, total))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:items:add:\d+$"))
async def cb_items_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[3])
    await state.set_state(DigitalItemStates.waiting_items)
    await state.update_data(product_id=product_id)
    await render(callback, ADD_ITEMS_TEXT, items_input_done_kb(product_id))
    await callback.answer()


async def _read_lines(message: Message) -> list[str] | None:
    if message.text:
        return message.text.splitlines()
    doc = message.document
    if doc is None:
        return None
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await message.answer("Файл слишком большой (максимум 5 МБ).")
        return []
    buffer = await message.bot.download(doc)
    raw = buffer.read() if buffer else b""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding).splitlines()
        except UnicodeDecodeError:
            continue
    await message.answer("Не удалось прочитать файл — сохраните его в кодировке UTF-8.")
    return []


@router.message(DigitalItemStates.waiting_items)
async def process_items_add(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    product = await repo.get_product(session, product_id) if product_id else None
    if product is None:
        await state.clear()
        await message.answer("Товар не найден.")
        return

    lines = await _read_lines(message)
    if lines is None:
        await message.answer("Отправьте позиции текстом или .txt-файлом.", reply_markup=items_input_done_kb(product_id))
        return
    added = await repo.add_digital_items(session, product_id, lines)
    if added == 0:
        await message.answer(
            "Не удалось распознать ни одной позиции. Попробуйте ещё раз.", reply_markup=items_input_done_kb(product_id)
        )
        return

    # Stay in the state: large batches often don't fit into one message.
    await message.answer(
        f"✅ Добавлено позиций: {added}\n" + (await _items_screen_text(session, product)).split("\n\n", 1)[1]
        + "\n\nОтправьте ещё или нажмите «✅ Готово».",
        reply_markup=items_input_done_kb(product_id),
    )


@router.callback_query(F.data.regexp(r"^adm:items:delconfirm:\d+$"))
async def cb_items_delete_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    available = await repo.count_available_items(session, product_id)
    await render(
        callback,
        f"❗️ Удалить все ({available}) непроданные позиции этого товара?\n\n"
        "Проданные позиции останутся в заказах покупателей.\n\nВы уверены?",
        confirm_delete_kb(f"adm:items:delete:{product_id}", f"adm:items:{product_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:items:delete:\d+$"))
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
