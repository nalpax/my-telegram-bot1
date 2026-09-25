from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.admin import admin_settings_kb, catalog_photo_kb, subscription_settings_kb, text_setting_kb
from bot.keyboards.user import cancel_kb, skip_cancel_kb
from bot.states.states import CatalogPhotoStates, SubscriptionSettingsStates, TextSettingStates
from bot.utils.formatting import esc
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
        f"📡 Канал: <code>{esc(channel_id)}</code>\n"
        f"🔗 Ссылка: {esc(channel_url)}"
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
    if channel.startswith("https://t.me/") and "/+" not in channel and "joinchat" not in channel:
        channel = "@" + channel.removeprefix("https://t.me/").strip("/")
    if not channel or not (channel.startswith("@") or channel.lstrip("-").isdigit()):
        await message.answer(
            "Укажите канал как @username или числовой ID вида -100123456789.", reply_markup=cancel_kb()
        )
        return
    # Make sure the bot can actually see members of this channel — otherwise
    # the gate would silently let everyone through.
    try:
        me = await message.bot.me()
        member = await message.bot.get_chat_member(chat_id=channel, user_id=me.id)
        if member.status not in ("administrator", "creator"):
            raise ValueError("bot is not an admin")
    except Exception:  # noqa: BLE001
        await message.answer(
            "⚠️ Бот не может проверить подписчиков этого канала. Добавьте бота в канал "
            "администратором и отправьте канал ещё раз.",
            reply_markup=cancel_kb(),
        )
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
    if not channel_id:
        await state.clear()
        await event.answer("Данные формы потерялись. Начните заново.")
        return
    if not url:
        url = f"https://t.me/{channel_id.lstrip('@')}" if channel_id.startswith("@") else ""
    await repo.set_setting(session, CHANNEL_ID_KEY, channel_id)
    if url:
        await repo.set_setting(session, CHANNEL_URL_KEY, url)
    else:
        await repo.delete_setting(session, CHANNEL_URL_KEY)
    await state.clear()
    text = "✅ Обязательная подписка настроена.\n\n" + await _subscription_text(session)
    if isinstance(event, Message):
        await event.answer(text, reply_markup=subscription_settings_kb(True))
    else:
        await render(event, text, subscription_settings_kb(True))


@router.message(SubscriptionSettingsStates.waiting_url)
async def process_subscription_url(message: Message, session: AsyncSession, state: FSMContext) -> None:
    url = (message.text or "").strip()
    if not url.startswith(("https://", "http://", "tg://")):
        await message.answer(
            "Ссылка должна начинаться с https:// (например https://t.me/+AbCdEf).",
            reply_markup=skip_cancel_kb("adm:settings:sub:skipurl"),
        )
        return
    await _finish_subscription_setup(message, session, state, url)


@router.callback_query(F.data == "adm:settings:sub:skipurl", SubscriptionSettingsStates.waiting_url)
async def cb_subscription_skip_url(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _finish_subscription_setup(callback, session, state, None)
    await callback.answer()


@router.callback_query(F.data == "adm:settings:sub:disable")
async def cb_subscription_disable(callback: CallbackQuery, session: AsyncSession) -> None:
    await repo.delete_setting(session, CHANNEL_ID_KEY)
    await repo.delete_setting(session, CHANNEL_URL_KEY)
    await render(callback, "🔴 Обязательная подписка отключена.\n\n" + await _subscription_text(session), subscription_settings_kb(False))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Simple text settings: support contact, welcome text
# --------------------------------------------------------------------------- #

TEXT_SETTINGS = {
    "support": {
        "key": "support_contact",
        "title": "💬 <b>Контакт поддержки</b>",
        "hint": "Показывается в разделе «Поддержка» и при проблемах с заказом.",
        "prompt": "Отправьте контакт поддержки, например @my_support или support@example.com:",
        "max_len": 200,
        "html": False,
    },
    "welcome": {
        "key": "welcome_text",
        "title": "👋 <b>Текст приветствия</b>",
        "hint": "Показывается в главном меню. Форматирование (жирный, курсив, ссылки) сохраняется.",
        "prompt": "Отправьте новый текст приветствия:",
        "max_len": 1500,
        "html": True,
    },
}


async def _text_setting_screen(session: AsyncSession, name: str) -> tuple[str, bool]:
    cfg = TEXT_SETTINGS[name]
    value = await repo.get_setting(session, cfg["key"])
    shown = (value if cfg["html"] else esc(value)) if value else "⚪ не задано (используется значение по умолчанию)"
    return f"{cfg['title']}\n─────────────────\n{cfg['hint']}\n\nСейчас:\n{shown}", bool(value)


@router.callback_query(F.data.regexp(r"^adm:settings:(support|welcome)$"))
async def cb_text_setting(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    name = callback.data.split(":")[2]
    text, is_set = await _text_setting_screen(session, name)
    await render(callback, text, text_setting_kb(name, is_set))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^adm:settings:(support|welcome):set$"))
async def cb_text_setting_set(callback: CallbackQuery, state: FSMContext) -> None:
    name = callback.data.split(":")[2]
    await state.set_state(TextSettingStates.waiting_value)
    await state.update_data(setting_name=name)
    await render(callback, TEXT_SETTINGS[name]["prompt"], cancel_kb())
    await callback.answer()


@router.message(TextSettingStates.waiting_value)
async def process_text_setting(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    name = data.get("setting_name")
    if name not in TEXT_SETTINGS:
        await state.clear()
        await message.answer("Данные формы потерялись. Начните заново.")
        return
    cfg = TEXT_SETTINGS[name]
    if not message.text:
        await message.answer("Отправьте значение текстом.", reply_markup=cancel_kb())
        return
    value = (message.html_text if cfg["html"] else message.text).strip()
    if not value or len(value) > cfg["max_len"]:
        await message.answer(f"Длина должна быть от 1 до {cfg['max_len']} символов.", reply_markup=cancel_kb())
        return
    await repo.set_setting(session, cfg["key"], value)
    await state.clear()
    text, is_set = await _text_setting_screen(session, name)
    await message.answer("✅ Сохранено.\n\n" + text, reply_markup=text_setting_kb(name, is_set))


@router.callback_query(F.data.regexp(r"^adm:settings:(support|welcome):del$"))
async def cb_text_setting_del(callback: CallbackQuery, session: AsyncSession) -> None:
    name = callback.data.split(":")[2]
    await repo.delete_setting(session, TEXT_SETTINGS[name]["key"])
    text, is_set = await _text_setting_screen(session, name)
    await render(callback, "🗑 Сброшено.\n\n" + text, text_setting_kb(name, is_set))
    await callback.answer()
