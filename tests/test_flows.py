"""End-to-end flows through the real Dispatcher with a fake Telegram API."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from itertools import count

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)

from bot.database import repo
from bot.database.engine import async_session_factory
from bot.database.models import OrderStatus

_ids = count(1)
BUYER = User(id=777, is_bot=False, first_name="Покупатель", username="buyer")
CHAT = Chat(id=777, type="private")


@pytest.fixture
def dp() -> Dispatcher:
    import bot.main as app  # noqa: F401 - ensures routers are importable
    from bot.handlers import fallback, payments, subscription
    from bot.handlers.admin import panel
    from bot.handlers.user import catalog, orders, profile, start
    from bot.middlewares import (
        DbSessionMiddleware,
        ErrorLoggingMiddleware,
        SubscriptionGateMiddleware,
        UserRegisterMiddleware,
    )

    d = Dispatcher(storage=MemoryStorage())
    d.update.outer_middleware(ErrorLoggingMiddleware())
    d.update.middleware(DbSessionMiddleware())
    d.update.middleware(UserRegisterMiddleware())
    d.update.middleware(SubscriptionGateMiddleware())
    for module in (payments, subscription, panel, start, catalog, orders, profile, fallback):
        # Routers can only be attached once; detach from any previous dispatcher.
        module.router._parent_router = None
        d.include_router(module.router)
    return d


def _message(text: str | None = None, **kwargs) -> Update:
    msg = Message(
        message_id=next(_ids), date=datetime.now(timezone.utc), chat=CHAT, from_user=BUYER, text=text, **kwargs
    )
    return Update(update_id=next(_ids), message=msg)


def _callback(data: str) -> Update:
    msg = Message(message_id=next(_ids), date=datetime.now(timezone.utc), chat=CHAT, text="screen")
    cq = CallbackQuery(id=str(next(_ids)), from_user=BUYER, chat_instance="x", message=msg, data=data)
    return Update(update_id=next(_ids), callback_query=cq)


async def _seed(items=("SECRET<KEY>&1",), price="10"):
    async with async_session_factory() as s:
        cat = await repo.create_category(s, "Игры <b>")
        product = await repo.create_product(s, cat.id, "Ключ & Co", "<i>desc</i>", Decimal(price))
        await repo.add_digital_items(s, product.id, list(items))
        return product.id


async def test_start_shows_menu(dp, bot, fake_session):
    await dp.feed_update(bot, _message("/start"))
    sent = fake_session.sent("SendMessage")
    assert sent and "Добро пожаловать" in sent[-1].text


async def test_full_purchase_with_stars(dp, bot, fake_session):
    product_id = await _seed()
    await dp.feed_update(bot, _message("/start"))
    await dp.feed_update(bot, _callback(f"prod_u:{product_id}"))
    await dp.feed_update(bot, _callback(f"buy:{product_id}"))

    invoice = fake_session.sent("SendInvoice")[-1]
    assert invoice.currency == "XTR" and invoice.prices[0].amount == 10
    order_id = int(invoice.payload.split(":")[1])

    pcq = PreCheckoutQuery(
        id="pc1", from_user=BUYER, currency="XTR", total_amount=10, invoice_payload=invoice.payload
    )
    await dp.feed_update(bot, Update(update_id=next(_ids), pre_checkout_query=pcq))
    assert fake_session.sent("AnswerPreCheckoutQuery")[-1].ok is True

    payment = SuccessfulPayment(
        currency="XTR",
        total_amount=10,
        invoice_payload=invoice.payload,
        telegram_payment_charge_id="charge-1",
        provider_payment_charge_id="",
    )
    await dp.feed_update(bot, _message(successful_payment=payment))

    delivered = fake_session.sent("SendMessage")
    buyer_msg = next(m for m in delivered if m.chat_id == BUYER.id and "Ваш товар" in (m.text or ""))
    # Content is HTML-escaped, so Telegram can parse the message.
    assert "SECRET&lt;KEY&gt;&amp;1" in buyer_msg.text
    admin_msg = [m for m in delivered if m.chat_id == 1]
    assert admin_msg and "Новая продажа" in admin_msg[-1].text

    async with async_session_factory() as s:
        order = await repo.get_order(s, order_id)
        assert order.status == OrderStatus.completed
        assert order.charge_id == "charge-1"

    # Duplicate delivery of the same payment must not hand out anything again.
    before = len(fake_session.sent("SendMessage"))
    await dp.feed_update(bot, _message(successful_payment=payment))
    assert len(fake_session.sent("SendMessage")) == before


async def test_sold_out_after_payment_refunds_stars(dp, bot, fake_session):
    product_id = await _seed(items=("ONLY",))
    await dp.feed_update(bot, _callback(f"buy:{product_id}"))
    invoice = fake_session.sent("SendInvoice")[-1]
    async with async_session_factory() as s:  # someone else buys the last item
        await repo.delete_available_items(s, product_id)
    payment = SuccessfulPayment(
        currency="XTR", total_amount=10, invoice_payload=invoice.payload,
        telegram_payment_charge_id="charge-2", provider_payment_charge_id="",
    )
    await dp.feed_update(bot, _message(successful_payment=payment))
    assert fake_session.sent("RefundStarPayment")
    order_id = int(invoice.payload.split(":")[1])
    async with async_session_factory() as s:
        assert (await repo.get_order(s, order_id)).status == OrderStatus.refunded


async def test_hundred_percent_promo_is_free(dp, bot, fake_session):
    product_id = await _seed()
    async with async_session_factory() as s:
        await repo.create_promocode(s, "FREE", 100)
    await dp.feed_update(bot, _callback(f"promo:enter:{product_id}"))
    await dp.feed_update(bot, _message("free"))
    await dp.feed_update(bot, _callback(f"buy:{product_id}"))
    assert not fake_session.sent("SendInvoice")
    assert any("Ваш товар" in (m.text or "") for m in fake_session.sent("SendMessage"))

    # The same buyer can't reuse the code.
    await dp.feed_update(bot, _callback(f"promo:enter:{product_id}"))
    await dp.feed_update(bot, _message("FREE"))
    assert "уже использовали" in fake_session.sent("SendMessage")[-1].text


async def test_subscription_gate_blocks_until_subscribed(dp, bot, fake_session):
    async with async_session_factory() as s:
        await repo.set_setting(s, "required_channel_id", "@chan")
    fake_session.subscribed = False
    await dp.feed_update(bot, _message("/start"))
    assert "Нужна подписка" in fake_session.sent("SendMessage")[-1].text

    await dp.feed_update(bot, _callback("subcheck:verify"))
    assert fake_session.sent("AnswerCallbackQuery")[-1].show_alert is True

    fake_session.subscribed = True
    await dp.feed_update(bot, _callback("subcheck:verify"))
    assert "Добро пожаловать" in fake_session.sent("SendMessage")[-1].text


async def test_banned_user_is_ignored(dp, bot, fake_session):
    async with async_session_factory() as s:
        user = await repo.get_or_create_user(s, BUYER.id, "buyer", "B")
        await repo.set_user_banned(s, user.id, True)
    await dp.feed_update(bot, _message("/start"))
    assert not fake_session.sent("SendMessage")


async def test_unknown_text_gets_menu(dp, bot, fake_session):
    await dp.feed_update(bot, _message("привет"))
    assert "Не понял" in fake_session.sent("SendMessage")[-1].text
