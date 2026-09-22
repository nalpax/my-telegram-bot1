from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.keyboards.user import main_menu_kb

router = Router(name="user_start")

WELCOME_TEXT = "🛍 <b>Добро пожаловать в магазин!</b>\n\nВыберите раздел:"


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    is_admin = message.from_user.id in settings.admin_id_list
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb(is_admin))


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


@router.callback_query(F.data == "back:main")
async def cb_back_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    is_admin = callback.from_user.id in settings.admin_id_list
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_kb(is_admin))
    await callback.answer()


@router.callback_query(F.data == "cancel:fsm")
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    settings = get_settings()
    is_admin = callback.from_user.id in settings.admin_id_list
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_kb(is_admin))
