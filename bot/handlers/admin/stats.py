from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repo
from bot.filters.admin import IsAdmin
from bot.keyboards.user import back_kb
from bot.utils.formatting import esc, format_price
from bot.utils.ui import render

router = Router(name="admin_stats")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)

    text = (
        "📊 <b>Статистика</b>\n"
        "─────────────────\n"
        f"👥 Пользователей: {await repo.count_users(session)} "
        f"(+{await repo.count_users(session, today)} сегодня)\n"
        f"📁 Категорий: {await repo.count_categories(session)}\n"
        f"📦 Товаров: {await repo.count_products(session)}\n"
        "─────────────────\n"
        "💰 <b>Продажи</b>\n"
        f"Сегодня: {await repo.count_completed_orders(session, today)} шт. / "
        f"{format_price(await repo.sum_sales(session, today))}\n"
        f"7 дней: {await repo.count_completed_orders(session, week)} шт. / "
        f"{format_price(await repo.sum_sales(session, week))}\n"
        f"30 дней: {await repo.count_completed_orders(session, month)} шт. / "
        f"{format_price(await repo.sum_sales(session, month))}\n"
        f"Всего: {await repo.count_completed_orders(session)} шт. / "
        f"{format_price(await repo.sum_sales(session))}\n"
    )
    failed = await repo.count_failed_orders(session)
    if failed:
        text += f"⚠️ Проблемных заказов (нужен возврат/проверка): {failed}\n"

    top = await repo.top_products(session)
    if top:
        text += "─────────────────\n🏆 <b>Топ товаров</b>\n"
        text += "\n".join(f"{i}. {esc(name)} — {cnt} шт." for i, (name, cnt) in enumerate(top, start=1))
        text += "\n"

    low = await repo.list_low_stock_products(session)
    if low:
        text += "─────────────────\n📉 <b>Заканчиваются</b>\n"
        text += "\n".join(f"{'🔴' if cnt == 0 else '🟡'} {esc(p.name)} — {cnt} шт." for p, cnt in low[:15])

    await render(callback, text, back_kb("adm:menu", "◀️ Назад"))
    await callback.answer()
