from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Category, Order, Product


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🛍 Каталог", callback_data="menu:catalog")
    b.button(text="🔍 Поиск", callback_data="menu:search")
    b.button(text="⭐ Избранное", callback_data="menu:favorites")
    b.button(text="🧾 Мои заказы", callback_data="menu:orders")
    b.button(text="👤 Профиль", callback_data="menu:profile")
    if is_admin:
        b.button(text="🛠 Админ-панель", callback_data="adm:menu")
    b.adjust(1, 2, 2, 1)
    return b.as_markup()


def catalog_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in categories:
        b.button(text=f"📁 {c.name}", callback_data=f"cat_u:{c.id}")
    b.button(text="🔍 Поиск", callback_data="menu:search")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def category_products_kb(products: list[Product], category_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        mark = "" if p.is_active else "🔴 "
        b.button(text=f"📦 {mark}{p.name}", callback_data=f"prod_u:{p.id}")
    b.button(text="◀️ К категориям", callback_data="menu:catalog")
    b.adjust(1)
    return b.as_markup()


def product_view_kb(
    product_id: int, in_stock: bool, category_id: int, is_favorite: bool
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if in_stock:
        b.button(text="💳 Купить", callback_data=f"buy:{product_id}")
        b.button(text="🎟 Промокод", callback_data=f"promo:enter:{product_id}")
    fav_text = "💔 Убрать из избранного" if is_favorite else "⭐ В избранное"
    b.button(text=fav_text, callback_data=f"fav:toggle:{product_id}")
    b.button(text="◀️ К товарам", callback_data=f"cat_u:{category_id}")
    b.adjust(2, 1, 1)
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
        emoji = {"completed": "✅", "pending": "⏳", "failed": "⚠️", "cancelled": "❌", "paid": "✅"}.get(
            o.status, "•"
        )
        b.button(text=f"{emoji} Заказ #{o.id} — {o.product_name}", callback_data=f"order_u:{o.id}")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def search_results_kb(products: list[Product]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        b.button(text=f"📦 {p.name}", callback_data=f"prod_u:{p.id}")
    b.button(text="🔁 Искать снова", callback_data="menu:search")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def favorites_kb(products: list[Product]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        b.button(text=f"📦 {p.name}", callback_data=f"prod_u:{p.id}")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def post_purchase_kb(category_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🛍 В каталог", callback_data="menu:catalog")
    b.button(text="🧾 Мои заказы", callback_data="menu:orders")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(2, 1)
    return b.as_markup()


def subscription_gate_kb(channel_url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📢 Подписаться на канал", url=channel_url)
    b.button(text="✅ Проверить подписку", callback_data="subcheck:verify")
    b.adjust(1)
    return b.as_markup()


def back_kb(callback_data: str = "back:main", text: str = "◀️ В меню") -> InlineKeyboardMarkup:
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
