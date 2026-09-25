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
from bot.services.fulfillment import fulfill_order
from bot.utils.formatting import invoice_amount

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
    db_user = await repo.get_user_by_telegram_id(session, pre_checkout_query.from_user.id)
    if order is None or order.status != OrderStatus.pending or db_user is None or order.user_id != db_user.id:
        await pre_checkout_query.answer(
            ok=False, error_message="Счёт устарел. Откройте товар и нажмите «Купить» ещё раз."
        )
        return

    settings = get_settings()
    if (
        pre_checkout_query.currency != settings.currency
        or pre_checkout_query.total_amount != invoice_amount(order.price)
    ):
        logger.warning("Pre-checkout amount/currency mismatch for order %s", order.id)
        await pre_checkout_query.answer(ok=False, error_message="Сумма счёта изменилась. Оформите заказ заново.")
        await repo.set_order_status(session, order.id, OrderStatus.cancelled)
        return

    product = await repo.get_product(session, order.product_id) if order.product_id else None
    if not await repo.is_product_visible(session, product):
        await pre_checkout_query.answer(ok=False, error_message="Товар больше недоступен.")
        await repo.set_order_status(session, order.id, OrderStatus.cancelled)
        return

    available = await repo.count_available_items(session, order.product_id)
    if available <= 0:
        await pre_checkout_query.answer(ok=False, error_message="Товар только что закончился.")
        await repo.set_order_status(session, order.id, OrderStatus.failed)
        return

    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, session: AsyncSession) -> None:
    payment = message.successful_payment
    order_id = _parse_order_id(payment.invoice_payload)

    if order_id is None:
        logger.error("Successful payment with unparsable payload: %s", payment.invoice_payload)
        return

    order = await repo.get_order(session, order_id)
    if order is None:
        logger.error("Successful payment for unknown order %s", order_id)
        return
    if order.status in (OrderStatus.paid, OrderStatus.completed, OrderStatus.refunded):
        # Telegram never double-delivers on its own, but be idempotent anyway.
        logger.warning("Duplicate successful_payment for order %s (status %s)", order.id, order.status)
        return

    await repo.mark_order_paid(session, order.id, payment.telegram_payment_charge_id)
    logger.info("Order %s paid by user %s", order.id, message.from_user.id)

    await fulfill_order(
        message.bot,
        session,
        order,
        chat_id=message.chat.id,
        buyer_telegram_id=message.from_user.id,
        buyer_username=message.from_user.username,
        buyer_full_name=message.from_user.full_name,
    )
