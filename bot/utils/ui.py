"""
Telegram can't edit_text() a message that has a photo (and vice versa), and
callbacks can be stale (message already deleted, too old to edit, etc).
`render()` sidesteps all of that: it deletes the previous bot message and
sends a fresh one — simple and always correct, at the cost of one extra
delete call per screen.
"""
from __future__ import annotations

import logging

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def render(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    photo: str | None = None,
) -> None:
    chat_id = callback.message.chat.id
    bot = callback.bot
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001 - message may already be gone/too old
        logger.debug("Could not delete previous message", exc_info=True)

    try:
        if photo:
            await bot.send_photo(chat_id, photo=photo, caption=text, reply_markup=reply_markup)
        else:
            await bot.send_message(chat_id, text=text, reply_markup=reply_markup)
    except Exception:  # noqa: BLE001 - e.g. stale file_id
        logger.warning("Failed to render with photo, falling back to text", exc_info=True)
        await bot.send_message(chat_id, text=text, reply_markup=reply_markup)
