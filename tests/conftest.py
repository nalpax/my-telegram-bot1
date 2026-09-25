"""
Test setup: a throwaway SQLite database and a fake Telegram API.

Environment must be configured before any `bot.*` module is imported,
because settings and the DB engine are created at import time.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

_tmp = tempfile.mkdtemp(prefix="shopbot-tests-")
os.environ.update(
    BOT_TOKEN="42:TEST",
    ADMIN_IDS="1",
    DATABASE_URL="",
    DATA_DIR=_tmp,
    CURRENCY="XTR",
    PAYMENT_PROVIDER_TOKEN="",
    BROADCAST_DELAY_SECONDS="0",
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.methods import GetChatMember, TelegramMethod  # noqa: E402
from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, Message, User  # noqa: E402

from bot.database.engine import async_session_factory, engine, init_models  # noqa: E402
from sqlalchemy import text  # noqa: E402

RETURNS_MESSAGE = {"SendMessage", "SendPhoto", "SendInvoice", "CopyMessage"}


class FakeSession(BaseSession):
    """Records every API call and returns plausible results without network access."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []
        self.subscribed: bool = True
        self._message_id = 1000

    async def close(self) -> None:  # pragma: no cover
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name in RETURNS_MESSAGE:
            self._message_id += 1
            if name == "CopyMessage":
                from aiogram.types import MessageId

                return MessageId(message_id=self._message_id)
            return Message(
                message_id=self._message_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=getattr(method, "chat_id", 0), type="private"),
                text=getattr(method, "text", None),
            )
        if isinstance(method, GetChatMember):
            user = User(id=method.user_id, is_bot=False, first_name="X")
            return ChatMemberMember(user=user) if self.subscribed else ChatMemberLeft(user=user)
        return True

    def sent(self, method_name: str) -> list[TelegramMethod]:
        return [c for c in self.calls if type(c).__name__ == method_name]


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    await init_models()
    async with engine.begin() as conn:
        # digital_items <-> orders reference each other; break the cycle first.
        await conn.execute(text("UPDATE digital_items SET order_id = NULL"))
        await conn.execute(text("UPDATE orders SET digital_item_id = NULL"))
        for table in ("favorites", "digital_items", "orders", "promo_codes", "products", "categories", "users", "settings"):
            await conn.execute(text(f"DELETE FROM {table}"))
    yield


@pytest_asyncio.fixture
async def session():
    async with async_session_factory() as s:
        yield s


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def bot(fake_session: FakeSession) -> Bot:
    return Bot(token="42:TEST", session=fake_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
