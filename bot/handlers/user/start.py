from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.keyboards.user import back_kb, main_menu_kb
from bot.utils.formatting import esc
from bot.utils.ui import render

router = Router(name="user_start")
router.message.filter(F.chat.type == "private")

DEFAULT_WELCOME = "🛍 <b>Добро пожаловать в магазин!</b>"
WELCOME_FOOTER = "\n─────────────────\nВыберите раздел в меню ниже 👇"
WELCOME_KEY = "welcome_text"
SUPPORT_KEY = "support_contact"

HELP_TEXT = (
    "ℹ️ <b>Как купить</b>\n"
    "─────────────────\n"
    "1. Откройте 🛍 <b>Каталог</b> и выберите товар\n"
    "2. Если есть промокод — нажмите 🎟 <b>Промокод</b>\n"
    "3. Нажмите 💳 <b>Купить</b> и оплатите счёт\n"
    "4. Товар придёт сразу после оплаты и останется в 🧾 <b>Мои заказы</b>\n"
    "─────────────────\n"
    "Команды:\n"
    "/start — главное меню\n"
    "/cancel — отменить текущее действие\n"
    "/help — эта справка"
)


async def welcome_text(session: AsyncSession) -> str:
    custom = await repo.get_setting(session, WELCOME_KEY)
    return (custom or DEFAULT_WELCOME) + WELCOME_FOOTER


def _is_admin(user_id: int) -> bool:
    return user_id in get_settings().admin_id_list


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    # Subscription gating (if enabled) already happened in SubscriptionGateMiddleware
    # before this handler runs — nothing to check here.
    await state.clear()
    await message.answer(await welcome_text(session), reply_markup=main_menu_kb(_is_admin(message.from_user.id)))


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await cmd_start(message, state, session)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    had_state = await state.get_state() is not None
    await state.clear()
    prefix = "❌ Действие отменено.\n\n" if had_state else ""
    await message.answer(
        prefix + await welcome_text(session), reply_markup=main_menu_kb(_is_admin(message.from_user.id))
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=back_kb())


@router.callback_query(F.data == "back:main")
async def cb_back_main(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await render(callback, await welcome_text(session), main_menu_kb(_is_admin(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "cancel:fsm")
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await render(callback, await welcome_text(session), main_menu_kb(_is_admin(callback.from_user.id)))


@router.callback_query(F.data == "menu:support")
async def cb_support(callback: CallbackQuery, session: AsyncSession) -> None:
    contact = await repo.get_setting(session, SUPPORT_KEY)
    if contact:
        text = (
            "💬 <b>Поддержка</b>\n"
            "─────────────────\n"
            f"По любым вопросам пишите: {esc(contact)}\n\n"
            "При обращении укажите номер заказа — так мы поможем быстрее."
        )
    else:
        text = "💬 <b>Поддержка</b>\n─────────────────\nКонтакты поддержки пока не указаны."
    await render(callback, text + "\n\n" + HELP_TEXT, back_kb())
    await callback.answer()
