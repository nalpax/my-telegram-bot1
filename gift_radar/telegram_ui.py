import asyncio
import logging
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard, ReplyKeyboardRemove, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from .analytics import Rate
from .models import CURRENCIES, FEE_NAMES, MARKETS, Fee, utcnow
from .presentation import FEE_LABELS, comparison, render_card, settings_text, text
from .storage import listing_key

log = logging.getLogger(__name__)
FIELDS = {"min_profit": "Мин. прибыль", "min_roi": "Мин. ROI %", "max_price": "Макс. цена",
          "min_sales": "Минимум аналогов", "min_liquidity": "Ликвидность", "history_days": "Период истории",
          "collections": "Коллекции", "models": "Модели", "backdrops": "Фоны", "patterns": "Узоры",
          "symbols": "Символы", "signature": "Подпись", "signature_discount": "Скидка подписи %",
          "buy_markets": "Площадки покупки", "sell_markets": "Площадки перепродажи", "interval_minutes": "Интервал уведомлений"}
HELP = ("Бот анализирует данные и уведомляет, покупки и кошельки не поддерживаются.\n"
        "/settings — фильтры; /markets — доступность; /scan — просмотр аналитики; /alerts — уведомления; "
        "/favorites — избранное; /fees — расходы по маршруту; /currency — валюты и курсы; /status — состояние.\n"
        "/cancel — отменить ввод; /delete_me — удалить мои настройки и избранное.\n"
        "Площадки пока не подключены: требуется подтверждённый официальный доступ. Цены активных лотов не используются вместо завершённых продаж.")


def keyboard(items, width=2):
    buttons = [Button(label, callback_data=data) for label, data in items]
    return Keyboard([buttons[i:i+width] for i in range(0, len(buttons), width)])


MENU = keyboard([("Аналитика", "scan"), ("Настройки", "settings"), ("Площадки", "markets"),
                 ("Уведомления", "alerts"), ("Избранное", "favorites"), ("Расходы", "fees"),
                 ("Валюты", "currency"), ("Статус", "status")])


def card_buttons(gift):
    key = listing_key(gift)
    rows = []
    if gift.url:
        rows.append([Button("Открыть лот", url=gift.url)])
    rows += [[Button("Сравнить площадки", callback_data=f"compare:{key}")],
             [Button("Аналоги и источники", callback_data=f"evidence:{key}")],
             [Button("В избранное", callback_data=f"favorite:{key}"), Button("Скрыть лот", callback_data=f"hide:{key}")]]
    return Keyboard(rows)


async def send_card(bot, user, value, settings, store):
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
        await bot.send_message(chat_id=user, text=chunk, parse_mode="HTML", disable_web_page_preview=True,
                               reply_markup=card_buttons(value.listing) if i == len(chunks)-1 else None)


