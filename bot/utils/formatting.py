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


def format_price(amount: Decimal) -> str:
    settings = get_settings()
    if settings.uses_stars:
        return f"{int(amount)} ⭐"
    return f"{amount:.2f} {settings.currency}"


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)
