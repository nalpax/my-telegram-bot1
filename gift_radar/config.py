"""Host-compatible settings. Existing process variables take priority over .env."""
import os
from pathlib import Path

from dotenv import load_dotenv
from .security import private_file


def initialize_environment():
    path = Path.cwd() / ".env"
    if path.is_file() and os.name == "posix" and path.stat().st_mode & 0o077:
        private_file(path)
    load_dotenv(path, override=False)


def bot_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")


def database_path():
    # Never open the shop.db used by the former application.
    return os.environ.get("GIFT_RADAR_DB") or str(Path(os.environ.get("DATA_DIR") or "data") / "radar.sqlite3")
