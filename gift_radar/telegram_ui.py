import asyncio
import logging
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal

from telegram import (BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault,
                      InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard, ReplyKeyboardRemove, Update)
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, TypeHandler, filters
from telegram.request import HTTPXRequest

from .analytics import Rate
from .models import CURRENCIES, FEE_NAMES, MARKETS, Fee, utcnow
from .presentation import FEE_LABELS, comparison, render_card, settings_text, text
from .storage import listing_key
from .security import contains_secret, erase_input, is_admin, private_admin

log = logging.getLogger(__name__)
FIELDS = {"min_profit": "💰 Мин. прибыль", "min_roi": "📈 Мин. ROI %", "max_price": "🏷 Макс. цена",
          "min_sales": "🔎 Минимум аналогов", "min_liquidity": "💧 Ликвидность", "history_days": "🗓 Период истории",
          "collections": "🎁 Коллекции", "models": "🧩 Модели", "backdrops": "🎨 Фоны", "patterns": "✨ Узоры",
          "symbols": "🔣 Символы", "signature": "✍️ Подпись", "signature_discount": "✂️ Скидка подписи %",
          "buy_markets": "🛍 Площадки покупки", "sell_markets": "📤 Площадки перепродажи", "interval_minutes": "⏱ Интервал уведомлений"}
HELP = ("Бот анализирует данные и уведомляет, покупки и кошельки не поддерживаются.\n"
        "/settings — фильтры; /markets — доступность; /scan — просмотр аналитики; /alerts — уведомления; "
        "/favorites — избранное; /fees — расходы по маршруту; /currency — валюты и курсы; /status — состояние.\n"
        "/cancel — отменить ввод; /delete_me — удалить мои настройки и избранное.\n"
        "Площадки пока не подключены: требуется подтверждённый официальный доступ. Цены активных лотов не используются вместо завершённых продаж.")
BOT_COMMANDS = [("start", "Начало"), ("help", "Помощь"), ("settings", "Настройки"), ("markets", "Площадки"),
                ("scan", "Просмотр аналитики"), ("alerts", "Уведомления"), ("favorites", "Избранное"),
                ("fees", "Расходы"), ("currency", "Валюты"), ("status", "Состояние"),
                ("cancel", "Отменить ввод"), ("id", "Мой ID")]


def keyboard(items, width=2, *, back=None, home=False):
    buttons = [Button(label, callback_data=data) for label, data in items]
    rows = [buttons[i:i+width] for i in range(0, len(buttons), width)]
    navigation = []
    if back:
        navigation.append(Button("⬅️ Назад", callback_data=back))
    if home:
        navigation.append(Button("🏠 Главное меню", callback_data="menu"))
    if navigation:
        rows.append(navigation)
    return Keyboard(rows)


MENU = keyboard([("🔎 Аналитика", "scan"), ("⚙️ Настройки", "settings"), ("🏪 Площадки", "markets"),
                 ("🔔 Уведомления", "alerts"), ("⭐ Избранное", "favorites"), ("🧾 Расходы", "fees"),
                 ("💱 Валюты", "currency"), ("📡 Статус", "status"), ("❔ Помощь", "help")])


def settings_parent(field):
    return "currency" if field in ("calculation_currency", "display_currencies") else "settings"


def input_navigation(context):
    pending = context.user_data.get("awaiting")
    if not pending:
        return keyboard([], home=True)
    kind, field = pending
    parent = f"market:{field}" if kind.startswith("market_") else settings_parent(field) if kind == "setting" else "feesview" if kind == "fee" else "rate_quote_menu"
    return keyboard([], back=parent, home=True)


def card_buttons(gift):
    key = listing_key(gift)
    rows = []
    if gift.url:
        rows.append([Button("🔗 Открыть лот", url=gift.url)])
    rows += [[Button("⚖️ Сравнить площадки", callback_data=f"compare:{key}")],
             [Button("🔎 Аналоги и источники", callback_data=f"evidence:{key}")],
             [Button("⭐ В избранное", callback_data=f"favorite:{key}"), Button("🙈 Скрыть лот", callback_data=f"hide:{key}")],
             [Button("⬅️ К аналитике", callback_data="scan"), Button("🏠 Главное меню", callback_data="menu")]]
    return Keyboard(rows)


