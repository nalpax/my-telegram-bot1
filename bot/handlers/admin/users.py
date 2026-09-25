from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import admin_user_kb
from bot.keyboards.user import cancel_kb
from bot.states.states import UserSearchStates
from bot.utils.formatting import format_dt, format_price, user_mention
from bot.utils.ui import render

router = Router(name="admin_users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _user_card(session: AsyncSession, user) -> str:  # noqa: ANN001
    total, completed, spent = await repo.user_order_stats(session, user.id)
    return (
        f"👤 <b>Пользователь</b>\n"
        f"─────────────────\n"
        f"{user_mention(user.telegram_id, user.username, user.full_name)}\n"
        f"🆔 ID: <code>{user.telegram_id}</code>\n"
        f"📅 Регистрация: {format_dt(user.created_at)}\n"
        f"📊 Статус: {'⛔️ Заблокирован' if user.is_banned else '🟢 Активен'}\n"
        f"─────────────────\n"
        f"🧾 Заказов: {total}\n"
        f"✅ Покупок: {completed}\n"
        f"💰 Потрачено: {format_price(spent)}"
    )


@router.callback_query(F.data == "adm:users")
async def cb_users(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.set_state(UserSearchStates.waiting_query)
    users = await repo.count_users(session)
    banned = await repo.count_banned_users(session)
    await render(
        callback,
        f"👥 <b>Пользователи</b>\n─────────────────\n"
        f"Всего: {users}, заблокировано: {banned}\n\n"
        "Отправьте Telegram ID или @username пользователя, чтобы открыть его карточку.",
        cancel_kb(),
    )
    await callback.answer()


@router.message(UserSearchStates.waiting_query)
async def process_user_search(message: Message, session: AsyncSession, state: FSMContext) -> None:
    user = await repo.find_user(session, message.text or "")
    if user is None:
        await message.answer(
            "Пользователь не найден. Он должен хотя бы раз написать боту. Попробуйте ещё раз:",
            reply_markup=cancel_kb(),
        )
        return
    await state.clear()
    await message.answer(await _user_card(session, user), reply_markup=admin_user_kb(user))


@router.callback_query(F.data.regexp(r"^adm:user:\d+$"))
async def cb_user_open(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_user(session, int(callback.data.split(":")[2]))
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await render(callback, await _user_card(session, user), admin_user_kb(user))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:user:(ban|unban):\d+$"))
async def cb_user_ban(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, action, user_id = callback.data.split(":")
    user = await repo.get_user(session, int(user_id))
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    if action == "ban" and user.telegram_id in get_settings().admin_id_list:
        await callback.answer("Нельзя заблокировать администратора.", show_alert=True)
        return
    await repo.set_user_banned(session, user.id, action == "ban")
    await session.refresh(user)
    await render(callback, await _user_card(session, user), admin_user_kb(user))
    await callback.answer("⛔️ Заблокирован" if action == "ban" else "🟢 Разблокирован")
