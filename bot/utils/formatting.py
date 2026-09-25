from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from bot.config import get_settings

STATUS_LABELS = {
    "pending": "⏳ Ожидает оплаты",
    "paid": "✅ Оплачен",
    "completed": "✅ Выполнен",
    "cancelled": "❌ Отменён",
    "failed": "⚠️ Ошибка",
    "refunded": "↩️ Возврат",
}

STATUS_EMOJI = {
    "completed": "✅",
    "paid": "✅",
    "pending": "⏳",
    "failed": "⚠️",
    "cancelled": "❌",
    "refunded": "↩️",
}

LOW_STOCK_THRESHOLD = 3
MAX_PRICE = Decimal("1000000000")

_TAG_RE = re.compile(r"<[^>]+>")


def esc(value: object) -> str:
    """Escape any user/admin supplied value before putting it into an HTML message."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def strip_html(value: str | None) -> str:
    """Plain-text version of an HTML-formatted string (for invoices, alerts, etc.)."""
    if not value:
        return ""
    return html.unescape(_TAG_RE.sub("", value)).strip()


def format_price(amount: Decimal) -> str:
    settings = get_settings()
    if settings.uses_stars:
        return f"{int(amount)} ⭐"
    return f"{amount:.2f} {settings.currency}"


def normalize_price(amount: Decimal) -> Decimal:
    """Round a price to what can actually be charged in the configured currency."""
    if get_settings().uses_stars:
        return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_price(raw: str | None) -> Decimal | None:
    """Parse an admin-entered price. Returns None if it isn't a sane positive number."""
    # Accept "199", "199.90", "199,90", "1 500", "199 руб", "199₽", "50⭐".
    if "-" in (raw or ""):
        return None
    text = re.sub(r"[^0-9.,]", "", (raw or "").strip()).replace(",", ".")
    if text.count(".") > 1:
        return None
    if not text:
        return None
    try:
        price = Decimal(text)
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0 or price >= MAX_PRICE:
        return None
    price = normalize_price(price)
    return price if price > 0 else None


def invoice_amount(price: Decimal) -> int:
    """Amount in the smallest currency unit, as Telegram's send_invoice expects."""
    if get_settings().uses_stars:
        return int(price)
    return int((price * 100).to_integral_value(rounding=ROUND_HALF_UP))


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even for timezone=True columns — treat them as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def format_dt(value: datetime | None) -> str:
    value = as_utc(value)
    return value.strftime("%d.%m.%Y %H:%M UTC") if value else "—"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def toggle_dot(is_active: bool) -> str:
    """🟢 for on/active, 🔴 for off/inactive — used consistently across admin lists."""
    return "🟢" if is_active else "🔴"


def neutral_dot(is_set: bool) -> str:
    """🟢 when configured, ⚪ when not — for optional settings (e.g. subscription channel)."""
    return "🟢" if is_set else "⚪"


def stock_line(available: int) -> str:
    """A friendly, status-colored availability line for product cards."""
    if available <= 0:
        return "🔴 Нет в наличии"
    if available <= LOW_STOCK_THRESHOLD:
        return f"🟡 Осталось мало: {available} шт."
    return f"🟢 В наличии: {available} шт."


def user_mention(telegram_id: int, username: str | None, full_name: str | None) -> str:
    """HTML link to a user that works whether or not they have a @username."""
    label = f"@{username}" if username else (full_name or str(telegram_id))
    return f'<a href="tg://user?id={telegram_id}">{esc(label)}</a>'
