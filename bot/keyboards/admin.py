from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Category, PromoCode, Product


def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📦 Товары", callback_data="adm:cat:list")
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="🧾 Заказы", callback_data="adm:orders")
    b.button(text="🎟 Промокоды", callback_data="adm:promo:list")
    b.button(text="📢 Рассылка", callback_data="adm:broadcast:start")
    b.button(text="⚙️ Настройки", callback_data="adm:settings")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #


def admin_categories_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in categories:
        mark = "" if c.is_active else "⛔️ "
        b.button(text=f"📁 {mark}{c.name}", callback_data=f"adm:cat:open:{c.id}")
    b.button(text="➕ Добавить категорию", callback_data="adm:cat:add")
    b.button(text="◀️ Назад", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()


def admin_category_menu_kb(category: Category) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
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
    b.adjust(1, 2, 2, 1, 1, 1)
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


def admin_products_kb(products: list[Product], category_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        mark = "" if p.is_active else "⛔️ "
        b.button(text=f"📦 {mark}{p.name}", callback_data=f"adm:prod:open:{p.id}")
    b.button(text="➕ Добавить товар", callback_data=f"adm:prod:add:{category_id}")
    b.button(text="◀️ Назад", callback_data=f"adm:cat:open:{category_id}")
    b.adjust(1)
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
        mark = "" if p.is_active else "⛔️ "
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
    b.button(text="◀️ Назад", callback_data="adm:menu")
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
