from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.keyboards.user import (
    catalog_kb,
    category_products_kb,
    confirm_purchase_kb,
    favorites_kb,
    main_menu_kb,
    product_view_kb,
    search_results_kb,
)
from bot.states.states import PurchaseStates, SearchStates
from bot.utils.formatting import format_price, stock_line
from bot.utils.ui import render

logger = logging.getLogger(__name__)
router = Router(name="user_catalog")


@router.callback_query(F.data == "menu:catalog")
async def cb_catalog(callback: CallbackQuery, session: AsyncSession) -> None:
    categories = await repo.list_categories(session, only_active=True)
    catalog_photo = await repo.get_setting(session, "catalog_photo_file_id")
    if not categories:
        await render(callback, "🛍 <b>Каталог</b>\n\nПока здесь пусто, загляните чуть позже.", catalog_kb(categories), photo=catalog_photo)
        await callback.answer()
        return
    text = (
        "🛍 <b>Каталог</b>\n"
        "─────────────────\n"
        "Выберите категорию, чтобы посмотреть товары 👇"
    )
    await render(callback, text, catalog_kb(categories), photo=catalog_photo)
    await callback.answer()


@router.callback_query(F.data.startswith("cat_u:"))
async def cb_open_category(callback: CallbackQuery, session: AsyncSession) -> None:
    category_id = int(callback.data.split(":")[1])
    category = await repo.get_category(session, category_id)
    if category is None or not category.is_active:
        await callback.answer("Категория недоступна", show_alert=True)
        return
    products = await repo.list_products(session, category_id, only_active=True)
    text = f"📁 <b>{category.name}</b>\n─────────────────"
    if category.description:
        text += f"\n{category.description}\n─────────────────"
    if not products:
        text += "\n\nТоваров пока нет, загляните позже 🙂"
    else:
        text += f"\n\n📦 Товаров в категории: {len(products)}\nВыберите товар 👇"
    await render(callback, text, category_products_kb(products, category_id), photo=category.photo_file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("prod_u:"))
async def cb_open_product(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    product_id = int(callback.data.split(":")[1])
    product = await repo.get_product(session, product_id)
    if product is None or not product.is_active:
        await callback.answer("Товар недоступен", show_alert=True)
        return
    available = await repo.count_available_items(session, product_id)
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    is_fav = await repo.is_favorite(session, db_user.id, product_id) if db_user else False
    text = _product_text(product, available)
    await render(
        callback,
        text,
        product_view_kb(product.id, available > 0, product.category_id, is_fav),
        photo=product.photo_file_id,
    )
    await callback.answer()


def _product_text(product, available: int, discount_percent: int = 0) -> str:
    price = product.price
    text = f"📦 <b>{product.name}</b>\n─────────────────\n"
    if product.description:
        text += f"{product.description}\n─────────────────\n"
    if discount_percent:
        final = (price * (Decimal(100 - discount_percent) / Decimal(100))).quantize(Decimal("0.01"))
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
    )
    await callback.answer()


