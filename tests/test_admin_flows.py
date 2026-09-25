"""Walk through every admin screen and make sure none of them errors out."""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import count

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.database import repo
from bot.database.engine import async_session_factory

_ids = count(10_000)
ADMIN = User(id=1, is_bot=False, first_name="Админ", username="admin")
CHAT = Chat(id=1, type="private")


@pytest.fixture
def dp() -> Dispatcher:
    from bot.handlers import fallback, payments, subscription
    from bot.handlers.admin import (
        broadcast, categories, digital_items, panel, products, promocodes, settings, stats, users,
    )
    from bot.handlers.user import catalog, orders, profile, start
    from bot.middlewares import (
        DbSessionMiddleware, ErrorLoggingMiddleware, SubscriptionGateMiddleware, UserRegisterMiddleware,
    )

    d = Dispatcher(storage=MemoryStorage())
    d.update.outer_middleware(ErrorLoggingMiddleware())
    d.update.middleware(DbSessionMiddleware())
    d.update.middleware(UserRegisterMiddleware())
    d.update.middleware(SubscriptionGateMiddleware())
    for module in (
        payments, subscription, panel, categories, products, digital_items, promocodes, broadcast, stats,
        settings, users, start, catalog, orders, profile, fallback,
    ):
        module.router._parent_router = None
        d.include_router(module.router)
    return d


def msg(text: str) -> Update:
    m = Message(message_id=next(_ids), date=datetime.now(timezone.utc), chat=CHAT, from_user=ADMIN, text=text)
    return Update(update_id=next(_ids), message=m)


def cb(data: str) -> Update:
    m = Message(message_id=next(_ids), date=datetime.now(timezone.utc), chat=CHAT, text="screen")
    q = CallbackQuery(id=str(next(_ids)), from_user=ADMIN, chat_instance="x", message=m, data=data)
    return Update(update_id=next(_ids), callback_query=q)


def assert_no_errors(fake_session):
    for call in fake_session.calls:
        text = getattr(call, "text", None) or ""
        assert "Что-то пошло не так" not in text, text
        assert "устарела" not in text, text


async def test_admin_walkthrough(dp, bot, fake_session, caplog):
    steps = [
        msg("/admin"),
        cb("adm:cat:list"), cb("adm:cat:add"), msg("Игры <&>"),
    ]
    for u in steps:
        await dp.feed_update(bot, u)
    async with async_session_factory() as s:
        cat = (await repo.list_categories(s))[0]

    steps = [
        cb(f"adm:cat:open:{cat.id}"), cb(f"adm:cat:descr:{cat.id}"), msg("Описание"),
        cb(f"adm:cat:rename:{cat.id}"), msg("Игры"), cb(f"adm:cat:toggle:{cat.id}"), cb(f"adm:cat:toggle:{cat.id}"),
        cb(f"adm:cat:up:{cat.id}"), cb(f"adm:prod:add:{cat.id}"), msg("Ключ <Steam>"), cb("adm:prod:addskipdesc"),
        msg("abc"), msg("15"), cb("adm:prod:addskipphoto"), cb("adm:prod:addfinish:now"), msg("K1\nK2\n\nK3"),
    ]
    for u in steps:
        await dp.feed_update(bot, u)
    async with async_session_factory() as s:
        product = (await repo.list_products(s, cat.id))[0]
        assert product.name == "Ключ <Steam>"
        assert await repo.count_available_items(s, product.id) == 3
        user = await repo.get_user_by_telegram_id(s, 1)

    pid = product.id
    steps = [
        cb(f"adm:items:{pid}"), cb(f"adm:prod:list:{cat.id}"), cb(f"adm:prod:open:{pid}"), cb(f"adm:prod:edit:{pid}"),
        cb(f"adm:prod:editprice:{pid}"), msg("20"), cb(f"adm:prod:editdesc:{pid}"), msg("новое <описание>"),
        cb(f"adm:prod:toggle:{pid}"), cb(f"adm:prod:toggle:{pid}"), cb(f"adm:prod:editcat:{pid}"),
        cb(f"adm:prod:setcat:{pid}:{cat.id}"), cb(f"adm:prod:photo:{pid}"),
        cb("adm:promo:list"), cb("adm:promo:add"), msg("sale10"), msg("10"), cb("adm:promo:addskipuses"), msg("7"),
        cb("adm:stats"), cb("adm:orders"), cb("adm:users"), msg("@admin"), cb(f"adm:user:{user.id}"),
        cb(f"adm:user:ban:{user.id}"),
        cb("adm:settings"), cb("adm:settings:support"), cb("adm:settings:support:set"), msg("@help_me"),
        cb("adm:settings:welcome"), cb("adm:settings:welcome:set"), msg("Привет!"), cb("adm:settings:sub"),
        cb("adm:broadcast:start"), msg("Всем привет"), cb("adm:broadcast:confirm"),
        cb("menu:catalog"), cb(f"cat_u:{cat.id}"), cb(f"prod_u:{pid}"), cb("menu:support"), cb("menu:profile"),
        cb("menu:orders"), cb("menu:favorites"), cb("menu:search"), msg("ключ"),
        cb(f"adm:items:delconfirm:{pid}"), cb(f"adm:items:delete:{pid}"),
        cb(f"adm:prod:delconfirm:{pid}"), cb(f"adm:prod:delete:{pid}"),
        cb(f"adm:cat:delconfirm:{cat.id}"), cb(f"adm:cat:delete:{cat.id}"),
    ]
    for u in steps:
        await dp.feed_update(bot, u)

    assert "Unhandled error" not in caplog.text, caplog.text
    assert_no_errors(fake_session)
    async with async_session_factory() as s:
        assert (await repo.get_setting(s, "support_contact")) == "@help_me"
        assert (await repo.get_promocode_by_code(s, "SALE10")) is not None
        assert not (await repo.get_user_by_telegram_id(s, 1)).is_banned  # admins can't be banned
    assert fake_session.sent("CopyMessage")  # broadcast went out
