"""
Telegram Payments handlers.

Nothing is ever handed out just because a button was pressed: a digital
item is only assigned after Telegram confirms `successful_payment`, and
even then only if an unsold item can be atomically reserved.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database import repo
from bot.database.models import OrderStatus
from bot.keyboards.user import main_menu_kb
from bot.utils.formatting import format_price

logger = logging.getLogger(__name__)
router = Router(name="payments")


def _parse_order_id(payload: str) -> int | None:
    if not payload.startswith("order:"):
        return None
    try:
        return int(payload.split(":", 1)[1])
    except ValueError:
        return None


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery, session: AsyncSession) -> None:
    order_id = _parse_order_id(pre_checkout_query.invoice_payload)
    if order_id is None:
        await pre_checkout_query.answer(ok=False, error_message="Некорректный заказ.")
        return

    order = await repo.get_order(session, order_id)
    if order is None or order.status != OrderStatus.pending:
        await pre_checkout_query.answer(ok=False, error_message="Заказ уже недействителен.")
        return

    available = await repo.count_available_items(session, order.product_id) if order.product_id else 0
    if available <= 0:
        await pre_checkout_query.answer(ok=False, error_message="Товар только что закончился.")
        await repo.set_order_status(session, order.id, OrderStatus.failed)
        return

    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, session: AsyncSession) -> None:
    payment = message.successful_payment
    order_id = _parse_order_id(payment.invoice_payload)
    settings = get_settings()

    if order_id is None:
        logger.error("Successful payment with unparsable payload: %s", payment.invoice_payload)
        return

    order = await repo.get_order(session, order_id)
    if order is None:
        logger.error("Successful payment for unknown order %s", order_id)
        return

    await repo.set_order_status(session, order.id, OrderStatus.paid)
    logger.info("Order %s paid by user %s", order.id, message.from_user.id)

    db_user = await repo.get_user_by_telegram_id(session, message.from_user.id)

    item = None
    if order.product_id:
        item = await repo.reserve_and_sell_item(session, order.product_id, order.id, db_user.id)

    if item is None:
        # Race condition: item sold out between invoice and payment confirmation.
        await repo.set_order_status(session, order.id, OrderStatus.failed)
        logger.error("Order %s paid but no digital item available — needs manual refund", order.id)
        await message.answer(
            "⚠️ Оплата прошла, но товар закончился в последний момент.\n"
            "Свяжитесь с поддержкой — вам оформят возврат.",
        )
        for admin_id in settings.admin_id_list:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"⚠️ Заказ #{order.id}: оплата получена, но цифровых позиций не осталось. "
                    f"Требуется ручной возврат пользователю {message.from_user.id}.",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to notify admin %s about failed fulfillment", admin_id)
        return

    await repo.attach_digital_item(session, order.id, item.id)
    await repo.set_order_status(session, order.id, OrderStatus.completed)

    if order.promo_code_id:
        await repo.increment_promocode_usage(session, order.promo_code_id)

    logger.info("Order %s completed, digital item %s delivered", order.id, item.id)

    is_admin = message.from_user.id in settings.admin_id_list
    await message.answer(
        f"✅ <b>Оплата успешна!</b>\n\n"
        f"📦 Товар: {order.product_name}\n"
        f"💰 Сумма: {format_price(order.price)}\n\n"
        f"🔑 Ваш товар:\n<code>{item.content}</code>",
        reply_markup=main_menu_kb(is_admin),
    )
