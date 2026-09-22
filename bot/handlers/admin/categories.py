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
from bot.utils.ui import render

router = Router(name="admin_categories")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _category_text(category) -> str:
    text = f"📁 <b>{category.name}</b>\n\n"
    text += f"Статус: {'🟢 Активна' if category.is_active else '🔴 Выключена'}\n"
    if category.description:
        text += f"Описание: {category.description}\n"
    return text


@router.callback_query(F.data == "adm:cat:list")
async def cb_list_categories(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    categories = await repo.list_categories(session)
    text = "📦 <b>Товары — категории</b>" if categories else "📦 Категорий пока нет."
    await render(callback, text, admin_categories_kb(categories))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:cat:open:"))
async def cb_open_category(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    category = await repo.get_category(session, category_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await render(callback, _category_text(category), admin_category_menu_kb(category), photo=category.photo_file_id)
    await callback.answer()


# --------------------------------------------------------------------------- #
# Add category
# --------------------------------------------------------------------------- #


@router.callback_query(F.data == "adm:cat:add")
async def cb_add_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CategoryStates.waiting_name)
    await render(callback, "➕ Введите название новой категории:", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_name)
async def process_add_category(message: Message, session: AsyncSession, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуйте ещё раз.", reply_markup=cancel_kb())
        return
    category = await repo.create_category(session, name)
    await state.clear()
    await message.answer(
        f"✅ Категория «{category.name}» создана.", reply_markup=admin_category_menu_kb(category)
    )


# --------------------------------------------------------------------------- #
# Rename
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:cat:rename:"))
async def cb_rename_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[3])
    await state.set_state(CategoryStates.waiting_rename)
    await state.update_data(category_id=category_id)
    await render(callback, "✏️ Введите новое название категории:", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_rename)
async def process_rename_category(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуйте ещё раз.", reply_markup=cancel_kb())
        return
    await repo.rename_category(session, data["category_id"], name)
    category = await repo.get_category(session, data["category_id"])
    await state.clear()
    await message.answer("✅ Название обновлено.", reply_markup=admin_category_menu_kb(category))


# --------------------------------------------------------------------------- #
# Description
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:cat:descr:"))
async def cb_descr_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[3])
    await state.set_state(CategoryStates.waiting_description)
    await state.update_data(category_id=category_id)
    await render(callback, "📝 Введите описание категории:", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_description)
async def process_descr_category(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    await repo.set_category_description(session, data["category_id"], message.text or "")
    category = await repo.get_category(session, data["category_id"])
    await state.clear()
    await message.answer("✅ Описание обновлено.", reply_markup=admin_category_menu_kb(category))


# --------------------------------------------------------------------------- #
# Toggle / reorder
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:cat:toggle:"))
async def cb_toggle_category(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    await repo.toggle_category(session, category_id)
    category = await repo.get_category(session, category_id)
    await render(callback, _category_text(category), admin_category_menu_kb(category), photo=category.photo_file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:cat:up:"))
async def cb_move_up(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    await repo.move_category(session, category_id, -1)
    category = await repo.get_category(session, category_id)
    await render(callback, _category_text(category), admin_category_menu_kb(category), photo=category.photo_file_id)
    await callback.answer("Порядок обновлён")


@router.callback_query(F.data.startswith("adm:cat:down:"))
async def cb_move_down(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    await repo.move_category(session, category_id, 1)
    category = await repo.get_category(session, category_id)
    await render(callback, _category_text(category), admin_category_menu_kb(category), photo=category.photo_file_id)
    await callback.answer("Порядок обновлён")


# --------------------------------------------------------------------------- #
# Photo
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:cat:photo:"))
async def cb_category_photo_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    category = await repo.get_category(session, category_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    text = "🖼 Фото категории" if category.photo_file_id else "🖼 У категории пока нет фото."
    await render(callback, text, category_photo_kb(category.id, bool(category.photo_file_id)), photo=category.photo_file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:cat:photoset:"))
async def cb_category_photo_set(callback: CallbackQuery, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[3])
    await state.set_state(CategoryStates.waiting_photo)
    await state.update_data(category_id=category_id)
    await render(callback, "📷 Отправьте новую фотографию категории.", cancel_kb())
    await callback.answer()


@router.message(CategoryStates.waiting_photo, F.photo)
async def process_category_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    file_id = message.photo[-1].file_id
    await repo.set_category_photo(session, data["category_id"], file_id)
    category = await repo.get_category(session, data["category_id"])
    await state.clear()
    await message.answer_photo(
        file_id, caption="✅ Фото сохранено.", reply_markup=admin_category_menu_kb(category)
    )


@router.message(CategoryStates.waiting_photo)
async def process_category_photo_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте фотографию (или нажмите «Отмена»).", reply_markup=cancel_kb())


@router.callback_query(F.data.startswith("adm:cat:photodel:"))
async def cb_category_photo_del(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    await repo.set_category_photo(session, category_id, None)
    category = await repo.get_category(session, category_id)
    await render(callback, "🗑 Фото удалено.", admin_category_menu_kb(category))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("adm:cat:delconfirm:"))
async def cb_delete_category_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    category = await repo.get_category(session, category_id)
    if category is None:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await render(
        callback,
        f"❗️ Удалить категорию «{category.name}»? Все товары в ней тоже будут удалены.\n\nВы уверены?",
        confirm_delete_kb(f"adm:cat:delete:{category_id}", f"adm:cat:open:{category_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:cat:delete:"))
async def cb_delete_category(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[3])
    await repo.delete_category(session, category_id)
    categories = await repo.list_categories(session)
    await render(callback, "🗑 Категория удалена.\n\n📦 Товары — категории", admin_categories_kb(categories))
    await callback.answer()
