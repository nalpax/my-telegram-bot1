from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from bot.config import get_settings
from bot.database.engine import init_models
from bot.handlers import fallback, payments, subscription
from bot.handlers.admin import broadcast, categories, digital_items, panel, prices, products, promocodes, users
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

USER_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="help", description="Как купить"),
    BotCommand(command="cancel", description="Отменить действие"),
]
ADMIN_COMMANDS = USER_COMMANDS + [BotCommand(command="admin", description="Админ-панель")]


async def setup_commands(bot: Bot, admin_ids: list[int]) -> None:
    try:
        await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
        for admin_id in admin_ids:
            try:
                await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception:  # noqa: BLE001 - admin hasn't started the bot yet
                logger.warning("Could not set admin commands for %s (has the admin started the bot?)", admin_id)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to set bot commands", exc_info=True)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.admin_id_list:
        logger.warning("ADMIN_IDS is empty in .env — nobody will be able to open the admin panel.")
    if not settings.uses_stars and not settings.payment_provider_token:
        logger.warning(
            "PAYMENT_PROVIDER_TOKEN is empty and CURRENCY=%s — payments are disabled, buyers "
            "will see 'оплата скоро будет доступна'. Set a provider token or use CURRENCY=XTR.",
            settings.currency,
        )

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
    dp.include_router(users.router)
    dp.include_router(prices.router)

    # User-facing routers
    dp.include_router(start.router)
    dp.include_router(catalog.router)
    dp.include_router(orders.router)
    dp.include_router(profile.router)

    # Must be last: answers anything no other router handled.
    dp.include_router(fallback.router)

    # Never drop pending updates: a successful_payment that arrived while the
    # bot was down must still be processed, or the buyer paid for nothing.
    await bot.delete_webhook(drop_pending_updates=False)
    await setup_commands(bot, settings.admin_id_list)
    logger.info("Bot starting (polling)...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Bot stopped.")
