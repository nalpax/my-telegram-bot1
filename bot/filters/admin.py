from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings


class IsAdmin(BaseFilter):
    """
    Server-side admin check. Applied to the whole admin router (both message
    and callback_query events) so a regular user can never reach an admin
    handler, even by crafting/guessing a callback manually.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        settings = get_settings()
        user = event.from_user
        if user is None:
            return False
        return user.id in settings.admin_id_list
