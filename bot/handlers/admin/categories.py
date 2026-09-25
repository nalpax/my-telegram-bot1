from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import (
    admin_categories_kb,
    admin_category_menu_kb,
    category_photo_kb,
    confirm_delete_kb,
)
from bot.keyboards.user import cancel_kb
from bot.states.states import CategoryStates
from bot.utils.formatting import esc
from bot.utils.ui import render

router = Router(name="admin_categories")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 800


def _category_id(callback: CallbackQuery) -> int:
    return int(callback.data.split(":")[3])


async def _category_text(session: AsyncSession, category) -> str:  # noqa: ANN001
    products = await repo.list_products(session, category.id)
    active = sum(1 for p in products if p.is_active)
    text = f"📁 <b>{esc(category.name)}</b>\n\n"
    text += f"Статус: {'🟢 Активна' if category.is_active else '🔴 Выключена'}\n"
    text += f"Товаров: {len(products)} (активных: {active})\n"
    if category.description:
        text += f"Описание: {category.description}\n"
    return text


async def _show_category(callback: CallbackQuery, session: AsyncSession, category_id: int, note: str = "") -> None:
    category = await repo.get_category(session, category_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await render(
        callback,
        note + await _category_text(session, category),
        admin_category_menu_kb(category),
        photo=category.photo_file_id,
    )
    await callback.answer()


def _validate_name(message: Message) -> str | None:
    name = (message.text or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    return name


@router.callback_query(F.data == "adm:cat:list")
async def cb_list_categories(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    categories = await repo.list_categories(session)
    text = "📦 <b>Товары — категории</b>\n\nВыберите категорию:" if categories else "📦 Категорий пока нет."
    await render(callback, text, admin_categories_kb(categories))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:cat:open:\d+$"))
async def cb_open_category(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    await _show_category(callback, session, _category_id(callback))


# --------------------------------------------------------------------------- #
# Add category
# --------------------------------------------------------------------------- #


@router.callback_query(F.data == "adm:cat:add")
async def cb_add_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CategoryStates.waiting_name)
    await render(callback, f"➕ Введите название новой категории (до {MAX_NAME_LEN} символов):", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_name)
async def process_add_category(message: Message, session: AsyncSession, state: FSMContext) -> None:
    name = _validate_name(message)
    if name is None:
        await message.answer(
            f"Название должно быть текстом от 1 до {MAX_NAME_LEN} символов. Попробуйте ещё раз.",
            reply_markup=cancel_kb(),
        )
        return
    category = await repo.create_category(session, name)
    await state.clear()
    await message.answer(
        f"✅ Категория «{esc(category.name)}» создана.", reply_markup=admin_category_menu_kb(category)
    )


# --------------------------------------------------------------------------- #
# Rename
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:cat:rename:\d+$"))
async def cb_rename_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CategoryStates.waiting_rename)
    await state.update_data(category_id=_category_id(callback))
    await render(callback, f"✏️ Введите новое название категории (до {MAX_NAME_LEN} символов):", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_rename)
async def process_rename_category(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    name = _validate_name(message)
    if name is None:
        await message.answer(
            f"Название должно быть текстом от 1 до {MAX_NAME_LEN} символов.", reply_markup=cancel_kb()
        )
        return
    await state.clear()
    category = await repo.get_category(session, data.get("category_id", 0))
    if category is None:
        await message.answer("Категория не найдена.")
        return
    await repo.rename_category(session, category.id, name)
    await session.refresh(category)
    await message.answer("✅ Название обновлено.", reply_markup=admin_category_menu_kb(category))


# --------------------------------------------------------------------------- #
# Description
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:cat:descr:\d+$"))
async def cb_descr_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CategoryStates.waiting_description)
    await state.update_data(category_id=_category_id(callback))
    await render(
        callback,
        "📝 Введите описание категории (форматирование сохранится).\n"
        "Чтобы убрать описание, отправьте «-».",
        cancel_kb(),
    )
    await callback.answer()


@router.message(CategoryStates.waiting_description)
async def process_descr_category(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Отправьте описание текстом.", reply_markup=cancel_kb())
        return
    description = "" if message.text.strip() == "-" else message.html_text
    if len(description) > MAX_DESCRIPTION_LEN:
        await message.answer(f"Слишком длинно (максимум {MAX_DESCRIPTION_LEN} символов).", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    await state.clear()
    category = await repo.get_category(session, data.get("category_id", 0))
    if category is None:
        await message.answer("Категория не найдена.")
        return
    await repo.set_category_description(session, category.id, description)
    await session.refresh(category)
    await message.answer("✅ Описание обновлено.", reply_markup=admin_category_menu_kb(category))


# --------------------------------------------------------------------------- #
# Toggle / reorder
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:cat:toggle:\d+$"))
async def cb_toggle_category(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = _category_id(callback)
    await repo.toggle_category(session, category_id)
    await _show_category(callback, session, category_id)


@router.callback_query(F.data.regexp(r"^adm:cat:(up|down):\d+$"))
async def cb_move(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = _category_id(callback)
    direction = -1 if callback.data.split(":")[2] == "up" else 1
    moved = await repo.move_category(session, category_id, direction)
    if not moved:
        await callback.answer("Категория уже на краю списка")
        return
    await _show_category(callback, session, category_id, note="↕️ Порядок обновлён\n\n")


# --------------------------------------------------------------------------- #
# Photo
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:cat:photo:\d+$"))
async def cb_category_photo_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    category = await repo.get_category(session, _category_id(callback))
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    text = "🖼 Фото категории" if category.photo_file_id else "🖼 У категории пока нет фото."
    await render(callback, text, category_photo_kb(category.id, bool(category.photo_file_id)), photo=category.photo_file_id)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:cat:photoset:\d+$"))
async def cb_category_photo_set(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CategoryStates.waiting_photo)
    await state.update_data(category_id=_category_id(callback))
    await render(callback, "📷 Отправьте новую фотографию категории.", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_photo, F.photo)
async def process_category_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    category = await repo.get_category(session, data.get("category_id", 0))
    if category is None:
        await message.answer("Категория не найдена.")
        return
    file_id = message.photo[-1].file_id
    await repo.set_category_photo(session, category.id, file_id)
    await session.refresh(category)
    await message.answer_photo(file_id, caption="✅ Фото сохранено.", reply_markup=admin_category_menu_kb(category))


@router.message(CategoryStates.waiting_photo)
async def process_category_photo_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте фотографию (или нажмите «Отмена»).", reply_markup=cancel_kb())


@router.callback_query(F.data.regexp(r"^adm:cat:photodel:\d+$"))
async def cb_category_photo_del(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = _category_id(callback)
    await repo.set_category_photo(session, category_id, None)
    await _show_category(callback, session, category_id, note="🗑 Фото удалено.\n\n")


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:cat:delconfirm:\d+$"))
async def cb_delete_category_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = _category_id(callback)
    category = await repo.get_category(session, category_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    products = await repo.list_products(session, category_id)
    await render(
        callback,
        f"❗️ Удалить категорию «{esc(category.name)}»?\n\n"
        f"Вместе с ней будут удалены товары ({len(products)}) и их непроданные позиции. "
        "Уже купленное останется у покупателей в заказах.\n\nВы уверены?",
        confirm_delete_kb(f"adm:cat:delete:{category_id}", f"adm:cat:open:{category_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:cat:delete:\d+$"))
async def cb_delete_category(callback: CallbackQuery, session: AsyncSession) -> None:
    await repo.delete_category(session, _category_id(callback))
    categories = await repo.list_categories(session)
    await render(callback, "🗑 Категория удалена.\n\n📦 <b>Товары — категории</b>", admin_categories_kb(categories))
    await callback.answer()