async def send_card(bot, user, value, settings, store):
    if not is_admin(user):
        raise PermissionError("Administrator access required")
    # Split on lines, never inside an HTML tag/entity. Keep each Telegram message <4096.
    body = render_card(value, settings, store, user=user)
    chunks, current = [], ""
    for line in body.splitlines():
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    chunks.append(current)
    for i, chunk in enumerate(chunks):
        if not is_admin(user):
            raise PermissionError("Administrator access revoked")
        await bot.send_message(chat_id=user, text=chunk, parse_mode="HTML", disable_web_page_preview=True,
                               protect_content=True,
                               reply_markup=card_buttons(value.listing) if i == len(chunks)-1 else None)


class Interface:
    def __init__(self, store, radar):
        self.store, self.radar = store, radar
        self.last_scan = {}
        self.commands_ready = set()
        from .market_ui import MarketInterface
        self.markets = MarketInterface(self)

    async def reply(self, update, message, markup=None):
        if not private_admin(update):
            return
        await update.effective_message.reply_text(message, reply_markup=markup, disable_web_page_preview=True, protect_content=True)

    async def register_commands(self, user, bot):
        if user in self.commands_ready or not is_admin(user):
            return
        try:
            await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeChat(user))
        except Exception as exc:
            log.warning("private_command_menu_failure category=%s", type(exc).__name__)
        else:
            self.commands_ready.add(user)

    async def command(self, update, context):
        if not private_admin(update):
            return
        cmd = update.effective_message.text.split()[0].split("@")[0][1:]
        if cmd == "cancel":
            context.user_data.clear()
            await self.reply(update, "↩️ Ввод отменён.", MENU)
            return
        context.user_data.pop("awaiting", None)
        await self.action(cmd, update, context)

    async def action(self, cmd, update, context):
        if not private_admin(update):
            return
        user = update.effective_user.id
        settings = self.store.settings(user)
        if cmd == "start":
            context.user_data.clear()
            await self.reply(update, "🎁 Gift Radar — аналитический бот", ReplyKeyboardRemove())
            await self.action("menu", update, context)
        elif cmd == "menu":
            context.user_data.clear()
            await self.reply(update, "🎁 Gift Radar\n━━━━━━━━━━━━━━\n"
                             "Аналитика коллекционных подарков\n\n"
                             f"🔔 Уведомления: {'🟢 включены' if settings.alerts else '🔴 выключены'}\n"
                             f"💱 Валюта порогов: {settings.calculation_currency}\n"
                             "⚙️ Настройте фильтры и расходы под себя.\n\n"
                             "⚪ Рыночные источники пока отключены.\n"
                             "👇 Выберите раздел:", MENU)
        elif cmd == "help":
            await self.reply(update, "❔ Помощь\n━━━━━━━━━━━━━━\n" + HELP, keyboard([], home=True))
        elif cmd == "settings":
            await self.reply(update, "⚙️ Настройки\n━━━━━━━━━━━━━━\n" + settings_text(settings),
                             keyboard([(label, f"edit:{key}") for key, label in FIELDS.items()], home=True))
        elif cmd == "markets":
            await self.markets.overview(update)
        elif cmd == "id":
            await self.reply(update, f"🆔 Твой Telegram ID: {user}\nДля управления подключениями владелец добавляет свой ID в ADMIN_IDS на хостинге.", keyboard([], home=True))
        elif cmd == "alerts":
            await self.reply(update, "🔔 Уведомления\n━━━━━━━━━━━━━━\n"
                             f"{'🟢 Включены' if settings.alerts else '🔴 Выключены'}\n\nДо 3 лотов за цикл, без повторов. Неполные расчёты не отправляются.",
                             keyboard([("🔕 Выключить" if settings.alerts else "🔔 Включить", "togglealerts")], home=True))
        elif cmd == "togglealerts":
            settings.alerts = not settings.alerts
            self.store.save_settings(user, settings)
            await self.action("alerts", update, context)
        elif cmd == "scan":
            now = utcnow()
            if user in self.last_scan and (now - self.last_scan[user]).total_seconds() < 30:
                await self.reply(update, "⏳ Повторный просмотр доступен через 30 секунд.", keyboard([], home=True))
                return
            self.last_scan[user] = now
            values = self.radar.analyses(user, now=now)
            # One best target per gift in scan; compare button exposes other targets.
            seen, chosen = set(), []
            for value in values:
                key = listing_key(value.listing)
                if key not in seen:
                    seen.add(key)
                    chosen.append(value)
            if not chosen:
                await self.reply(update, "🔎 Аналитика\n━━━━━━━━━━━━━━\nНет активных данных. Автосбор площадок пока отключён. Доступен импорт разрешённого экспорта; тестовые лоты в рабочий режим не подставляются.", keyboard([], home=True))
            for value in chosen[:3]:
                await send_card(context.bot, user, value, settings, self.store)
            if len(chosen) > 3:
                await self.reply(update, f"Показаны 3 из {len(chosen)} кандидатов. Настройте площадки и используйте сравнение. /scan показывает аналитические кандидаты; фильтры прибыли применяются к уведомлениям.", keyboard([], home=True))
        elif cmd == "favorites":
            keys = self.store.marked("favorites", user)
            if not keys:
                await self.reply(update, "⭐ Избранное\n━━━━━━━━━━━━━━\nИзбранное пусто. Сохраняйте лоты кнопкой ⭐ в карточке.", keyboard([], home=True))
            for key in sorted(keys)[:20]:
                gift = self.store.listing(key)
                if gift:
                    await self.reply(update, f"{gift.name[:300]} · {gift.market} · {gift.price} {gift.currency} · {gift.status}",
                                     keyboard([("🎁 Карточка", f"compare:{key}"), ("🗑 Удалить", f"unfavorite:{key}")], home=True))
        elif cmd == "fees":
            context.user_data["route"] = {}
            await self.reply(update, "🧾 Расходы · шаг 1 из 3\n━━━━━━━━━━━━━━\nВыберите площадку покупки. Для каждой пары площадок и валюты расходы отдельные.",
                             keyboard([(m.title(), f"routebuy:{m}") for m in MARKETS], home=True))
        elif cmd == "currency":
            await self.reply(update, "💱 Валюты\n━━━━━━━━━━━━━━\n"
                             f"Пороги: {settings.calculation_currency}. Показ: {', '.join(settings.display_currencies)}.\n"
                             "Конвертация только по сохранённому курсу с источником и временем, не старше часа. Курс влияет на показ, а сравнение продаж выполняется в исходной валюте.",
                             keyboard([("🎯 Валюта порогов", "edit:calculation_currency"), ("👁 Валюты показа", "edit:display_currencies"), ("✏️ Курс вручную", "ratebase")], home=True))
        elif cmd == "status":
            await self.reply(update, "📡 Статус сервиса\n━━━━━━━━━━━━━━\n"
                             f"Последнее получение данных: {self.store.last_sync()}\n"
                             f"В базе активных лотов: {len(self.store.listings())}; завершённых продаж за 30 дней: {len(self.store.sales(utcnow()))}.\n"
                             "Проверка источников выполняется фоновым циклом. Наличие процесса не подтверждает доступ к рынкам.", keyboard([], home=True))
        elif cmd == "delete_me":
            await self.reply(update, "🗑 Удалить мои настройки, избранное и историю уведомлений?", keyboard([("🗑 Удалить", "deleteconfirm"), ("↩️ Отмена", "menu")], home=True))
        elif cmd == "deleteconfirm":
            self.store.delete_user(user)
            context.user_data.clear()
            await self.reply(update, "✅ Ваши данные удалены. При следующей команде будут созданы настройки по умолчанию.", keyboard([], home=True))
        else:
            await self.reply(update, "Это действие из прежнего меню. Отправьте /start, чтобы открыть Gift Radar.", MENU)

    async def callback(self, update, context):
        if not private_admin(update):
            return
        query = update.callback_query
        await query.answer()
        if not private_admin(update):
            return
        user = update.effective_user.id
        data = query.data
        cmd, _, value = data.partition(":")
        # Leaving any input screen cancels its draft; later text must not save it.
        context.user_data.pop("awaiting", None)
        if await self.markets.callback(cmd, value, update, context):
            return
        if cmd in ("favorite", "unfavorite", "hide", "compare", "evidence"):
            gift = self.store.listing(value)
            if not gift:
                await self.reply(update, "Лот больше не доступен.", keyboard([], home=True))
                return
            if cmd == "evidence":
                lines = [f"{text(k)}: {text(gift.provenance[k])}" for k in
                         ("collection", "model", "backdrop", "pattern", "symbol", "serial", "rare", "signature", "signature_text") if k in gift.provenance]
                for i in range(0, max(1,len(lines)), 3):
                    await update.effective_message.reply_text("<b>🔎 Происхождение свойств</b>\n" + "\n".join(lines[i:i+3]), parse_mode="HTML", protect_content=True,
                                                              reply_markup=keyboard([], back=f"compare:{value}", home=True))
                values = self.radar.analyses(user, key=value)
                if not values or not values[0].sales:
                    await self.reply(update, "Сопоставимые завершённые продажи не найдены.", keyboard([], back=f"compare:{value}", home=True))
                    return
                for sale in values[0].sales[:5]:
                    g = sale.gift
                    description = (f"Завершённая продажа: {g.market} · {g.price} {g.currency}\n{sale.sold_at:%d.%m.%Y %H:%M UTC}\n"
                                   f"Коллекция: {g.collection}; модель: {g.model}; фон: {g.backdrop}; узор: {g.pattern}; символ: {g.symbol}; "
                                   f"номер: {g.serial}; подпись: {'есть' if g.signature is True else 'нет' if g.signature is False else 'неизвестно'}\n"
                                   f"Основание: {sale.evidence[:500]}; источник: {g.source[:500]}")
                    nav = keyboard([], back=f"compare:{value}", home=True)
                    rows = ([[Button("🔗 Источник продажи", url=g.url)]] if g.url else []) + list(nav.inline_keyboard)
                    await self.reply(update, description[:3500], Keyboard(rows))
            elif cmd == "compare":
                values = self.radar.analyses(user, key=value)
                if not values:
                    await self.reply(update, f"Статус лота: {gift.status}. Активная оценка недоступна.", keyboard([], back="favorites", home=True))
                    return
                await update.effective_message.reply_text("<b>⚖️ Сравнение площадок</b>\n" + "\n".join(comparison(v) for v in values), parse_mode="HTML", protect_content=True,
                                                          reply_markup=keyboard([], back="scan", home=True))
                await send_card(context.bot, user, values[0], self.store.settings(user), self.store)
            else:
                self.store.mark("hidden" if cmd == "hide" else "favorites", user, value, remove=cmd == "unfavorite")
                await self.reply(update, "🙈 Лот скрыт из просмотра и уведомлений." if cmd == "hide" else "⭐ Избранное обновлено.",
                                 keyboard([], back="favorites" if cmd == "unfavorite" else "scan" if cmd == "hide" else f"compare:{value}", home=True))
        elif cmd == "edit":
            context.user_data.pop("awaiting", None)
            options = {"history_days": [(str(x), str(x)) for x in (7,14,30)],
                       "signature": [("Любая", "any"), ("Есть", "yes"), ("Нет", "no"), ("Неизвестно", "unknown")],
                       "min_liquidity": [("Любая активность", "0"), ("Низкая", "1"), ("Средняя", "2"), ("Высокая", "3")],
                       "calculation_currency": [(x, x) for x in CURRENCIES]}
            if value in options:
                await self.reply(update, "⚙️ Выберите значение:",
                                 keyboard([(label, f"set:{value}:{v}") for label, v in options[value]], back=settings_parent(value), home=True))
            elif value in ("buy_markets", "sell_markets", "display_currencies"):
                await self.list_choices(update, context, value)
            elif value in FIELDS:
                context.user_data["awaiting"] = ("setting", value)
                hint = "Названия через запятую; - чтобы убрать ограничение." if value in ("collections", "models", "backdrops", "patterns", "symbols") else "Введите число. Для скидки подписи - означает не задана."
                await self.reply(update, f"{FIELDS[value]}\n━━━━━━━━━━━━━━\n{hint}\n\n⬅️ Назад — отменить ввод.", input_navigation(context))
        elif cmd == "set":
            field, raw = value.split(":",1)
            settings = self.store.settings(user)
            if field == "min_liquidity":
                from .analytics import LIQUIDITY
                raw = LIQUIDITY[int(raw)]
            if field == "history_days":
                raw = int(raw)
            if field not in ("min_liquidity", "history_days", "signature", "calculation_currency"):
                return
            setattr(settings, field, raw)
            self.store.save_settings(user, settings)
            await self.reply(update, "✅ Сохранено." + (" При смене валюты проверьте числовые пороги." if field == "calculation_currency" else ""),
                             keyboard([], back=settings_parent(field), home=True))
        elif cmd == "listtoggle":
            field, choice = value.split(":", 1)
            choices = CURRENCIES if field == "display_currencies" else MARKETS
            if field not in ("buy_markets", "sell_markets", "display_currencies") or choice not in choices:
                return
            settings = self.store.settings(user)
            old = getattr(settings, field)
            new = [x for x in old if x != choice] if choice in old else old + [choice]
            if not new:
                await self.reply(update, "Оставьте хотя бы одно значение.", keyboard([], back=f"edit:{field}", home=True))
                return
            setattr(settings, field, new)
            self.store.save_settings(user, settings)
            await self.list_choices(update, context, field)
        elif cmd == "routebuy":
            if value not in MARKETS:
                return
            context.user_data["route"] = {"buy": value}
            await self.route_sell_menu(update, context)
        elif cmd == "route_sell_menu":
            await self.route_sell_menu(update, context)
        elif cmd == "routesell":
            if value not in MARKETS or "buy" not in context.user_data.get("route", {}):
                return
            context.user_data["route"]["sell"] = value
            context.user_data["route"].pop("currency", None)
            await self.route_currency_menu(update, context)
        elif cmd == "route_currency_menu":
            await self.route_currency_menu(update, context)
        elif cmd == "routecurrency":
            if value not in CURRENCIES or "sell" not in context.user_data.get("route", {}):
                return
            context.user_data["route"]["currency"] = value
            await self.fees_menu(update, context)
        elif cmd == "feesview":
            await self.fees_menu(update, context)
        elif cmd == "fee":
            if value not in FEE_NAMES or "currency" not in context.user_data.get("route", {}):
                await self.reply(update, "Выберите маршрут расходов заново.", keyboard([("🧾 Выбрать маршрут", "fees")], home=True))
                return
            context.user_data["awaiting"] = ("fee", value)
            await self.reply(update, f"✏️ {FEE_LABELS[value]}\n━━━━━━━━━━━━━━\nСумма в валюте маршрута или процент с %. - означает неизвестно.\n"
                             "0 вводите только если сбор действительно не применяется. Процент продажи/вывода от цены перепродажи, прочие проценты от цены покупки. Значение будет отмечено как пользовательское.", input_navigation(context))
        elif cmd == "ratebase":
            context.user_data.pop("rate_base", None)
            await self.reply(update, "💱 Ручной курс · шаг 1 из 3\n━━━━━━━━━━━━━━\nИсходная валюта курса:",
                             keyboard([(c, f"ratequote:{c}") for c in CURRENCIES], back="currency", home=True))
        elif cmd == "ratequote":
            if value not in CURRENCIES:
                return
            context.user_data["rate_base"] = value
            await self.rate_quote_menu(update, context)
        elif cmd == "rate_quote_menu":
            await self.rate_quote_menu(update, context)
        elif cmd == "ratevalue":
            if value not in CURRENCIES or "rate_base" not in context.user_data:
                return
            context.user_data["awaiting"] = ("rate", value)
            await self.reply(update, "💱 Ручной курс · шаг 3 из 3\n━━━━━━━━━━━━━━\nВведите курс: число единиц целевой валюты за одну единицу исходной. Источник будет помечен «введён пользователем». Срок актуальности — один час.", input_navigation(context))
        else:
            await self.action(cmd, update, context)

    async def list_choices(self, update, context, field):
        chosen = getattr(self.store.settings(update.effective_user.id), field)
        options = CURRENCIES if field == "display_currencies" else MARKETS
        parent = settings_parent(field)
        await self.reply(update, "✅ Выбор значений\n━━━━━━━━━━━━━━\nНажимайте, чтобы включить или выключить:",
                         keyboard([(("🟢 " if c in chosen else "⚪ ") + c, f"listtoggle:{field}:{c}") for c in options] + [("✅ Готово", parent)], back=parent, home=True))

    async def route_sell_menu(self, update, context):
        if "buy" not in context.user_data.get("route", {}):
            await self.action("fees", update, context)
            return
        await self.reply(update, "🧾 Расходы · шаг 2 из 3\n━━━━━━━━━━━━━━\nВыберите площадку перепродажи:",
                         keyboard([(m.title(), f"routesell:{m}") for m in MARKETS], back="fees", home=True))

    async def route_currency_menu(self, update, context):
        if "sell" not in context.user_data.get("route", {}):
            await self.route_sell_menu(update, context)
            return
        await self.reply(update, "🧾 Расходы · шаг 3 из 3\n━━━━━━━━━━━━━━\nВалюта расходов:",
                         keyboard([(c, f"routecurrency:{c}") for c in CURRENCIES], back="route_sell_menu", home=True))

    async def rate_quote_menu(self, update, context):
        base = context.user_data.get("rate_base")
        if base not in CURRENCIES:
            await self.reply(update, "Выберите исходную валюту заново.", keyboard([("💱 Выбрать валюту", "ratebase")], back="currency", home=True))
            return
        await self.reply(update, f"💱 Ручной курс · шаг 2 из 3\n━━━━━━━━━━━━━━\nСколько единиц целевой валюты стоит 1 {base}? Выберите целевую валюту:",
                         keyboard([(c, f"ratevalue:{c}") for c in CURRENCIES if c != base], back="ratebase", home=True))

    async def fees_menu(self, update, context):
        r = context.user_data.get("route", {})
        if not all(k in r for k in ("buy", "sell", "currency")):
            await self.reply(update, "Выберите маршрут заново.", keyboard([("🧾 Выбрать маршрут", "fees")], home=True))
            return
        route = self.store.route(update.effective_user.id, r["buy"], r["sell"], r["currency"])
        lines = ["🧾 Расходы маршрута\n━━━━━━━━━━━━━━", f"{r['buy']} → {r['sell']} · {r['currency']} · {route.conditions}"]
        for name in FEE_NAMES:
            fee = route.fees.get(name, Fee())
            lines.append(f"{FEE_LABELS[name]}: {'неизвестно' if fee.value is None else str(fee.value)+('%' if fee.percent else ' '+r['currency'])}; "
                         f"{fee.source}; дата {fee.updated_at.isoformat() if fee.updated_at else 'неизвестна'}; {'подтверждено' if fee.confirmed else 'не подтверждено площадкой'}")
        await self.reply(update, "\n".join(lines), keyboard([("✏️ " + label, f"fee:{name}") for name, label in FEE_LABELS.items()], back="route_currency_menu", home=True))

    async def input(self, update, context):
        if not private_admin(update):
            return
        try:
            await self.process_input(update, context)
        finally:
            await erase_input(update)

    async def process_input(self, update, context):
        pending = context.user_data.get("awaiting")
        if pending and pending[0] == "market_key":
            await self.markets.input(update, context)
            return
        names = [self.store.connection(market).key_env for market in MARKETS]
        if contains_secret(update.effective_message.text, names):
            await self.reply(update, "🔒 Здесь секреты не принимаются. Для ключа выбери «Площадки» → нужную площадку → «Добавить / заменить ключ» в личном чате администратора.", input_navigation(context))
            return
        if "awaiting" not in context.user_data:
            await self.reply(update, "Выберите действие в меню.", MENU)
            return
        if await self.markets.input(update, context):
            return
        kind, field = context.user_data["awaiting"]
        navigation = input_navigation(context)
        raw = update.effective_message.text.strip()
        user = update.effective_user.id
        try:
            if len(raw) > 5000:
                raise ValueError("Слишком длинный ввод")
            if kind == "setting":
                settings = self.store.settings(user)
                if field in ("collections", "models", "backdrops", "patterns", "symbols"):
                    value = [] if raw == "-" else list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))
                elif field in ("min_sales", "interval_minutes"):
                    value = int(raw)
                else:
                    value = None if raw == "-" and field == "signature_discount" else Decimal(raw.replace(",", "."))
                setattr(settings, field, value)
                self.store.save_settings(user, settings)
            elif kind == "fee":
                r = context.user_data["route"]
                route = self.store.route(user, r["buy"], r["sell"], r["currency"])
                value = None if raw == "-" else Decimal(raw.rstrip("%").replace(",", "."))
                fee = Fee(value, raw.endswith("%"), "введено пользователем", utcnow(), False)
                self.store.save_route(user, replace(route, fees={**route.fees, field: fee}))
            elif kind == "rate":
                from .models import money
                value = money(raw.replace(",", "."), positive=True)
                self.store.save_rate(Rate(context.user_data["rate_base"], field, value, utcnow(), "введён пользователем"), user=user)
            context.user_data.pop("awaiting", None)
            await self.reply(update, "✅ Сохранено.", navigation)
            if kind == "fee":
                await self.fees_menu(update, context)
        except (ValueError, ArithmeticError, KeyError):
            await self.reply(update, "⚠️ Некорректное значение. Проверьте диапазон и формат; повторите ввод или нажмите «Назад».", navigation)

    async def document(self, update, context):
        if not private_admin(update):
            return
        try:
            await self.markets.document(update, context)
        finally:
            await erase_input(update)


