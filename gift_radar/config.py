"""Host-compatible settings. Existing process variables take priority over .env."""
import os
from pathlib import Path

from dotenv import load_dotenv


def initialize_environment():
    load_dotenv(Path.cwd() / ".env", override=False)


def bot_token():
    return os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")


def database_path():
    # Never open the shop.db used by the former application.
    return os.environ.get("GIFT_RADAR_DB") or str(Path(os.environ.get("DATA_DIR") or "data") / "radar.sqlite3")
