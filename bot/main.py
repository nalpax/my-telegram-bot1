from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import get_settings
from bot.database.engine import init_models
from bot.handlers import payments, subscription
from bot.handlers.admin import broadcast, categories, digital_items, panel, products, promocodes
from bot.handlers.admin import settings as admin_settings
from bot.handlers.admin import stats
from bot.handlers.user import catalog, orders, profile, start
from bot.middlewares import (
    DbSessionMiddleware,
    ErrorLoggingMiddleware,
    SubscriptionGateMiddleware,
    UserRegisterMiddleware,
)
from bot.utils.logger import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.admin_id_list:
        logger.warning("ADMIN_IDS is empty in .env — nobody will be able to open the admin panel.")

    logger.info("Ensuring database schema...")
    await init_models()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Outermost: never let one bad update crash the whole bot.
    dp.update.outer_middleware(ErrorLoggingMiddleware())
    # Inner: DB session must be available before user lookup, and both must
    # be available before any handler in any router runs — register at the
    # Update level so they apply to every event type (messages, callbacks,
    # pre_checkout_query, successful_payment, etc.)
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(UserRegisterMiddleware())
    # Must come after the two above (needs `session` and `event_from_user`
    # already in data) and before any user-facing router is reached.
    dp.update.middleware(SubscriptionGateMiddleware())

    # Payments must be registered so pre_checkout_query / successful_payment
    # are handled regardless of which router the invoice-sending code lives in.
    dp.include_router(payments.router)
    # The "Проверить подписку" button — exempted from the gate itself.
    dp.include_router(subscription.router)

    # Admin routers (each internally filtered by IsAdmin — safe to include globally)
    dp.include_router(panel.router)
    dp.include_router(categories.router)
    dp.include_router(products.router)
    dp.include_router(digital_items.router)
    dp.include_router(promocodes.router)
    dp.include_router(broadcast.router)
    dp.include_router(stats.router)
    dp.include_router(admin_settings.router)

    # User-facing routers
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(orders.router)
    dp.include_router(profile.router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot starting (polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Bot stopped.")