@router.message(SearchStates.waiting_query)
async def process_search(message: Message, session: AsyncSession, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer("Введите текст для поиска.")
        return
    results = await repo.search_products(session, query)
    await state.clear()
    if not results:
        await message.answer(
            f"🔍 По запросу «{query}» ничего не найдено.\n\nПопробуйте другой запрос.",
            reply_markup=search_results_kb([]),
        )
        return
    await message.answer(
        f"🔍 Найдено товаров: {len(results)}",
        reply_markup=search_results_kb(results),
    )


# --------------------------------------------------------------------------- #
# Favorites
# --------------------------------------------------------------------------- #


@router.callback_query(F.data == "menu:favorites")
async def cb_favorites(callback: CallbackQuery, session: AsyncSession) -> None:
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
    await render(callback, f"⭐ <b>Избранное</b>\n─────────────────\nВсего: {len(products)}", favorites_kb(products))
    await callback.answer()


@router.callback_query(F.data.startswith("fav:toggle:"))
async def cb_favorite_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    product_id = int(callback.data.split(":")[2])
    product = await repo.get_product(session, product_id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    currently_fav = await repo.is_favorite(session, db_user.id, product_id)
    if currently_fav:
        await repo.remove_favorite(session, db_user.id, product_id)
        await callback.answer("💔 Убрано из избранного")
    else:
        await repo.add_favorite(session, db_user.id, product_id)
        await callback.answer("⭐ Добавлено в избранное")

    available = await repo.count_available_items(session, product_id)
    text = _product_text(product, available)
    await render(
        callback,
        text,
        product_view_kb(product.id, available > 0, product.category_id, not currently_fav),
        photo=product.photo_file_id,
    )


# --------------------------------------------------------------------------- #
# Promo code entry
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("promo:enter:"))
async def cb_promo_enter(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[2])
    await state.set_state(PurchaseStates.waiting_promo)
    await state.update_data(product_id=product_id)
    await render(
        callback,
        "🎟 <b>Промокод</b>\n─────────────────\nВведите промокод сообщением, или нажмите «Отмена».",
    )
    await callback.answer()


@router.message(PurchaseStates.waiting_promo)
async def process_promo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    product = await repo.get_product(session, product_id) if product_id else None
    if product is None:
        await state.clear()
        await message.answer("Товар не найден.")
        return

    promo = await repo.get_promocode_by_code(session, message.text or "")
    available = await repo.count_available_items(session, product_id)
    db_user = await repo.get_user_by_telegram_id(session, message.from_user.id)
    is_fav = await repo.is_favorite(session, db_user.id, product_id) if db_user else False

    if promo is None or not repo.promocode_is_valid(promo):
        await message.answer(
            "❌ Промокод недействителен.\n\n" + _product_text(product, available),
            reply_markup=product_view_kb(product.id, available > 0, product.category_id, is_fav),
        )
        await state.clear()
        return

    await state.update_data(promo_id=promo.id, promo_code=promo.code)
    await message.answer(
        f"✅ Промокод «{promo.code}» применён (-{promo.discount_percent}%).\n\n"
        + _product_text(product, available, promo.discount_percent),
        reply_markup=confirm_purchase_kb(product.id),
    )


# --------------------------------------------------------------------------- #
# Purchase / invoice
# --------------------------------------------------------------------------- #


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    settings = get_settings()
    product_id = int(callback.data.split(":")[1])
    product = await repo.get_product(session, product_id)
    if product is None or not product.is_active:
        await callback.answer("Товар недоступен", show_alert=True)
        return

    available = await repo.count_available_items(session, product_id)
    if available <= 0:
        await callback.answer("❌ Товар закончился.", show_alert=True)
        return

    data = await state.get_data()
    promo_id = None
    discount_amount = Decimal("0")
    final_price = product.price
    if data.get("product_id") == product_id and data.get("promo_id"):
        promo = await repo.get_promocode(session, data["promo_id"])
        if promo and repo.promocode_is_valid(promo):
            promo_id = promo.id
            discount_amount = (product.price * Decimal(promo.discount_percent) / Decimal(100)).quantize(
                Decimal("0.01")
            )
            final_price = product.price - discount_amount

    db_user = await repo.get_user_by_telegram_id(session, callback.from_user.id)
    order = await repo.create_order(
        session,
        user_id=db_user.id,
        product_id=product.id,
        product_name=product.name,
        price=final_price,
        promo_code_id=promo_id,
        discount_amount=Decimal("0"),  # final_price already discounted; kept for clarity in records
    )
    await state.clear()

    amount = int(final_price) if settings.uses_stars else int((final_price * 100).to_integral_value())
    try:
        await callback.bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=product.name[:32] or "Товар",
            description=(product.description or product.name)[:255],
            payload=f"order:{order.id}",
            provider_token=settings.payment_provider_token,
            currency=settings.currency,
            prices=[LabeledPrice(label=product.name[:32] or "Товар", amount=amount)],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send invoice for order %s", order.id)
        await repo.set_order_status(session, order.id, repo.OrderStatus.failed)
        await callback.answer("⚠️ Не удалось создать счёт на оплату. Попробуйте позже.", show_alert=True)
        return

    await callback.answer()
