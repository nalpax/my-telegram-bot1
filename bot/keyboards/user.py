from __future__ import annotations

from typing import Sequence, TypeVar

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.database.models import Category, Order, Product
from bot.utils.formatting import STATUS_EMOJI, format_price

PAGE_SIZE = 8

T = TypeVar("T")


def paginate(items: Sequence[T], page: int, size: int = PAGE_SIZE) -> tuple[list[T], int, int]:
    """Returns (items on this page, clamped page index, total pages)."""
    pages = max(1, (len(items) + size - 1) // size)
    page = min(max(page, 0), pages - 1)
    return list(items[page * size : (page + 1) * size]), page, pages


def nav_row(page: int, pages: int, prefix: str) -> list[InlineKeyboardButton]:
    """◀️ 2/5 ▶️ row; callbacks are f"{prefix}:{page}". Empty when there's one page."""
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{page - 1}"))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        row.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{page + 1}"))
    return row


def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🛍 Каталог", callback_data="menu:catalog")
    b.button(text="🔍 Поиск", callback_data="menu:search")
    b.button(text="⭐ Избранное", callback_data="menu:favorites")
    b.button(text="🧾 Мои заказы", callback_data="menu:orders")
    b.button(text="👤 Профиль", callback_data="menu:profile")
    b.button(text="💬 Поддержка", callback_data="menu:support")
    if is_admin:
        b.button(text="🛠 Админ-панель", callback_data="adm:menu")
    b.adjust(1, 2, 2, 1, 1)
    return b.as_markup()


def catalog_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in categories:
        b.button(text=f"📁 {c.name}", callback_data=f"cat_u:{c.id}")
    b.button(text="🔍 Поиск", callback_data="menu:search")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def _product_button_text(p: Product) -> str:
    return f"📦 {p.name} — {format_price(p.price)}"


def category_products_kb(products: list[Product], category_id: int, page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    chunk, page, pages = paginate(products, page)
    for p in chunk:
        b.button(text=_product_button_text(p), callback_data=f"prod_u:{p.id}")
    b.adjust(1)
    nav = nav_row(page, pages, f"cat_u:{category_id}")
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="◀️ К категориям", callback_data="menu:catalog"))
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
    if in_stock:
        b.adjust(2, 1, 1)
    else:
        b.adjust(1)
    return b.as_markup()


def confirm_purchase_kb(product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💳 Оплатить", callback_data=f"buy:{product_id}")
    b.button(text="❌ Отмена", callback_data=f"prod_u:{product_id}")
    b.adjust(1)
    return b.as_markup()


def promo_cancel_kb(product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=f"prod_u:{product_id}")
    return b.as_markup()


def orders_list_kb(orders: list[Order], page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    chunk, page, pages = paginate(orders, page)
    for o in chunk:
        emoji = STATUS_EMOJI.get(o.status, "•")
        b.button(text=f"{emoji} #{o.id} — {o.product_name}", callback_data=f"order_u:{o.id}:{page}")
    b.adjust(1)
    nav = nav_row(page, pages, "menu:orders")
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="◀️ В меню", callback_data="back:main"))
    return b.as_markup()


def search_results_kb(products: list[Product]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        b.button(text=_product_button_text(p), callback_data=f"prod_u:{p.id}")
    b.button(text="🔁 Искать снова", callback_data="menu:search")
    b.button(text="◀️ В меню", callback_data="back:main")
    b.adjust(1)
    return b.as_markup()


def favorites_kb(products: list[Product], page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    chunk, page, pages = paginate(products, page)
    for p in chunk:
        b.button(text=_product_button_text(p), callback_data=f"prod_u:{p.id}")
    b.adjust(1)
    nav = nav_row(page, pages, "menu:favorites")
    if nav:
        b.row(*nav)
    b.row(InlineKeyboardButton(text="◀️ В меню", callback_data="back:main"))
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
