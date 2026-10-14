import argparse
import asyncio
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .adapters import read_authorized_export, registry
from .analytics import Rate
from .models import CURRENCIES, instant, money, utcnow
from .service import Radar
from .storage import Store
from .config import initialize_environment, bot_token, database_path
from .security import SecretLogFilter, private_file


def configure_logging(directory):
    if os.name == "posix":
        os.umask(0o077)
    Path(directory).mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(Path(directory) / "radar.log")
    for suffix in (".1", ".2", ".3"):
        rotated = Path(directory) / ("radar.log" + suffix)
        if rotated.exists():
            private_file(rotated)
    handler = RotatingFileHandler(Path(directory) / "radar.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.addFilter(SecretLogFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log = logging.getLogger("gift_radar")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate = False
    # These libraries may log request URLs containing bot tokens. Silence them.
    for name in ("httpx", "httpcore", "telegram"):
        lib = logging.getLogger(name)
        lib.handlers = [logging.NullHandler()]
        lib.propagate = False


def main(argv=None):
    if os.name == "posix":
        os.umask(0o077)
    try:
        initialize_environment()
    except Exception as exc:
        print(f"Настройки не загружены: {type(exc).__name__}. Проверьте доступ к .env и приватные права файла. Значения секретов не выводятся.")
        return 1
    parser = argparse.ArgumentParser(description="Gift Radar: аналитика без торговых операций")
    parser.add_argument("--db", default=database_path())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Проверка БД и конфигурации без обращения к рынкам")
    sub.add_parser("bot", help="Telegram long polling")
    sub.add_parser("telegram-check", help="Read-only getMe: проверить токен и доступ к Bot API")
    export = sub.add_parser("import", help="Импорт нормализованного разрешённого экспорта")
    export.add_argument("file")
    export.add_argument("--authorized", action="store_true")
    rate = sub.add_parser("rate", help="Сохранить реальный курс с источником и временем")
    rate.add_argument("base", choices=CURRENCIES)
    rate.add_argument("quote", choices=CURRENCIES)
    rate.add_argument("value")
    rate.add_argument("--source", required=True)
    rate.add_argument("--at", required=True, help="ISO 8601 с часовым поясом")
    args = parser.parse_args(argv)
    store = None
    try:
        store = Store(args.db)
        if args.command == "check":
            store.cleanup(utcnow(), int(os.environ.get("GIFT_RADAR_RETENTION_DAYS", "90")))
            print(json.dumps({"database": "ok", "schema_version": store.db.execute("PRAGMA user_version").fetchone()[0],
                "active_listings": len(store.listings()), "sales_30d": len(store.sales(utcnow())),
                "markets": {m: state for m, state, _, _ in store.market_status()},
                "telegram_token": "present (not validated)" if bot_token() else "missing"}, ensure_ascii=False, indent=2))
        elif args.command == "import":
            listings, sales = read_authorized_export(args.file, args.authorized)
            store.ingest(listings, sales)
            print(f"Импортировано записей: лоты {len(listings)}, завершённые продажи {len(sales)}. API площадок не подключены.")
        elif args.command == "rate":
            at = instant(args.at)
            if at > utcnow() or args.base == args.quote or not args.source.strip():
                raise ValueError("Invalid rate metadata")
            store.save_rate(Rate(args.base, args.quote, money(args.value, positive=True), at, args.source))
            print("Курс сохранён с источником и временем. Актуальность для показа — один час.")
        elif args.command in ("bot", "telegram-check"):
            token = bot_token()
            if not token:
                print("Не задан TELEGRAM_BOT_TOKEN или BOT_TOKEN. Добавьте токен в секреты среды; не отправляйте его в чат.")
                return 2
            seconds = int(os.environ.get("GIFT_RADAR_SCAN_SECONDS", "300"))
            days = int(os.environ.get("GIFT_RADAR_RETENTION_DAYS", "90"))
            if seconds < 30 or days < 30:
                raise ValueError("Scan interval >=30 seconds; retention >=30 days")
            configure_logging(os.environ.get("GIFT_RADAR_LOG_DIR", "logs"))
            from .telegram_ui import build_app
            app = build_app(token, store, Radar(store, registry(store)), seconds, days)
            if args.command == "telegram-check":
                async def check():
                    async with app:
                        # Initialization executes getMe; never print identity/token/URL.
                        print("Telegram Bot API: getMe успешно выполнен.")
                asyncio.run(check())
                return 0
            print("Запуск Telegram polling; доступ к площадкам смотрите в /markets. Для остановки Ctrl+C.")
            app.run_polling(timeout=30, bootstrap_retries=0, drop_pending_updates=False, allowed_updates=["message", "callback_query"])
    except Exception as exc:
        # Exception bodies can include credentials/URLs. Only category is safe to report.
        print(f"Операция не завершена: {type(exc).__name__}. Проверьте формат данных, настройки и доступ. Значения секретов не выводятся.")
        return 1
    finally:
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
