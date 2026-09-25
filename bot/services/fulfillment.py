"""
Order fulfillment — the single place where a digital item is handed out.

Used both after a confirmed `successful_payment` and for orders that cost
nothing (100% promo code), so both paths behave identically: reserve one
unsold item atomically, snapshot it into the order, deliver it, notify the
admins. If stock ran out in the meantime, the order is marked failed and a
Telegram Stars payment is refunded automatically.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.database.models import Order, OrderStatus
from bot.keyboards.user import post_purchase_kb
from bot.utils.formatting import LOW_STOCK_THRESHOLD, esc, format_price, user_mention

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in get_settings().admin_id_list:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001 - admin may have never started the bot
            logger.warning("Failed to notify admin %s", admin_id, exc_info=True)


async def _support_hint(session: AsyncSession) -> str:
    contact = await repo.get_setting(session, "support_contact")
    return f"\n\n💬 Поддержка: {esc(contact)}" if contact else ""


async def fulfill_order(
    bot: Bot,
    session: AsyncSession,
    order: Order,
    chat_id: int,
    buyer_telegram_id: int,
    buyer_username: str | None,
    buyer_full_name: str | None,
) -> bool:
    """Deliver a paid (or free) order. Returns True if the buyer received an item."""
    settings = get_settings()
    buyer = user_mention(buyer_telegram_id, buyer_username, buyer_full_name)

    item = None
    if order.product_id:
        item = await repo.reserve_and_sell_item(session, order.product_id, order.id, order.user_id)

    if item is None:
        await repo.set_order_status(session, order.id, OrderStatus.failed)
        refunded = await _try_refund(bot, session, order, buyer_telegram_id)
        logger.error("Order %s paid but no digital item available (refunded=%s)", order.id, refunded)
        if refunded:
            buyer_text = (
                "⚠️ Товар закончился в последний момент.\n"
                "Оплата автоматически возвращена на ваш баланс ⭐. Приносим извинения!"
            )
            admin_text = f"↩️ Заказ #{order.id}: товар закончился, оплата {buyer} автоматически возвращена."
        elif order.price > 0:
            buyer_text = (
                "⚠️ Оплата прошла, но товар закончился в последний момент.\n"
                "Свяжитесь с поддержкой — вам оформят возврат." + await _support_hint(session)
            )
            admin_text = (
                f"⚠️ Заказ #{order.id} ({esc(order.product_name)}): оплата получена, "
                f"но позиций не осталось. Требуется ручной возврат пользователю {buyer} "
                f"(ID <code>{buyer_telegram_id}</code>)."
            )
        else:
            buyer_text = "⚠️ Товар закончился в последний момент. Попробуйте позже."
            admin_text = f"⚠️ Бесплатный заказ #{order.id}: позиций не осталось."
        await bot.send_message(chat_id, buyer_text)
        await notify_admins(bot, admin_text)
        return False

    await repo.complete_order(session, order.id, item)
    if order.promo_code_id:
        await repo.increment_promocode_usage(session, order.promo_code_id)
    logger.info("Order %s completed, digital item %s delivered", order.id, item.id)

    product = await repo.get_product(session, order.product_id) if order.product_id else None
    await bot.send_message(
        chat_id,
        f"🎉 <b>{'Оплата прошла успешно!' if order.price > 0 else 'Заказ оформлен!'}</b>\n"
        f"─────────────────\n"
        f"📦 Товар: {esc(order.product_name)}\n"
        f"💰 Сумма: {format_price(order.price)}\n"
        f"🧾 Заказ: #{order.id}\n"
        f"─────────────────\n"
        f"🔑 Ваш товар:\n<code>{esc(item.content)}</code>\n\n"
        f"Товар всегда доступен в разделе «🧾 Мои заказы».\n"
        f"Спасибо за покупку! 🙌",
        reply_markup=post_purchase_kb(product.category_id if product else 0),
    )

    admin_text = (
        f"💰 <b>Новая продажа</b> — заказ #{order.id}\n"
        f"📦 {esc(order.product_name)} — {format_price(order.price)}\n"
        f"👤 {buyer}"
    )
    if order.product_id:
        left = await repo.count_available_items(session, order.product_id)
        if left <= LOW_STOCK_THRESHOLD:
            admin_text += f"\n{'🔴' if left == 0 else '🟡'} Осталось позиций: {left}"
    if settings.notify_admins_on_sale:
        await notify_admins(bot, admin_text)
    return True


async def _try_refund(bot: Bot, session: AsyncSession, order: Order, buyer_telegram_id: int) -> bool:
    """Telegram Stars payments can be refunded by the bot itself; card payments can't."""
    fresh = await repo.get_order(session, order.id)
    charge_id = fresh.charge_id if fresh else None
    if not get_settings().uses_stars or not charge_id:
        return False
    try:
        await bot.refund_star_payment(user_id=buyer_telegram_id, telegram_payment_charge_id=charge_id)
    except Exception:  # noqa: BLE001
        logger.exception("Automatic Stars refund failed for order %s", order.id)
        return False
    await repo.set_order_status(session, order.id, OrderStatus.refunded)
    return True