def build_app(token, store, radar, scan_seconds=300, retention_days=90, request=None, polling_request=None):
    ui = Interface(store, radar)
    worker = None

    async def authorize(update, context):
        if private_admin(update):
            await ui.register_commands(update.effective_user.id, context.bot)
            return
        if update.effective_user:
            context.user_data.clear()
        try:
            if update.callback_query:
                await update.callback_query.answer("🔒 Доступ только администратору в личном чате.", show_alert=True)
            elif (update.effective_message and update.effective_chat and update.effective_user and
                  update.effective_chat.type == "private" and update.effective_chat.id == update.effective_user.id):
                await update.effective_message.reply_text(
                    f"🔒 Доступ закрыт. Бот доступен только администратору.\nТвой Telegram ID: {update.effective_user.id}",
                    protect_content=True)
        except Exception:
            pass
        finally:
            # A failed Telegram denial response MUST NOT allow later handlers to execute.
            raise ApplicationHandlerStop

    async def sender(user, value):
        await send_card(app.bot, user, value, store.settings(user), store)

    async def work():
        while True:
            try:
                await radar.tick(sender)
                store.cleanup(utcnow(), retention_days)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("worker_failure category=%s", type(exc).__name__)
            await asyncio.sleep(scan_seconds)

    async def startup(application):
        nonlocal worker
        await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
        await application.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        for user in store.users():
            if not is_admin(user):
                try:
                    await application.bot.delete_my_commands(scope=BotCommandScopeChat(user))
                except Exception as exc:
                    log.warning("revoked_command_menu_cleanup_failure category=%s", type(exc).__name__)
        # Admin menus are registered for their private chats after verified updates.
        worker = asyncio.create_task(work())

    async def stop(application):
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    async def on_error(update, context):
        log.warning("telegram_handler_failure category=%s", type(context.error).__name__)
        if update and update.effective_message and private_admin(update):
            try:
                await ui.reply(update, "Временная ошибка. Повторите действие позже; детали записаны без секретов.")
            except Exception:
                pass

    # HTTPX honors inherited HTTPS_PROXY and CA trust; never disable TLS verification.
    request = request or HTTPXRequest(connection_pool_size=8, connect_timeout=10, read_timeout=20)
    polling_request = polling_request or HTTPXRequest(connect_timeout=10, read_timeout=40)
    app = (Application.builder().token(token).request(request).get_updates_request(polling_request)
           .post_init(startup).post_stop(stop).build())
    app.add_handler(TypeHandler(Update, authorize), group=-100)
    for name in ("start", "help", "settings", "markets", "scan", "alerts", "favorites", "fees", "currency", "status", "cancel", "delete_me", "id"):
        app.add_handler(CommandHandler(name, ui.command))
    app.add_handler(CallbackQueryHandler(ui.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ui.input))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, ui.document))
    app.add_error_handler(on_error)
    return app
