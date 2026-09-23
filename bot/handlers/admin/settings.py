from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import admin_settings_kb, catalog_photo_kb, subscription_settings_kb
from bot.keyboards.user import cancel_kb, skip_cancel_kb
from bot.states.states import CatalogPhotoStates, SubscriptionSettingsStates
from bot.utils.ui import render

router = Router(name="admin_settings")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SETTING_KEY = "catalog_photo_file_id"
CHANNEL_ID_KEY = "required_channel_id"
CHANNEL_URL_KEY = "required_channel_url"


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


# --------------------------------------------------------------------------- #
# Mandatory channel subscription
# --------------------------------------------------------------------------- #


async def _subscription_text(session: AsyncSession) -> str:
    channel_id = await repo.get_setting(session, CHANNEL_ID_KEY)
    if not channel_id:
        return (
            "🔔 <b>Обязательная подписка</b>\n"
            "─────────────────\n"
            "⚪ Сейчас не настроена — пользователи могут пользоваться магазином без подписки.\n\n"
            "Чтобы включить, укажите канал (например @mychannel или -100123456789).\n"
            "⚠️ Бот должен быть добавлен в канал как администратор — иначе Telegram "
            "не даст проверять подписку."
        )
    channel_url = await repo.get_setting(session, CHANNEL_URL_KEY) or f"https://t.me/{channel_id.lstrip('@')}"
    return (
        "🔔 <b>Обязательная подписка</b>\n"
        "─────────────────\n"
        f"🟢 Включена\n"
        f"📡 Канал: <code>{channel_id}</code>\n"
        f"🔗 Ссылка: {channel_url}"
    )


@router.callback_query(F.data == "adm:settings:sub")
async def cb_subscription_menu(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    channel_id = await repo.get_setting(session, CHANNEL_ID_KEY)
    await render(callback, await _subscription_text(session), subscription_settings_kb(bool(channel_id)))
    await callback.answer()


@router.callback_query(F.data == "adm:settings:sub:setchannel")
async def cb_subscription_set_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionSettingsStates.waiting_channel)
    await render(
        callback,
        "✏️ Введите канал для проверки подписки — @username (для публичного) или "
        "числовой ID вида -100123456789 (для приватного).\n\n"
        "⚠️ Бот должен быть администратором этого канала.",
        cancel_kb(),
    )
    await callback.answer()


@router.message(SubscriptionSettingsStates.waiting_channel)
async def process_subscription_channel(message: Message, state: FSMContext) -> None:
    channel = (message.text or "").strip()
    if not channel:
        await message.answer("Канал не может быть пустым.", reply_markup=cancel_kb())
        return
    await state.update_data(channel_id=channel)
    default_url = f"https://t.me/{channel.lstrip('@')}" if channel.startswith("@") else ""
    await state.set_state(SubscriptionSettingsStates.waiting_url)
    hint = f"\n\nМожно пропустить — тогда подставится: {default_url}" if default_url else ""
    await message.answer(
        "🔗 Теперь введите ссылку для кнопки «Подписаться» (публичная ссылка или инвайт-ссылка для приватного канала)."
        + hint,
        reply_markup=skip_cancel_kb("adm:settings:sub:skipurl"),
    )


async def _finish_subscription_setup(
    event: Message | CallbackQuery, session: AsyncSession, state: FSMContext, url: str | None
) -> None:
    data = await state.get_data()
    channel_id = data.get("channel_id", "")
    if not url:
        url = f"https://t.me/{channel_id.lstrip('@')}" if channel_id.startswith("@") else ""
    await repo.set_setting(session, CHANNEL_ID_KEY, channel_id)
    if url:
        await repo.set_setting(session, CHANNEL_URL_KEY, url)
    await state.clear()
    text = "✅ Обязательная подписка настроена.\n\n" + await _subscription_text(session)
    if isinstance(event, Message):
        await event.answer(text, reply_markup=subscription_settings_kb(True))
    else:
        await render(event, text, subscription_settings_kb(True))


@router.message(SubscriptionSettingsStates.waiting_url)
async def process_subscription_url(message: Message, session: AsyncSession, state: FSMContext) -> None:
    url = (message.text or "").strip()
    if not url:
        await message.answer("Ссылка не может быть пустой.", reply_markup=skip_cancel_kb("adm:settings:sub:skipurl"))
        return
    await _finish_subscription_setup(message, session, state, url)


@router.callback_query(F.data == "adm:settings:sub:skipurl")
async def cb_subscription_skip_url(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _finish_subscription_setup(callback, session, state, None)
    await callback.answer()


@router.callback_query(F.data == "adm:settings:sub:disable")
async def cb_subscription_disable(callback: CallbackQuery, session: AsyncSession) -> None:
    await repo.delete_setting(session, CHANNEL_ID_KEY)
    await repo.delete_setting(session, CHANNEL_URL_KEY)
    await render(callback, "🔴 Обязательная подписка отключена.\n\n" + await _subscription_text(session), subscription_settings_kb(False))
    await callback.answer()
