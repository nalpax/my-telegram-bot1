from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Category, Order, Product


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📦 Каталог", callback_data="menu:catalog")
    b.button(text="🧾 Мои заказы", callback_data="menu:orders")
    b.button(text="👤 Профиль", callback_data="menu:profile")
    if is_admin:
        b.button(text="🛠 Админ-панель", callback_data="adm:menu")
    b.adjust(1)
    return b.as_markup()


def catalog_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in categories:
        b.button(text=f"📁 {c.name}", callback_data=f"cat_u:{c.id}")
    b.button(text="◀️ Назад", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def category_products_kb(products: list[Product]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        mark = "" if p.is_active else "⛔️ "
        b.button(text=f"📦 {mark}{p.name}", callback_data=f"prod_u:{p.id}")
    b.button(text="◀️ Назад", callback_data="menu:catalog")
    b.adjust(1)
    return b.as_markup()


def product_view_kb(product_id: int, in_stock: bool, category_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if in_stock:
        b.button(text="💳 Купить", callback_data=f"buy:{product_id}")
        b.button(text="🎟 Промокод", callback_data=f"promo:enter:{product_id}")
    b.button(text="◀️ Назад", callback_data=f"cat_u:{category_id}")
    b.adjust(2, 1)
    return b.as_markup()


def confirm_purchase_kb(product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Оплатить", callback_data=f"buy:{product_id}")
    b.button(text="❌ Отмена", callback_data=f"prod_u:{product_id}")
    b.adjust(1)
    return b.as_markup()


def orders_list_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in orders:
        b.button(text=f"🧾 Заказ #{o.id} — {o.status}", callback_data=f"order_u:{o.id}")
    b.button(text="◀️ Назад", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def back_kb(callback_data: str = "back:main", text: str = "◀️ Назад") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=callback_data)
    return b.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="cancel:fsm")
    return b.as_markup()


def skip_cancel_kb(skip_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➡️ Пропустить", callback_data=skip_cb)
    b.button(text="❌ Отмена", callback_data="cancel:fsm")
    b.adjust(1)
    return b.as_markup()
