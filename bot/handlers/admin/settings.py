from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import admin_settings_kb, catalog_photo_kb
from bot.keyboards.user import cancel_kb
from bot.states.states import CatalogPhotoStates
from bot.utils.ui import render

router = Router(name="admin_settings")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SETTING_KEY = "catalog_photo_file_id"


@router.callback_query(F.data == "adm:settings")
async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, "⚙️ <b>Настройки</b>", admin_settings_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:settings:catphoto")
async def cb_catalog_photo_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    photo = await repo.get_setting(session, SETTING_KEY)
    text = "🖼 Фото каталога" if photo else "🖼 У каталога пока нет фото."
    await render(callback, text, catalog_photo_kb(bool(photo)), photo=photo)
    await callback.answer()


@router.callback_query(F.data == "adm:settings:catphotoset")
async def cb_catalog_photo_set(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CatalogPhotoStates.waiting_photo)
    await render(callback, "📷 Отправьте новую фотографию каталога.", cancel_kb())
    await callback.answer()


@router.message(CatalogPhotoStates.waiting_photo, F.photo)
async def process_catalog_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await repo.set_setting(session, SETTING_KEY, file_id)
    await state.clear()
    await message.answer_photo(file_id, caption="✅ Фото каталога сохранено.", reply_markup=admin_settings_kb())


@router.message(CatalogPhotoStates.waiting_photo)
async def process_catalog_photo_invalid(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте фотографию (или нажмите «Отмена»).", reply_markup=cancel_kb())


@router.callback_query(F.data == "adm:settings:catphotodel")
async def cb_catalog_photo_del(callback: CallbackQuery, session: AsyncSession) -> None:
    await repo.delete_setting(session, SETTING_KEY)
    await render(callback, "🗑 Фото удалено.", admin_settings_kb())
    await callback.answer()
