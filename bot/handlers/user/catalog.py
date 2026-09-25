from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.database.models import OrderStatus
from bot.keyboards.user import (
    back_kb,
    catalog_kb,
    category_products_kb,
    confirm_purchase_kb,
    favorites_kb,
    product_view_kb,
    promo_cancel_kb,
    search_results_kb,
)
from bot.services.fulfillment import fulfill_order
from bot.states.states import PurchaseStates, SearchStates
from bot.utils.formatting import (
    esc,
    format_price,
    invoice_amount,
    normalize_price,
    stock_line,
    strip_html,
)
from bot.utils.ui import render

logger = logging.getLogger(__name__)
router = Router(name="user_catalog")


def _menu_cancel_kb():  # noqa: ANN202
    return back_kb("back:main", "❌ Отмена")


def _int_part(data: str, index: int, default: int = 0) -> int:
    parts = data.split(":")
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return default


@router.callback_query(F.data == "menu:catalog")
async def cb_catalog(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    categories = await repo.list_categories(session, only_active=True)
    catalog_photo = await repo.get_setting(session, "catalog_photo_file_id")
    if not categories:
        text = "🛍 <b>Каталог</b>\n─────────────────\nПока здесь пусто, загляните чуть позже."
    else:
        text = "🛍 <b>Каталог</b>\n─────────────────\nВыберите категорию, чтобы посмотреть товары 👇"
    await render(callback, text, catalog_kb(categories), photo=catalog_photo)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^cat_u:\d+(:\d+)?$"))
async def cb_open_category(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    category_id = _int_part(callback.data, 1)
    page = _int_part(callback.data, 2)
    category = await repo.get_category(session, category_id)
    if category is None or not category.is_active:
        await callback.answer("Категория недоступна", show_alert=True)
        return
    products = await repo.list_products(session, category_id, only_active=True)
    text = f"📁 <b>{esc(category.name)}</b>\n─────────────────"
    if category.description:
        text += f"\n{category.description}\n─────────────────"
    if not products:
        text += "\n\nТоваров пока нет, загляните позже 🙂"
    else:
        text += f"\n\n📦 Товаров в категории: {len(products)}\nВыберите товар 👇"
    await render(callback, text, category_products_kb(products, category_id, page), photo=category.photo_file_id)
    await callback.answer()


async def _show_product(callback: CallbackQuery, session: AsyncSession, product) -> None:  # noqa: ANN001
    available = await repo.count_available_items(session, product.id)
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    is_fav = await repo.is_favorite(session, db_user.id, product.id) if db_user else False
    await render(
        callback,
        _product_text(product, available),
        product_view_kb(product.id, available > 0, product.category_id, is_fav),
        photo=product.photo_file_id,
    )


@router.callback_query(F.data.regexp(r"^prod_u:\d+$"))
async def cb_open_product(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    product = await repo.get_product(session, _int_part(callback.data, 1))
    if not await repo.is_product_visible(session, product):
        await callback.answer("Товар недоступен", show_alert=True)
        return
    await _show_product(callback, session, product)
    await callback.answer()


def _discounted(price: Decimal, discount_percent: int) -> Decimal:
    return normalize_price(price * Decimal(100 - discount_percent) / Decimal(100))


def _product_text(product, available: int, discount_percent: int = 0) -> str:  # noqa: ANN001
    price = product.price
    text = f"📦 <b>{esc(product.name)}</b>\n─────────────────\n"
    if product.description:
        text += f"{product.description}\n─────────────────\n"
    if discount_percent:
        final = _discounted(price, discount_percent)
        text += f"💰 Цена: <s>{format_price(price)}</s> <b>{format_price(final)}</b> (-{discount_percent}%)\n"
    else:
        text += f"💰 Цена: <b>{format_price(price)}</b>\n"
    text += stock_line(available)
    return text


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@router.callback_query(F.data == "menu:search")
async def cb_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SearchStates.waiting_query)
    await render(
        callback,
        "🔍 <b>Поиск товаров</b>\n─────────────────\nВведите название товара (или часть названия) сообщением:",
        _menu_cancel_kb(),
    )
    await callback.answer()


@router.message(SearchStates.waiting_query, F.text)
async def process_search(message: Message, session: AsyncSession, state: FSMContext) -> None:
    query = message.text.strip()[:100]
    if not query or query.startswith("/"):
        await message.answer("Введите текст для поиска.", reply_markup=_menu_cancel_kb())
        return
    results = await repo.search_products(session, query)
    await state.clear()
    if not results:
        await message.answer(
            f"🔍 По запросу «{esc(query)}» ничего не найдено.\n\nПопробуйте другой запрос.",
            reply_markup=search_results_kb([]),
        )
        return
    await message.answer(
        f"🔍 По запросу «{esc(query)}» найдено товаров: {len(results)}",
        reply_markup=search_results_kb(results),
    )


@router.message(SearchStates.waiting_query)
async def process_search_invalid(message: Message) -> None:
    await message.answer("Отправьте поисковый запрос текстом.", reply_markup=_menu_cancel_kb())


# --------------------------------------------------------------------------- #
# Favorites
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^menu:favorites(:\d+)?$"))
async def cb_favorites(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    page = _int_part(callback.data, 2)
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    products = await repo.list_favorite_products(session, db_user.id) if db_user else []
    if not products:
        await render(
            callback,
            "⭐ <b>Избранное</b>\n─────────────────\nПока пусто — добавляйте товары кнопкой «В избранное» на странице товара.",
            favorites_kb([]),
        )
        await callback.answer()
        return
    await render(
        callback, f"⭐ <b>Избранное</b>\n─────────────────\nВсего: {len(products)}", favorites_kb(products, page)
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^fav:toggle:\d+$"))
async def cb_favorite_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    product = await repo.get_product(session, _int_part(callback.data, 2))
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    if product is None or db_user is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    if await repo.is_favorite(session, db_user.id, product.id):
        await repo.remove_favorite(session, db_user.id, product.id)
        await callback.answer("💔 Убрано из избранного")
    else:
        await repo.add_favorite(session, db_user.id, product.id)
        await callback.answer("⭐ Добавлено в избранное")
    await _show_product(callback, session, product)


# --------------------------------------------------------------------------- #
# Promo code entry
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^promo:enter:\d+$"))
async def cb_promo_enter(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = _int_part(callback.data, 2)
    await state.set_state(PurchaseStates.waiting_promo)
    await state.update_data(product_id=product_id)
    await render(
        callback,
        "🎟 <b>Промокод</b>\n─────────────────\nВведите промокод сообщением, или нажмите «Отмена».",
        promo_cancel_kb(product_id),
    )
    await callback.answer()


@router.message(PurchaseStates.waiting_promo)
async def process_promo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    product = await repo.get_product(session, product_id) if product_id else None
    if not await repo.is_product_visible(session, product):
        await state.clear()
        await message.answer("Товар больше недоступен.", reply_markup=_menu_cancel_kb())
        return

    code = (message.text or "").strip()
    if not code:
        await message.answer("Отправьте промокод текстом.", reply_markup=promo_cancel_kb(product.id))
        return

    promo = await repo.get_promocode_by_code(session, code)
    db_user = await repo.get_user_by_telegram_id(session, message.from_user.id)

    error = None
    if promo is None or not repo.promocode_is_valid(promo):
        error = "❌ Промокод недействителен или истёк."
    elif db_user is not None and await repo.user_used_promocode(session, db_user.id, promo.id):
        error = "❌ Вы уже использовали этот промокод."
    if error:
        await message.answer(
            f"{error}\n\nПопробуйте другой промокод или нажмите «Отмена».",
            reply_markup=promo_cancel_kb(product.id),
        )
        return

    available = await repo.count_available_items(session, product.id)
    # Leave the "waiting for promo" state but keep the applied code in data.
    await state.set_state(None)
    await state.update_data(promo_id=promo.id)
    await message.answer(
        f"✅ Промокод «{esc(promo.code)}» применён (-{promo.discount_percent}%).\n\n"
        + _product_text(product, available, promo.discount_percent),
        reply_markup=confirm_purchase_kb(product.id),
    )


# --------------------------------------------------------------------------- #
# Purchase / invoice
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.regexp(r"^buy:\d+$"))
async def cb_buy(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    settings = get_settings()
    product_id = _int_part(callback.data, 1)
    product = await repo.get_product(session, product_id)
    if not await repo.is_product_visible(session, product):
        await callback.answer("Товар недоступен", show_alert=True)
        return

    available = await repo.count_available_items(session, product_id)
    if available <= 0:
        await callback.answer("❌ Товар закончился.", show_alert=True)
        return

    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    if db_user is None:
        await callback.answer("Нажмите /start и попробуйте снова.", show_alert=True)
        return

    data = await state.get_data()
    await state.clear()
    promo_id = None
    final_price = normalize_price(product.price)
    if data.get("product_id") == product_id and data.get("promo_id"):
        promo = await repo.get_promocode(session, data["promo_id"])
        if (
            promo
            and repo.promocode_is_valid(promo)
            and not await repo.user_used_promocode(session, db_user.id, promo.id)
        ):
            promo_id = promo.id
            final_price = _discounted(product.price, promo.discount_percent)
    final_price = max(final_price, Decimal("0"))

    # Any older unpaid invoice for this product becomes unpayable.
    await repo.cancel_pending_orders(session, db_user.id, product.id)
    order = await repo.create_order(
        session,
        user_id=db_user.id,
        product_id=product.id,
        product_name=product.name,
        price=final_price,
        promo_code_id=promo_id,
        discount_amount=normalize_price(product.price) - final_price,
    )

    if final_price <= 0:
        # 100% promo code — nothing to pay, deliver right away.
        await callback.answer()
        await repo.mark_order_paid(session, order.id, None)
        await fulfill_order(
            callback.bot,
            session,
            order,
            chat_id=callback.message.chat.id,
            buyer_telegram_id=callback.from_user.id,
            buyer_username=callback.from_user.username,
            buyer_full_name=callback.from_user.full_name,
        )
        return

    title = product.name[:32] or "Товар"
    description = strip_html(product.description)[:255] or product.name[:255]
    try:
        await callback.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=title,
            description=description,
            payload=f"order:{order.id}",
            provider_token=settings.payment_provider_token,
            currency=settings.currency,
            prices=[LabeledPrice(label=title, amount=invoice_amount(final_price))],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send invoice for order %s", order.id)
        await repo.set_order_status(session, order.id, OrderStatus.failed)
        await callback.answer("⚠️ Не удалось создать счёт на оплату. Попробуйте позже.", show_alert=True)
        return

    await callback.answer("🧾 Счёт на оплату отправлен ниже 👇")
