"""Deny-by-default access policy and local data protection."""
import logging
import os
import re
from pathlib import Path


_saved_secrets = set()


def register_secret(value):
    """Keep known credentials out of settings and logs, including after restart."""
    if value and len(value) >= 8:
        _saved_secrets.add(value)


def admin_ids() -> frozenset[int]:
    raw = os.environ.get("GIFT_RADAR_ADMIN_IDS") or os.environ.get("ADMIN_IDS", "")
    if not raw.strip():
        return frozenset()
    parts = [x.strip() for x in raw.split(",")]
    if any(not re.fullmatch(r"[1-9][0-9]{0,18}", x) for x in parts):
        return frozenset()
    return frozenset(int(x) for x in parts)


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


def private_admin(update) -> bool:
    user, chat = update.effective_user, update.effective_chat
    return bool(user and not user.is_bot and is_admin(user.id) and chat and chat.type == "private" and chat.id == user.id)


def contains_secret(text, extra_names=()) -> bool:
    if any(value in text for value in _saved_secrets):
        return True
    names = {"BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "GIFT_RADAR_ENCRYPTION_KEY", *filter(None, extra_names)}
    names.update(k for k in os.environ if k.startswith(("PORTALS_", "TONNEL_", "MRKT_")) and
                 any(word in k for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "SESSION", "HASH")))
    for name in names:
        value = os.environ.get(name)
        if value and len(value) >= 8 and value in text:
            return True
    return bool(re.search(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{25,}\b", text))


def private_file(path):
    """On POSIX, create/harden application data files to owner read/write only."""
    path = Path(path)
    if os.name != "posix":
        return
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


async def erase_input(update):
    """Best effort removal; never log the original message or an API error body."""
    if private_admin(update) and update.effective_message:
        try:
            await update.effective_message.delete()
        except Exception:
            pass


class SecretLogFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            message = "Malformed log record suppressed"
        for value in sorted(_saved_secrets, key=len, reverse=True):
            message = message.replace(value, "[REDACTED]")
        names = {"BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "GIFT_RADAR_ENCRYPTION_KEY"}
        names.update(k for k in os.environ if k.startswith(("PORTALS_", "TONNEL_", "MRKT_")))
        for name in names:
            value = os.environ.get(name)
            if value and len(value) >= 8:
                message = message.replace(value, "[REDACTED]")
        message = re.sub(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{25,}\b", "[REDACTED]", message)
        if record.exc_info:
            message += f" exception={getattr(record.exc_info[0], '__name__', 'UnknownError')}"
        record.msg, record.args = message, ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True
