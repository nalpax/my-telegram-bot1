from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Category, Order, PromoCode, Product, User
from bot.keyboards.user import nav_row, paginate
from bot.utils.formatting import STATUS_EMOJI, format_price


def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📦 Товары", callback_data="adm:cat:list")
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="🧾 Заказы", callback_data="adm:orders")
    b.button(text="🎟 Промокоды", callback_data="adm:promo:list")
    b.button(text="📢 Рассылка", callback_data="adm:broadcast:start")
    b.button(text="👥 Пользователи", callback_data="adm:users")
    b.button(text="⚙️ Настройки", callback_data="adm:settings")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #


def admin_categories_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in categories:
        mark = "🟢 " if c.is_active else "🔴 "
        b.button(text=f"📁 {mark}{c.name}", callback_data=f"adm:cat:open:{c.id}")
    b.button(text="➕ Добавить категорию", callback_data="adm:cat:add")
    b.button(text="◀️ Назад", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()


def admin_category_menu_kb(category: Category) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📦 Товары категории", callback_data=f"adm:prod:list:{category.id}")
    b.button(text="➕ Добавить товар", callback_data=f"adm:prod:add:{category.id}")
    b.button(text="✏️ Название", callback_data=f"adm:cat:rename:{category.id}")
    b.button(text="📝 Описание", callback_data=f"adm:cat:descr:{category.id}")
    toggle_text = "🔴 Выключить" if category.is_active else "🟢 Включить"
    b.button(text=toggle_text, callback_data=f"adm:cat:toggle:{category.id}")
    b.button(text="🔼 Выше", callback_data=f"adm:cat:up:{category.id}")
    b.button(text="🔽 Ниже", callback_data=f"adm:cat:down:{category.id}")
    b.button(text="🖼 Фото категории", callback_data=f"adm:cat:photo:{category.id}")
    b.button(text="🗑 Удалить категорию", callback_data=f"adm:cat:delconfirm:{category.id}")
    b.button(text="◀️ Назад", callback_data="adm:cat:list")
    b.adjust(2, 2, 2, 2, 1, 1, 1)
    return b.as_markup()


def category_photo_kb(category_id: int, has_photo: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_photo:
        b.button(text="🔄 Заменить фото", callback_data=f"adm:cat:photoset:{category_id}")
        b.button(text="🗑 Удалить фото", callback_data=f"adm:cat:photodel:{category_id}")
    else:
        b.button(text="📷 Загрузить фото", callback_data=f"adm:cat:photoset:{category_id}")
    b.button(text="◀️ Назад", callback_data=f"adm:cat:open:{category_id}")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


def admin_products_kb(products: list[Product], category_id: int, page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    chunk, page, pages = paginate(products, page)
    for p in chunk:
        mark = "🟢 " if p.is_active else "🔴 "
        b.button(text=f"📦 {mark}{p.name} — {format_price(p.price)}", callback_data=f"adm:prod:open:{p.id}")
    b.adjust(1)
    nav = nav_row(page, pages, f"adm:prod:list:{category_id}")
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="➕ Добавить товар", callback_data=f"adm:prod:add:{category_id}"))
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"adm:cat:open:{category_id}"))
    return b.as_markup()


def admin_product_menu_kb(product: Product) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Изменить", callback_data=f"adm:prod:edit:{product.id}")
    b.button(text="🖼 Фото", callback_data=f"adm:prod:photo:{product.id}")
    b.button(text="📦 Позиции", callback_data=f"adm:items:{product.id}")
    b.button(text="🗑 Удалить", callback_data=f"adm:prod:delconfirm:{product.id}")
    b.button(text="◀️ Назад", callback_data=f"adm:prod:list:{product.category_id}")
    b.adjust(2, 2, 1)
    return b.as_markup()


