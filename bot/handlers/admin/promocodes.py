from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import confirm_delete_kb, promocode_menu_kb, promocodes_list_kb
from bot.keyboards.user import cancel_kb, skip_cancel_kb
from bot.states.states import PromoCodeStates
from bot.utils.formatting import esc, format_dt
from bot.utils.ui import render

CODE_RE = re.compile(r"^[A-Z0-9_\-]{3,32}$")

router = Router(name="admin_promocodes")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _promo_text(promo) -> str:
    text = (
        f"🎟 <b>{esc(promo.code)}</b>\n\n"
        f"💸 Скидка: {promo.discount_percent}%\n"
        f"📊 Статус: {'🟢 Активен' if promo.is_active else '🔴 Выключен'}\n"
        f"🔢 Использован: {promo.used_count}"
    )
    text += f" / {promo.max_uses}" if promo.max_uses is not None else " раз"
    if promo.expires_at:
        text += f"\n⏳ Истекает: {format_dt(promo.expires_at)}"
    if promo.is_active and not repo.promocode_is_valid(promo):
        text += "\n⚠️ Сейчас не работает: истёк срок или исчерпан лимит"
    return text


@router.callback_query(F.data == "adm:promo:list")
async def cb_promo_list(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    promos = await repo.list_promocodes(session)
    text = "🎟 <b>Промокоды</b>" if promos else "🎟 Промокодов пока нет."
    await render(callback, text, promocodes_list_kb(promos))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:promo:open:\d+$"))
async def cb_promo_open(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    promo_id = int(callback.data.split(":")[3])
    promo = await repo.get_promocode(session, promo_id)
    if promo is None:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    await render(callback, _promo_text(promo), promocode_menu_kb(promo))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Add promo code (code -> discount -> max uses -> expiry)
# --------------------------------------------------------------------------- #


@router.callback_query(F.data == "adm:promo:add")
async def cb_promo_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoCodeStates.waiting_code)
    await render(
        callback, "🎟 Введите код промокода (3–32 символа: латиница, цифры, _ и -), например SALE10:", cancel_kb()
    )
    await callback.answer()


@router.message(PromoCodeStates.waiting_code)
async def process_promo_code(message: Message, session: AsyncSession, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not CODE_RE.match(code):
        await message.answer(
            "Код должен состоять из 3–32 символов: латинские буквы, цифры, _ и -.", reply_markup=cancel_kb()
        )
        return
    existing = await repo.get_promocode_by_code(session, code)
    if existing:
        await message.answer("Такой промокод уже существует. Введите другой:", reply_markup=cancel_kb())
        return
    await state.update_data(code=code)
    await state.set_state(PromoCodeStates.waiting_discount)
    await message.answer("Введите размер скидки в процентах (1-100):", reply_markup=cancel_kb())


@router.message(PromoCodeStates.waiting_discount)
async def process_promo_discount(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await message.answer("Введите целое число от 1 до 100.", reply_markup=cancel_kb())
        return
    await state.update_data(discount=int(text))
    await state.set_state(PromoCodeStates.waiting_max_uses)
    await message.answer(
        "Введите максимальное количество использований (число), или пропустите — без ограничения:",
        reply_markup=skip_cancel_kb("adm:promo:addskipuses"),
    )


@router.message(PromoCodeStates.waiting_max_uses)
async def process_promo_max_uses(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Введите положительное целое число.", reply_markup=skip_cancel_kb("adm:promo:addskipuses"))
        return
    await state.update_data(max_uses=int(text))
    await _ask_expiry(message, state)


@router.callback_query(F.data == "adm:promo:addskipuses", PromoCodeStates.waiting_max_uses)
async def cb_promo_skip_uses(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(max_uses=None)
    await state.set_state(PromoCodeStates.waiting_expires)
    await render(
        callback,
        "Введите срок действия в днях (число), или пропустите — бессрочно:",
        skip_cancel_kb("adm:promo:addskipexpiry"),
    )
    await callback.answer()


async def _ask_expiry(message: Message, state: FSMContext) -> None:
    await state.set_state(PromoCodeStates.waiting_expires)
    await message.answer(
        "Введите срок действия в днях (число), или пропустите — бессрочно:",
        reply_markup=skip_cancel_kb("adm:promo:addskipexpiry"),
    )


@router.message(PromoCodeStates.waiting_expires)
async def process_promo_expiry(message: Message, session: AsyncSession, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not (0 < int(text) <= 3650):
        await message.answer("Введите число дней от 1 до 3650.", reply_markup=skip_cancel_kb("adm:promo:addskipexpiry"))
        return
    expires_at = datetime.now(timezone.utc) + timedelta(days=int(text))
    await _finish_promo(message, session, state, expires_at)


@router.callback_query(F.data == "adm:promo:addskipexpiry", PromoCodeStates.waiting_expires)
async def cb_promo_skip_expiry(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _finish_promo(callback, session, state, None)
    await callback.answer()


async def _finish_promo(
    event: Message | CallbackQuery, session: AsyncSession, state: FSMContext, expires_at: datetime | None
) -> None:
    data = await state.get_data()
    if "code" not in data or "discount" not in data:
        await state.clear()
        await event.answer("Данные формы потерялись. Начните заново.")
        return
    if await repo.get_promocode_by_code(session, data["code"]):
        await state.clear()
        await event.answer("Такой промокод уже существует.")
        return
    promo = await repo.create_promocode(
        session,
        code=data["code"],
        discount_percent=data["discount"],
        expires_at=expires_at,
        max_uses=data.get("max_uses"),
    )
    await state.clear()
    text = f"✅ Промокод создан.\n\n{_promo_text(promo)}"
    if isinstance(event, Message):
        await event.answer(text, reply_markup=promocode_menu_kb(promo))
    else:
        await render(event, text, promocode_menu_kb(promo))


# --------------------------------------------------------------------------- #
# Toggle / delete
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^adm:promo:toggle:\d+$"))
async def cb_promo_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    promo_id = int(callback.data.split(":")[3])
    await repo.toggle_promocode(session, promo_id)
    promo = await repo.get_promocode(session, promo_id)
    if promo is None:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    await render(callback, _promo_text(promo), promocode_menu_kb(promo))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:promo:delconfirm:\d+$"))
async def cb_promo_delete_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    promo_id = int(callback.data.split(":")[3])
    promo = await repo.get_promocode(session, promo_id)
    if promo is None:
        await callback.answer("Промокод не найден", show_alert=True)
        return
    await render(
        callback,
        f"❗️ Удалить промокод «{esc(promo.code)}»?\n\nВы уверены?",
        confirm_delete_kb(f"adm:promo:delete:{promo_id}", f"adm:promo:open:{promo_id}"),
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:promo:delete:\d+$"))
async def cb_promo_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    promo_id = int(callback.data.split(":")[3])
    await repo.delete_promocode(session, promo_id)
    promos = await repo.list_promocodes(session)
    await render(callback, "🗑 Промокод удалён.", promocodes_list_kb(promos))
    await callback.answer()
