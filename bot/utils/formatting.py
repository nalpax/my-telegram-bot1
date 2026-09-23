from __future__ import annotations

from decimal import Decimal

from bot.config import get_settings

STATUS_LABELS = {
    "pending": "⏳ Ожидает оплаты",
    "paid": "✅ Оплачен",
    "completed": "✅ Выполнен",
    "cancelled": "❌ Отменён",
    "failed": "⚠️ Ошибка",
}

LOW_STOCK_THRESHOLD = 3


def format_price(amount: Decimal) -> str:
    settings = get_settings()
    if settings.uses_stars:
        return f"{int(amount)} ⭐"
    return f"{amount:.2f} {settings.currency}"


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