def admin_product_edit_menu_kb(product: Product) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Название", callback_data=f"adm:prod:editname:{product.id}")
    b.button(text="📝 Описание", callback_data=f"adm:prod:editdesc:{product.id}")
    b.button(text="💰 Цена", callback_data=f"adm:prod:editprice:{product.id}")
    b.button(text="📁 Категория", callback_data=f"adm:prod:editcat:{product.id}")
    toggle_text = "🔴 Выключить" if product.is_active else "🟢 Включить"
    b.button(text=toggle_text, callback_data=f"adm:prod:toggle:{product.id}")
    b.button(text="◀️ Назад", callback_data=f"adm:prod:open:{product.id}")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def choose_category_kb(categories: list[Category], product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in categories:
        b.button(text=f"📁 {c.name}", callback_data=f"adm:prod:setcat:{product_id}:{c.id}")
    b.button(text="◀️ Назад", callback_data=f"adm:prod:edit:{product_id}")
    b.adjust(1)
    return b.as_markup()


def product_photo_kb(product_id: int, has_photo: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_photo:
        b.button(text="🔄 Заменить фото", callback_data=f"adm:prod:photoset:{product_id}")
        b.button(text="🗑 Удалить фото", callback_data=f"adm:prod:photodel:{product_id}")
    else:
        b.button(text="📷 Загрузить фото", callback_data=f"adm:prod:photoset:{product_id}")
    b.button(text="◀️ Назад", callback_data=f"adm:prod:open:{product_id}")
    b.adjust(1)
    return b.as_markup()


def add_product_skip_photo_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➡️ Пропустить фото", callback_data="adm:prod:addskipphoto")
    b.button(text="❌ Отмена", callback_data="cancel:fsm")
    b.adjust(1)
    return b.as_markup()


def add_product_finish_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📦 Добавить позиции сейчас", callback_data="adm:prod:addfinish:now")
    b.button(text="⏭ Добавить позже", callback_data="adm:prod:addfinish:later")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Digital items
# --------------------------------------------------------------------------- #


def items_input_done_kb(product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Готово", callback_data=f"adm:items:{product_id}")
    return b.as_markup()


def digital_items_kb(product_id: int, available: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить позиции", callback_data=f"adm:items:add:{product_id}")
    if available:
        b.button(text="🗑 Удалить позиции", callback_data=f"adm:items:delconfirm:{product_id}")
    b.button(text="◀️ Назад", callback_data=f"adm:prod:open:{product_id}")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Promo codes
# --------------------------------------------------------------------------- #


def promocodes_list_kb(promos: list[PromoCode]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in promos:
        mark = "🟢 " if p.is_active else "🔴 "
        b.button(text=f"🎟 {mark}{p.code} (-{p.discount_percent}%)", callback_data=f"adm:promo:open:{p.id}")
    b.button(text="➕ Добавить промокод", callback_data="adm:promo:add")
    b.button(text="◀️ Назад", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()


def promocode_menu_kb(promo: PromoCode) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle_text = "🔴 Выключить" if promo.is_active else "🟢 Включить"
    b.button(text=toggle_text, callback_data=f"adm:promo:toggle:{promo.id}")
    b.button(text="🗑 Удалить", callback_data=f"adm:promo:delconfirm:{promo.id}")
    b.button(text="◀️ Назад", callback_data="adm:promo:list")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def admin_settings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🖼 Фото каталога", callback_data="adm:settings:catphoto")
    b.button(text="🔔 Обязательная подписка", callback_data="adm:settings:sub")
    b.button(text="💬 Контакт поддержки", callback_data="adm:settings:support")
    b.button(text="👋 Текст приветствия", callback_data="adm:settings:welcome")
    b.button(text="◀️ Назад", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()


def subscription_settings_kb(is_configured: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Указать канал", callback_data="adm:settings:sub:setchannel")
    if is_configured:
        b.button(text="🔴 Отключить подписку", callback_data="adm:settings:sub:disable")
    b.button(text="◀️ Назад", callback_data="adm:settings")
    b.adjust(1)
    return b.as_markup()


def catalog_photo_kb(has_photo: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_photo:
        b.button(text="🔄 Заменить фото", callback_data="adm:settings:catphotoset")
        b.button(text="🗑 Удалить фото", callback_data="adm:settings:catphotodel")
    else:
        b.button(text="📷 Загрузить фото", callback_data="adm:settings:catphotoset")
    b.button(text="◀️ Назад", callback_data="adm:settings")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Broadcast
# --------------------------------------------------------------------------- #


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Отправить", callback_data="adm:broadcast:confirm")
    b.button(text="❌ Отмена", callback_data="adm:broadcast:cancel")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Generic confirm/delete
# --------------------------------------------------------------------------- #


def confirm_delete_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, удалить", callback_data=yes_cb)
    b.button(text="❌ Отмена", callback_data=no_cb)
    b.adjust(1)
    return b.as_markup()


def text_setting_kb(key: str, is_set: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Изменить", callback_data=f"adm:settings:{key}:set")
    if is_set:
        b.button(text="🗑 Сбросить", callback_data=f"adm:settings:{key}:del")
    b.button(text="◀️ Назад", callback_data="adm:settings")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #


ADMIN_ORDERS_PAGE = 10


def admin_orders_kb(orders: list[Order], page: int, has_next: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in orders:
        emoji = STATUS_EMOJI.get(o.status, "•")
        b.button(
            text=f"{emoji} #{o.id} — {o.product_name} — {format_price(o.price)}",
            callback_data=f"adm:order:{o.id}:{page}",
        )
    b.adjust(1)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:orders:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:orders:{page + 1}"))
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu"))
    return b.as_markup()


def admin_order_kb(order: Order, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if order.status == "failed":
        b.button(text="↩️ Отметить возврат", callback_data=f"adm:order:refunded:{order.id}:{page}")
    b.button(text="👤 Покупатель", callback_data=f"adm:user:{order.user_id}")
    b.button(text="◀️ К заказам", callback_data=f"adm:orders:{page}")
    b.adjust(1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


def admin_user_kb(user: User) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if user.is_banned:
        b.button(text="🟢 Разблокировать", callback_data=f"adm:user:unban:{user.id}")
    else:
        b.button(text="⛔️ Заблокировать", callback_data=f"adm:user:ban:{user.id}")
    b.button(text="🔍 Найти другого", callback_data="adm:users")
    b.button(text="◀️ В админ-панель", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()
