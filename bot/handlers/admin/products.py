from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import (
    add_product_finish_kb,
    add_product_skip_photo_kb,
    admin_product_edit_menu_kb,
    admin_product_menu_kb,
    admin_products_kb,
    choose_category_kb,
    confirm_delete_kb,
    items_input_done_kb,
    product_photo_kb,
)
from bot.keyboards.user import cancel_kb, skip_cancel_kb
from bot.states.states import DigitalItemStates, ProductStates
from bot.utils.formatting import esc, format_price, parse_price
from bot.utils.ui import render

router = Router(name="admin_products")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MAX_NAME_LEN = 100
MAX_DESCRIPTION_LEN = 800
PRICE_HINT = "Некорректная цена. Введите положительное число, например 199.90"


def _id_at(callback: CallbackQuery, index: int = 3) -> int:
    return int(callback.data.split(":")[index])


def _validate_name(message: Message) -> str | None:
    name = (message.text or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    return name


def _description_from(message: Message) -> str | None:
    """HTML-formatted description, '' to clear, None if invalid."""
    if not message.text:
        return None
    if message.text.strip() == "-":
        return ""
    description = message.html_text.strip()
    return description if len(description) <= MAX_DESCRIPTION_LEN else None


async def _product_text(session: AsyncSession, product) -> str:  # noqa: ANN001
    available = await repo.count_available_items(session, product.id)
    total = await repo.count_total_items(session, product.id)
    category = await repo.get_category(session, product.category_id)
    return (
        f"📦 <b>{esc(product.name)}</b>\n\n"
        f"📁 Категория: {esc(category.name) if category else '—'}\n"
        f"💰 Цена: {format_price(product.price)}\n"
        f"📦 Остаток: {available} / {total}\n"
        f"📝 Описание: {product.description or '—'}\n"
        f"📊 Статус: {'🟢 Активен' if product.is_active else '🔴 Выключен'}\n"
    )


async def _show_product(callback: CallbackQuery, session: AsyncSession, product_id: int, note: str = "") -> None:
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await render(
        callback, note + await _product_text(session, product), admin_product_menu_kb(product), photo=product.photo_file_id
    )
    await callback.answer()


async def _answer_product(message: Message, session: AsyncSession, product_id: int, note: str) -> None:
    product = await repo.get_product(session, product_id)
    if product is None:
        await message.answer("Товар не найден.")
        return
    await message.answer(note + "\n\n" + await _product_text(session, product), reply_markup=admin_product_menu_kb(product))


@router.callback_query(F.data.regexp(r"^adm:prod:list:\d+(:\d+)?$"))
async def cb_list_products(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    parts = callback.data.split(":")
    category_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 0
    category = await repo.get_category(session, category_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    products = await repo.list_products(session, category_id)
    text = f"📁 <b>{esc(category.name)}</b>\n\n" + (f"Товаров: {len(products)}" if products else "Товаров пока нет.")
    await render(callback, text, admin_products_kb(products, category_id, page))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:prod:open:\d+$"))
async def cb_open_product(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_product(callback, session, _id_at(callback))


# --------------------------------------------------------------------------- #
# Add product (stepwise FSM)
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:prod:add:\d+$"))
async def cb_add_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ProductStates.add_name)
    await state.update_data(category_id=_id_at(callback))
    await render(
        callback, f"➕ <b>Добавление товара</b>\n\n1/4. Введите название товара (до {MAX_NAME_LEN} символов):", cancel_kb()
    )
    await callback.answer()


@router.message(ProductStates.add_name)
async def process_add_name(message: Message, state: FSMContext) -> None:
    name = _validate_name(message)
    if name is None:
        await message.answer(f"Название должно быть текстом от 1 до {MAX_NAME_LEN} символов.", reply_markup=cancel_kb())
        return
    await state.update_data(name=name)
    await state.set_state(ProductStates.add_description)
    await message.answer(
        "2/4. Введите описание товара (форматирование сохранится) или пропустите:",
        reply_markup=skip_cancel_kb("adm:prod:addskipdesc"),
    )


async def _ask_price(target: Message, state: FSMContext) -> None:
    await state.set_state(ProductStates.add_price)
    await target.answer("3/4. Введите цену товара (число):", reply_markup=cancel_kb())


@router.message(ProductStates.add_description)
async def process_add_description(message: Message, state: FSMContext) -> None:
    description = _description_from(message)
    if description is None:
        await message.answer(
            f"Отправьте описание текстом (до {MAX_DESCRIPTION_LEN} символов).",
            reply_markup=skip_cancel_kb("adm:prod:addskipdesc"),
        )
        return
    await state.update_data(description=description)
    await _ask_price(message, state)


@router.callback_query(F.data == "adm:prod:addskipdesc", ProductStates.add_description)
async def cb_add_skip_description(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description="")
    await callback.answer()
    await _ask_price(callback.message, state)


@router.message(ProductStates.add_price)
async def process_add_price(message: Message, state: FSMContext) -> None:
    price = parse_price(message.text)
    if price is None:
        await message.answer(PRICE_HINT, reply_markup=cancel_kb())
        return
    await state.update_data(price=str(price))
    await state.set_state(ProductStates.add_photo)
    await message.answer(
        "4/4. Отправьте фотографию товара (или пропустите этот шаг):",
        reply_markup=add_product_skip_photo_kb(),
    )


async def _create_from_state(session: AsyncSession, state: FSMContext, photo: str | None):  # noqa: ANN202
    data = await state.get_data()
    if not all(k in data for k in ("category_id", "name", "price")):
        return None
    if await repo.get_category(session, data["category_id"]) is None:
        return None
    product = await repo.create_product(
        session, data["category_id"], data["name"], data.get("description", ""), Decimal(data["price"])
    )
    if photo:
        await repo.set_product_photo(session, product.id, photo)
    await state.set_state(None)
    await state.set_data({"last_product_id": product.id})
    return product


@router.message(ProductStates.add_photo, F.photo)
async def process_add_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    product = await _create_from_state(session, state, message.photo[-1].file_id)
    if product is None:
        await state.clear()
        await message.answer("⚠️ Данные формы потерялись (возможно, бот перезапускался). Начните заново.")
        return
    await message.answer(
        f"✅ Товар «{esc(product.name)}» создан.\n\nДобавить цифровые позиции сейчас?",
        reply_markup=add_product_finish_kb(),
    )


@router.message(ProductStates.add_photo)
async def process_add_photo_invalid(message: Message) -> None:
    await message.answer("Отправьте фотографию или нажмите «Пропустить фото».", reply_markup=add_product_skip_photo_kb())


@router.callback_query(F.data == "adm:prod:addskipphoto", ProductStates.add_photo)
async def cb_add_product_skip_photo(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    product = await _create_from_state(session, state, None)
    if product is None:
        await state.clear()
        await callback.answer("Данные формы потерялись. Начните заново.", show_alert=True)
        return
    await render(
        callback,
        f"✅ Товар «{esc(product.name)}» создан.\n\nДобавить цифровые позиции сейчас?",
        add_product_finish_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:prod:addfinish:later")
async def cb_add_product_finish_later(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    if not data.get("last_product_id"):
        await callback.answer("Откройте товар через список товаров.", show_alert=True)
        return
    await _show_product(callback, session, data["last_product_id"])


@router.callback_query(F.data == "adm:prod:addfinish:now")
async def cb_add_product_finish_now(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("last_product_id")
    await state.clear()
    if not product_id:
        await callback.answer("Откройте товар через список товаров.", show_alert=True)
        return
    from bot.handlers.admin.digital_items import ADD_ITEMS_TEXT

    await state.set_state(DigitalItemStates.waiting_items)
    await state.update_data(product_id=product_id)
    await render(callback, ADD_ITEMS_TEXT, items_input_done_kb(product_id))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Edit product
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:prod:edit:\d+$"))
async def cb_edit_menu(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    product = await repo.get_product(session, _id_at(callback))
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await render(callback, f"✏️ <b>{esc(product.name)}</b>\n\nЧто изменить?", admin_product_edit_menu_kb(product))
    await callback.answer()


_EDIT_PROMPTS = {
    "editname": (ProductStates.edit_name, f"✏️ Введите новое название (до {MAX_NAME_LEN} символов):"),
    "editdesc": (ProductStates.edit_description, "📝 Введите новое описание (форматирование сохранится). «-» — убрать описание:"),
    "editprice": (ProductStates.edit_price, "💰 Введите новую цену (число):"),
}


@router.callback_query(F.data.regexp(r"^adm:prod:(editname|editdesc|editprice):\d+$"))
async def cb_edit_field_start(callback: CallbackQuery, state: FSMContext) -> None:
    new_state, prompt = _EDIT_PROMPTS[callback.data.split(":")[2]]
    await state.set_state(new_state)
    await state.update_data(product_id=_id_at(callback))
    await render(callback, prompt, cancel_kb())
    await callback.answer()


@router.message(ProductStates.edit_name)
async def process_edit_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    name = _validate_name(message)
    if name is None:
        await message.answer(f"Название должно быть текстом от 1 до {MAX_NAME_LEN} символов.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    await repo.update_product_field(session, data.get("product_id", 0), name=name)
    await _answer_product(message, session, data.get("product_id", 0), "✅ Название обновлено.")


@router.message(ProductStates.edit_description)
async def process_edit_desc(message: Message, session: AsyncSession, state: FSMContext) -> None:
    description = _description_from(message)
    if description is None:
        await message.answer(f"Отправьте описание текстом (до {MAX_DESCRIPTION_LEN} символов).", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    await repo.update_product_field(session, data.get("product_id", 0), description=description or None)
    await _answer_product(message, session, data.get("product_id", 0), "✅ Описание обновлено.")


@router.message(ProductStates.edit_price)
async def process_edit_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    price = parse_price(message.text)
    if price is None:
        await message.answer(PRICE_HINT, reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    await repo.update_product_field(session, data.get("product_id", 0), price=price)
    await _answer_product(message, session, data.get("product_id", 0), "✅ Цена обновлена.")


@router.callback_query(F.data.regexp(r"^adm:prod:editcat:\d+$"))
async def cb_edit_category_start(callback: CallbackQuery, session: AsyncSession) -> None:
    categories = await repo.list_categories(session)
    await render(callback, "📁 Выберите новую категорию:", choose_category_kb(categories, _id_at(callback)))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:prod:setcat:\d+:\d+$"))
async def cb_edit_category_set(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, _, product_id, category_id = callback.data.split(":")
    if await repo.get_category(session, int(category_id)) is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await repo.update_product_field(session, int(product_id), category_id=int(category_id))
    await _show_product(callback, session, int(product_id), note="✅ Категория обновлена.\n\n")


@router.callback_query(F.data.regexp(r"^adm:prod:toggle:\d+$"))
async def cb_toggle_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = _id_at(callback)
    await repo.toggle_product(session, product_id)
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await render(
        callback,
        f"✅ Статус обновлён: {'🟢 Активен' if product.is_active else '🔴 Выключен'}\n\nЧто ещё изменить?",
        admin_product_edit_menu_kb(product),
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Photo
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:prod:photo:\d+$"))
async def cb_product_photo_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    product = await repo.get_product(session, _id_at(callback))
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    text = "🖼 Фото товара" if product.photo_file_id else "🖼 У товара пока нет фото."
    await render(callback, text, product_photo_kb(product.id, bool(product.photo_file_id)), photo=product.photo_file_id)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:prod:photoset:\d+$"))
async def cb_product_photo_set(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProductStates.waiting_photo)
    await state.update_data(product_id=_id_at(callback))
    await render(callback, "📷 Отправьте новую фотографию товара.", cancel_kb())
    await callback.answer()


@router.message(ProductStates.waiting_photo, F.photo)
async def process_product_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    product = await repo.get_product(session, data.get("product_id", 0))
    if product is None:
        await message.answer("Товар не найден.")
        return
    file_id = message.photo[-1].file_id
    await repo.set_product_photo(session, product.id, file_id)
    await session.refresh(product)
    await message.answer_photo(file_id, caption="✅ Фото сохранено.", reply_markup=admin_product_menu_kb(product))


@router.message(ProductStates.waiting_photo)
async def process_product_photo_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте фотографию (или нажмите «Отмена»).", reply_markup=cancel_kb())


@router.callback_query(F.data.regexp(r"^adm:prod:photodel:\d+$"))
async def cb_product_photo_del(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = _id_at(callback)
    await repo.set_product_photo(session, product_id, None)
    await _show_product(callback, session, product_id, note="🗑 Фото удалено.\n\n")


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:prod:delconfirm:\d+$"))
async def cb_delete_product_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = _id_at(callback)
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    available = await repo.count_available_items(session, product_id)
    await render(
        callback,
        f"❗️ Удалить товар «{esc(product.name)}»?\n\n"
        f"Непроданные позиции ({available}) будут удалены. Уже купленное останется у покупателей.\n\n"
        "💡 Если хотите просто скрыть товар — выключите его в меню «Изменить».\n\nВы уверены?",
        confirm_delete_kb(f"adm:prod:delete:{product_id}", f"adm:prod:open:{product_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:prod:delete:\d+$"))
async def cb_delete_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = _id_at(callback)
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар уже удалён", show_alert=True)
        return
    category_id = product.category_id
    await repo.delete_product(session, product_id)
    products = await repo.list_products(session, category_id)
    await render(callback, "🗑 Товар удалён.", admin_products_kb(products, category_id))
    await callback.answer()