class Interface:
    def __init__(self, store, radar):
        self.store, self.radar = store, radar
        self.last_scan = {}

    async def reply(self, update, message, markup=None):
        await update.effective_message.reply_text(message, reply_markup=markup, disable_web_page_preview=True)

    async def command(self, update, context):
        if update.effective_chat.type != "private":
            await self.reply(update, "Настройте бота в личном чате.")
            return
        cmd = update.effective_message.text.split()[0].split("@")[0][1:]
        if cmd == "cancel":
            context.user_data.clear()
            await self.reply(update, "Ввод отменён.", MENU)
            return
        context.user_data.pop("awaiting", None)
        await self.action(cmd, update, context)

    async def action(self, cmd, update, context):
        user = update.effective_user.id
        settings = self.store.settings(user)
        if cmd == "start":
            await self.reply(update, "Gift Radar — аналитический бот. Новое меню:", ReplyKeyboardRemove())
            await self.reply(update, "Gift Radar — аналитика коллекционных подарков.\nУведомления по умолчанию выключены. Настройте расходы и фильтры; рыночные источники пока отключены.", MENU)
            await self.reply(update, settings_text(settings))
        elif cmd == "help":
            await self.reply(update, HELP, MENU)
        elif cmd == "settings":
            await self.reply(update, settings_text(settings), keyboard([(label, f"edit:{key}") for key, label in FIELDS.items()]))
        elif cmd == "markets":
            lines = [f"{m}: {state}\n{detail}" for m, state, detail, _ in self.store.market_status()]
            await self.reply(update, "\n\n".join(lines) + "\n\nИмпорт локального экспорта не означает подключение API.", MENU)
        elif cmd == "alerts":
            await self.reply(update, f"Уведомления {'включены' if settings.alerts else 'выключены'}. До 3 лотов за цикл, без повторов. Неполные расчёты не отправляются.",
                             keyboard([("Выключить" if settings.alerts else "Включить", "togglealerts")]))
        elif cmd == "togglealerts":
            settings.alerts = not settings.alerts
            self.store.save_settings(user, settings)
            await self.action("alerts", update, context)
        elif cmd == "scan":
            now = utcnow()
            if user in self.last_scan and (now - self.last_scan[user]).total_seconds() < 30:
                await self.reply(update, "Повторный просмотр доступен через 30 секунд.")
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
                await self.reply(update, "Нет активных данных. Автосбор площадок пока отключён. Доступен импорт разрешённого экспорта; тестовые лоты в рабочий режим не подставляются.", MENU)
            for value in chosen[:3]:
                await send_card(context.bot, user, value, settings, self.store)
            if len(chosen) > 3:
                await self.reply(update, f"Показаны 3 из {len(chosen)} кандидатов. Настройте площадки и используйте сравнение. /scan показывает аналитические кандидаты; фильтры прибыли применяются к уведомлениям.")
        elif cmd == "favorites":
            keys = self.store.marked("favorites", user)
            if not keys:
                await self.reply(update, "Избранное пусто.", MENU)
            for key in sorted(keys)[:20]:
                gift = self.store.listing(key)
                if gift:
                    await self.reply(update, f"{gift.name[:300]} · {gift.market} · {gift.price} {gift.currency} · {gift.status}",
                                     keyboard([("Карточка", f"compare:{key}"), ("Удалить", f"unfavorite:{key}")]))
        elif cmd == "fees":
            context.user_data["route"] = {}
            await self.reply(update, "Выберите площадку покупки. Для каждой пары площадок и валюты расходы отдельные.",
                             keyboard([(m, f"routebuy:{m}") for m in MARKETS]))
        elif cmd == "currency":
            await self.reply(update, f"Пороги: {settings.calculation_currency}. Показ: {', '.join(settings.display_currencies)}.\n"
                             "Конвертация только по сохранённому курсу с источником и временем, не старше часа. Курс влияет на показ, а сравнение продаж выполняется в исходной валюте.",
                             keyboard([("Валюта порогов", "edit:calculation_currency"), ("Валюты показа", "edit:display_currencies"), ("Курс вручную", "ratebase")]))
        elif cmd == "status":
            await self.reply(update, f"Последнее получение данных: {self.store.last_sync()}\n"
                             f"В базе активных лотов: {len(self.store.listings())}; завершённых продаж за 30 дней: {len(self.store.sales(utcnow()))}.\n"
                             "Проверка источников выполняется фоновым циклом. Наличие процесса не подтверждает доступ к рынкам.", MENU)
        elif cmd == "delete_me":
            await self.reply(update, "Удалить мои настройки, избранное и историю уведомлений?", keyboard([("Удалить", "deleteconfirm"), ("Отмена", "start")]))
        elif cmd == "deleteconfirm":
            self.store.delete_user(user)
            context.user_data.clear()
            await self.reply(update, "Ваши данные удалены. При следующей команде будут созданы настройки по умолчанию.")
        else:
            await self.reply(update, "Это действие из прежнего меню. Отправьте /start, чтобы открыть Gift Radar.", MENU)

    async def callback(self, update, context):
        query = update.callback_query
        await query.answer()
        if update.effective_chat.type != "private":
            return
        user = update.effective_user.id
        data = query.data
        cmd, _, value = data.partition(":")
        if cmd in ("favorite", "unfavorite", "hide", "compare", "evidence"):
            gift = self.store.listing(value)
            if not gift:
                await self.reply(update, "Лот больше не доступен.")
                return
            if cmd == "evidence":
                lines = [f"{text(k)}: {text(gift.provenance[k])}" for k in
                         ("collection", "model", "backdrop", "pattern", "symbol", "serial", "rare", "signature", "signature_text") if k in gift.provenance]
                for i in range(0, max(1,len(lines)), 3):
                    await update.effective_message.reply_text("<b>Происхождение свойств</b>\n" + "\n".join(lines[i:i+3]), parse_mode="HTML")
                values = self.radar.analyses(user, key=value)
                if not values or not values[0].sales:
                    await self.reply(update, "Сопоставимые завершённые продажи не найдены.")
                    return
                for sale in values[0].sales[:5]:
                    g = sale.gift
                    description = (f"Завершённая продажа: {g.market} · {g.price} {g.currency}\n{sale.sold_at:%d.%m.%Y %H:%M UTC}\n"
                                   f"Коллекция: {g.collection}; модель: {g.model}; фон: {g.backdrop}; узор: {g.pattern}; символ: {g.symbol}; "
                                   f"номер: {g.serial}; подпись: {'есть' if g.signature is True else 'нет' if g.signature is False else 'неизвестно'}\n"
                                   f"Основание: {sale.evidence[:500]}; источник: {g.source[:500]}")
                    await self.reply(update, description[:3500], Keyboard([[Button("Источник продажи", url=g.url)]]) if g.url else None)
            elif cmd == "compare":
                values = self.radar.analyses(user, key=value)
                if not values:
                    await self.reply(update, f"Статус лота: {gift.status}. Активная оценка недоступна.")
                    return
                await update.effective_message.reply_text("<b>Сравнение площадок</b>\n" + "\n".join(comparison(v) for v in values), parse_mode="HTML")
                await send_card(context.bot, user, values[0], self.store.settings(user), self.store)
            else:
                self.store.mark("hidden" if cmd == "hide" else "favorites", user, value, remove=cmd == "unfavorite")
                await self.reply(update, "Лот скрыт из просмотра и уведомлений." if cmd == "hide" else "Избранное обновлено.")
        elif cmd == "edit":
            context.user_data.pop("awaiting", None)
            options = {"history_days": [(str(x), str(x)) for x in (7,14,30)],
                       "signature": [("Любая", "any"), ("Есть", "yes"), ("Нет", "no"), ("Неизвестно", "unknown")],
                       "min_liquidity": [("Любая активность", "0"), ("Низкая", "1"), ("Средняя", "2"), ("Высокая", "3")],
                       "calculation_currency": [(x, x) for x in CURRENCIES]}
            if value in options:
                await self.reply(update, "Выберите значение:", keyboard([(label, f"set:{value}:{v}") for label, v in options[value]]))
            elif value in ("buy_markets", "sell_markets", "display_currencies"):
                await self.list_choices(update, context, value)
            elif value in FIELDS:
                context.user_data["awaiting"] = ("setting", value)
                hint = "Названия через запятую; - чтобы убрать ограничение." if value in ("collections", "models", "backdrops", "patterns", "symbols") else "Введите число. Для скидки подписи - означает не задана."
                await self.reply(update, f"{FIELDS[value]}. {hint} /cancel — отмена.")
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
            await self.reply(update, "Сохранено. При смене валюты проверьте числовые пороги.", MENU)
        elif cmd == "listtoggle":
            field, choice = value.split(":", 1)
            choices = CURRENCIES if field == "display_currencies" else MARKETS
            if field not in ("buy_markets", "sell_markets", "display_currencies") or choice not in choices:
                return
            settings = self.store.settings(user)
            old = getattr(settings, field)
            new = [x for x in old if x != choice] if choice in old else old + [choice]
            if not new:
                await self.reply(update, "Оставьте хотя бы одно значение.")
                return
            setattr(settings, field, new)
            self.store.save_settings(user, settings)
            await self.list_choices(update, context, field)
        elif cmd == "routebuy":
            if value not in MARKETS:
                return
            context.user_data["route"] = {"buy": value}
            await self.reply(update, "Выберите площадку перепродажи:", keyboard([(m, f"routesell:{m}") for m in MARKETS]))
        elif cmd == "routesell":
            if value not in MARKETS or "buy" not in context.user_data.get("route", {}):
                return
            context.user_data["route"]["sell"] = value
            await self.reply(update, "Валюта расходов:", keyboard([(c, f"routecurrency:{c}") for c in CURRENCIES]))
        elif cmd == "routecurrency":
            if value not in CURRENCIES or "sell" not in context.user_data.get("route", {}):
                return
            context.user_data["route"]["currency"] = value
            await self.fees_menu(update, context)
        elif cmd == "fee":
            if value not in FEE_NAMES or "currency" not in context.user_data.get("route", {}):
                await self.reply(update, "Начните настройку маршрута командой /fees.")
                return
            context.user_data["awaiting"] = ("fee", value)
            await self.reply(update, f"{FEE_LABELS[value]}: сумма в валюте маршрута или процент с %. - означает неизвестно.\n"
                             "0 вводите только если сбор действительно не применяется. Процент продажи/вывода от цены перепродажи, прочие проценты от цены покупки. Значение будет отмечено как пользовательское.")
        elif cmd == "ratebase":
            await self.reply(update, "Исходная валюта курса:", keyboard([(c, f"ratequote:{c}") for c in CURRENCIES]))
        elif cmd == "ratequote":
            if value not in CURRENCIES:
                return
            context.user_data["rate_base"] = value
            await self.reply(update, f"Сколько единиц целевой валюты стоит 1 {value}? Выберите целевую валюту:",
                             keyboard([(c, f"ratevalue:{c}") for c in CURRENCIES if c != value]))
        elif cmd == "ratevalue":
            if value not in CURRENCIES or "rate_base" not in context.user_data:
                return
            context.user_data["awaiting"] = ("rate", value)
            await self.reply(update, "Введите курс: число единиц целевой валюты за одну единицу исходной. Источник будет помечен «введён пользователем». Срок актуальности — один час.")
        else:
            await self.action(cmd, update, context)

    async def list_choices(self, update, context, field):
        chosen = getattr(self.store.settings(update.effective_user.id), field)
        options = CURRENCIES if field == "display_currencies" else MARKETS
        await self.reply(update, "Нажимайте, чтобы включить или выключить:",
                         keyboard([(("✓ " if c in chosen else "") + c, f"listtoggle:{field}:{c}") for c in options] + [("Готово", "settings")]))

    async def fees_menu(self, update, context):
        r = context.user_data.get("route", {})
        if not all(k in r for k in ("buy", "sell", "currency")):
            await self.reply(update, "Выберите маршрут заново: /fees")
            return
        route = self.store.route(update.effective_user.id, r["buy"], r["sell"], r["currency"])
        lines = [f"{r['buy']} → {r['sell']} · {r['currency']} · {route.conditions}"]
        for name in FEE_NAMES:
            fee = route.fees.get(name, Fee())
            lines.append(f"{FEE_LABELS[name]}: {'неизвестно' if fee.value is None else str(fee.value)+('%' if fee.percent else ' '+r['currency'])}; "
                         f"{fee.source}; дата {fee.updated_at.isoformat() if fee.updated_at else 'неизвестна'}; {'подтверждено' if fee.confirmed else 'не подтверждено площадкой'}")
        await self.reply(update, "\n".join(lines), keyboard([(label, f"fee:{name}") for name, label in FEE_LABELS.items()]))

    async def input(self, update, context):
        if update.effective_chat.type != "private":
            return
        if "awaiting" not in context.user_data:
            await self.reply(update, "Выберите действие в меню.", MENU)
            return
        kind, field = context.user_data["awaiting"]
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
            await self.reply(update, "Сохранено.", MENU)
            if kind == "fee":
                await self.fees_menu(update, context)
        except (ValueError, ArithmeticError, KeyError):
            await self.reply(update, "Некорректное значение. Проверьте диапазон и формат; повторите ввод или /cancel.")


