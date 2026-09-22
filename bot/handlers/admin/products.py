from __future__ import annotations

from decimal import Decimal, InvalidOperation

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
    product_photo_kb,
)
from bot.keyboards.user import cancel_kb
from bot.states.states import DigitalItemStates, ProductStates
from bot.utils.formatting import format_price
from bot.utils.ui import render

router = Router(name="admin_products")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _product_text(session: AsyncSession, product) -> str:
    available = await repo.count_available_items(session, product.id)
    total = await repo.count_total_items(session, product.id)
    return (
        f"📦 <b>{product.name}</b>\n\n"
        f"💰 Цена: {format_price(product.price)}\n"
        f"📦 Остаток: {available} / {total}\n"
        f"📝 Описание: {product.description or '—'}\n"
        f"📊 Статус: {'🟢 Активен' if product.is_active else '🔴 Выключен'}\n"
    )


@router.callback_query(F.data.startswith("adm:prod:list:"))
async def cb_list_products(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    category_id = int(callback.data.split(":")[3])
    category = await repo.get_category(session, category_id)
    products = await repo.list_products(session, category_id)
    text = f"📁 <b>{category.name}</b>\n\nТовары:" if products else f"📁 <b>{category.name}</b>\n\nТоваров пока нет."
    await render(callback, text, admin_products_kb(products, category_id))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:open:"))
async def cb_open_product(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    product_id = int(callback.data.split(":")[3])
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    text = await _product_text(session, product)
    await render(callback, text, admin_product_menu_kb(product), photo=product.photo_file_id)
    await callback.answer()


# --------------------------------------------------------------------------- #
# Add product (stepwise FSM)
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:prod:add:"))
async def cb_add_product_start(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[3])
    await state.set_state(ProductStates.add_name)
    await state.update_data(category_id=category_id)
    await render(callback, "➕ <b>Добавление товара</b>\n\n1/4. Введите название товара:", cancel_kb())
    await callback.answer()


@router.message(ProductStates.add_name)
async def process_add_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым.", reply_markup=cancel_kb())
        return
    await state.update_data(name=name)
    await state.set_state(ProductStates.add_description)
    await message.answer("2/4. Введите описание товара:", reply_markup=cancel_kb())


@router.message(ProductStates.add_description)
async def process_add_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(ProductStates.add_price)
    await message.answer("3/4. Введите цену товара (число):", reply_markup=cancel_kb())


@router.message(ProductStates.add_price)
async def process_add_price(message: Message, state: FSMContext) -> None:
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if price <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("Некорректная цена. Введите число, например 199.90", reply_markup=cancel_kb())
        return
    await state.update_data(price=str(price))
    await state.set_state(ProductStates.add_photo)
    await message.answer(
        "4/4. Отправьте фотографию товара (или пропустите этот шаг):",
        reply_markup=add_product_skip_photo_kb(),
    )


@router.message(ProductStates.add_photo, F.photo)
async def process_add_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product = await repo.create_product(
        session, data["category_id"], data["name"], data["description"], Decimal(data["price"])
    )
    await repo.set_product_photo(session, product.id, message.photo[-1].file_id)
    await state.update_data(last_product_id=product.id)
    await state.set_state(None)
    await message.answer(
        f"✅ Товар «{product.name}» создан.\n\nДобавить цифровые позиции сейчас?",
        reply_markup=add_product_finish_kb(),
    )


@router.callback_query(F.data == "adm:prod:addskipphoto")
async def cb_add_product_skip_photo(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product = await repo.create_product(
        session, data["category_id"], data["name"], data["description"], Decimal(data["price"])
    )
    await state.update_data(last_product_id=product.id)
    await render(
        callback,
        f"✅ Товар «{product.name}» создан.\n\nДобавить цифровые позиции сейчас?",
        add_product_finish_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:prod:addfinish:later")
async def cb_add_product_finish_later(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product = await repo.get_product(session, data.get("last_product_id"))
    await state.clear()
    if product is None:
        await callback.answer()
        return
    text = await _product_text(session, product)
    await render(callback, text, admin_product_menu_kb(product), photo=product.photo_file_id)
    await callback.answer()


@router.callback_query(F.data == "adm:prod:addfinish:now")
async def cb_add_product_finish_now(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("last_product_id")
    await state.clear()
    await state.set_state(DigitalItemStates.waiting_items)
    await state.update_data(product_id=product_id)
    await render(
        callback,
        "📦 Отправьте цифровые позиции — каждая с новой строки. Пустые строки будут проигнорированы.",
        cancel_kb(),
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# Edit product
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:prod:edit:"))
async def cb_edit_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await render(callback, "✏️ Что изменить?", admin_product_edit_menu_kb(product))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:editname:"))
async def cb_edit_name_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[3])
    await state.set_state(ProductStates.edit_name)
    await state.update_data(product_id=product_id)
    await render(callback, "✏️ Введите новое название:", cancel_kb())
    await callback.answer()


@router.message(ProductStates.edit_name)
async def process_edit_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым.", reply_markup=cancel_kb())
        return
    await repo.update_product_field(session, data["product_id"], name=name)
    product = await repo.get_product(session, data["product_id"])
    await state.clear()
    await message.answer("✅ Название обновлено.", reply_markup=admin_product_menu_kb(product))


@router.callback_query(F.data.startswith("adm:prod:editdesc:"))
async def cb_edit_desc_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[3])
    await state.set_state(ProductStates.edit_description)
    await state.update_data(product_id=product_id)
    await render(callback, "📝 Введите новое описание:", cancel_kb())
    await callback.answer()


@router.message(ProductStates.edit_description)
async def process_edit_desc(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    await repo.update_product_field(session, data["product_id"], description=(message.text or "").strip())
    product = await repo.get_product(session, data["product_id"])
    await state.clear()
    await message.answer("✅ Описание обновлено.", reply_markup=admin_product_menu_kb(product))


@router.callback_query(F.data.startswith("adm:prod:editprice:"))
async def cb_edit_price_start(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[3])
    await state.set_state(ProductStates.edit_price)
    await state.update_data(product_id=product_id)
    await render(callback, "💰 Введите новую цену (число):", cancel_kb())
    await callback.answer()


@router.message(ProductStates.edit_price)
async def process_edit_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        price = Decimal((message.text or "").strip().replace(",", "."))
        if price <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("Некорректная цена. Введите число, например 199.90", reply_markup=cancel_kb())
        return
    await repo.update_product_field(session, data["product_id"], price=price)
    product = await repo.get_product(session, data["product_id"])
    await state.clear()
    await message.answer("✅ Цена обновлена.", reply_markup=admin_product_menu_kb(product))


@router.callback_query(F.data.startswith("adm:prod:editcat:"))
async def cb_edit_category_start(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    categories = await repo.list_categories(session)
    await render(callback, "📁 Выберите новую категорию:", choose_category_kb(categories, product_id))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:setcat:"))
async def cb_edit_category_set(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, _, product_id, category_id = callback.data.split(":")
    await repo.update_product_field(session, int(product_id), category_id=int(category_id))
    product = await repo.get_product(session, int(product_id))
    await render(callback, "✅ Категория обновлена.", admin_product_menu_kb(product))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:toggle:"))
async def cb_toggle_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    await repo.toggle_product(session, product_id)
    product = await repo.get_product(session, product_id)
    await render(callback, "✅ Статус обновлён.", admin_product_edit_menu_kb(product))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Photo
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:prod:photo:"))
async def cb_product_photo_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    text = "🖼 Фото товара" if product.photo_file_id else "🖼 У товара пока нет фото."
    await render(callback, text, product_photo_kb(product.id, bool(product.photo_file_id)), photo=product.photo_file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:photoset:"))
async def cb_product_photo_set(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[3])
    await state.set_state(ProductStates.waiting_photo)
    await state.update_data(product_id=product_id)
    await render(callback, "📷 Отправьте новую фотографию товара.", cancel_kb())
    await callback.answer()


@router.message(ProductStates.waiting_photo, F.photo)
async def process_product_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    file_id = message.photo[-1].file_id
    await repo.set_product_photo(session, data["product_id"], file_id)
    product = await repo.get_product(session, data["product_id"])
    await state.clear()
    await message.answer_photo(file_id, caption="✅ Фото сохранено.", reply_markup=admin_product_menu_kb(product))


@router.message(ProductStates.waiting_photo)
async def process_product_photo_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте фотографию (или нажмите «Отмена»).", reply_markup=cancel_kb())


@router.callback_query(F.data.startswith("adm:prod:photodel:"))
async def cb_product_photo_del(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    await repo.set_product_photo(session, product_id, None)
    product = await repo.get_product(session, product_id)
    await render(callback, "🗑 Фото удалено.", admin_product_menu_kb(product))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:prod:delconfirm:"))
async def cb_delete_product_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await render(
        callback,
        f"❗️ Удалить товар «{product.name}»?\n\nВы уверены?",
        confirm_delete_kb(f"adm:prod:delete:{product_id}", f"adm:prod:open:{product_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:delete:"))
async def cb_delete_product(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[3])
    product = await repo.get_product(session, product_id)
    category_id = product.category_id if product else None
    await repo.delete_product(session, product_id)
    products = await repo.list_products(session, category_id) if category_id else []
    await render(callback, "🗑 Товар удалён.", admin_products_kb(products, category_id or 0))
    await callback.answer()