def build_app(token, store, radar, scan_seconds=300, retention_days=90, request=None, polling_request=None):
    ui = Interface(store, radar)
    worker = None

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
        await application.bot.set_my_commands([(name, label) for name, label in
            [("start", "Начало"), ("help", "Помощь"), ("settings", "Настройки"), ("markets", "Площадки"),
             ("scan", "Просмотр аналитики"), ("alerts", "Уведомления"), ("favorites", "Избранное"),
             ("fees", "Расходы"), ("currency", "Валюты"), ("status", "Состояние"), ("cancel", "Отменить ввод")]])
        worker = asyncio.create_task(work())

    async def stop(application):
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    async def on_error(update, context):
        log.warning("telegram_handler_failure category=%s", type(context.error).__name__)
        if update and update.effective_message:
            try:
                await ui.reply(update, "Временная ошибка. Повторите действие позже; детали записаны без секретов.")
            except Exception:
                pass

    # HTTPX honors inherited HTTPS_PROXY and CA trust; never disable TLS verification.
    request = request or HTTPXRequest(connection_pool_size=8, connect_timeout=10, read_timeout=20)
    polling_request = polling_request or HTTPXRequest(connect_timeout=10, read_timeout=40)
    app = (Application.builder().token(token).request(request).get_updates_request(polling_request)
           .post_init(startup).post_stop(stop).build())
    for name in ("start", "help", "settings", "markets", "scan", "alerts", "favorites", "fees", "currency", "status", "cancel", "delete_me"):
        app.add_handler(CommandHandler(name, ui.command))
    app.add_handler(CallbackQueryHandler(ui.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ui.input))
    app.add_error_handler(on_error)
    return app
