import asyncio
import logging
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal

from telegram import (BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault,
                      InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard, ReplyKeyboardRemove, Update)
from telegram.error import BadRequest
from telegram.ext import Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, TypeHandler, filters
from telegram.request import HTTPXRequest

from .analytics import INCOMPLETE, REASONS, Rate, rejection
from .models import CURRENCIES, FEE_NAMES, MARKETS, Fee, utcnow
from .presentation import FEE_LABELS, comparison, render_card, settings_text, text
from .sources import TITLES
from .storage import listing_key
from .security import contains_secret, erase_input, is_admin, private_admin

log = logging.getLogger(__name__)
FIELDS = {"min_profit": "💰 Мин. прибыль", "min_roi": "📈 Мин. ROI %", "max_price": "🏷 Макс. цена",
          "min_sales": "🔎 Минимум аналогов", "min_liquidity": "💧 Ликвидность", "history_days": "🗓 Период истории",
          "collections": "🎁 Коллекции", "models": "🧩 Модели", "backdrops": "🎨 Фоны", "patterns": "✨ Узоры",
          "symbols": "🔣 Символы", "signature": "✍️ Подпись", "signature_discount": "✂️ Скидка подписи %",
          "buy_markets": "🛍 Площадки покупки", "sell_markets": "📤 Площадки перепродажи", "interval_minutes": "⏱ Интервал уведомлений"}
# Marketplaces and the interval have their own toggles/screens; old buttons for them still work.
FILTER_FIELDS = [k for k in FIELDS if k not in ("buy_markets", "sell_markets", "interval_minutes")]
NOTIFY_TOGGLES = {"alerts": "Автоуведомления", "sound": "Звук", "only_new": "Только новые лоты", "error_alerts": "Ошибки подключения"}
ANALYSIS_TOGGLES = {"signed": "Подписанные подарки", "rare": "Редкие варианты", "incomplete": "Неполные данные в поиске"}
HELP = ("Gift Radar анализирует подарки и присылает уведомления. Покупок, продаж и переводов бот не выполняет.\n\n"
        "🔎 Поиск — кандидаты из полученных данных, избранное, обновление данных.\n"
        "🏪 Площадки — подключение Thermos, участие Portals, MRKT и Tonnel в анализе, покупке и перепродаже.\n"
        "🔔 Уведомления — фоновые карточки, звук, повторы, ошибки подключения.\n"
        "⚙️ Настройки — анализ, пороги, расходы, валюты.\n\n"
        "/cancel — отменить ввод.")
BOT_COMMANDS = [("start", "Главное меню"), ("markets", "Площадки"), ("scan", "Поиск"), ("alerts", "Уведомления"),
                ("settings", "Настройки"), ("cancel", "Отменить ввод")]


def mark(on):
    return "✅" if on else "❌"


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


MENU = keyboard([("🔎 Поиск", "search"), ("🏪 Площадки", "markets"), ("🔔 Уведомления", "alerts"), ("⚙️ Настройки", "settings")])


def settings_parent(field):
    if field in ("calculation_currency", "display_currencies"):
        return "currency"
    return "alerts" if field == "interval_minutes" else "filters"


def input_navigation(context):
    pending = context.user_data.get("awaiting")
    if not pending:
        return keyboard([], home=True)
    kind, field = pending
    parent = ("source" if kind == "source_key" else f"market:{field}" if kind.startswith("market_") else
              settings_parent(field) if kind == "setting" else "feesview" if kind == "fee" else "rate_quote_menu")
    return keyboard([], back=parent, home=True)


def card_buttons(gift):
    key = listing_key(gift)
    rows = []
    if gift.url:
        rows.append([Button("🔗 Открыть лот", url=gift.url)])
    rows += [[Button("⚖️ Сравнить площадки", callback_data=f"compare:{key}")],
             [Button("🔎 Аналоги и источники", callback_data=f"evidence:{key}")],
             [Button("⭐ В избранное", callback_data=f"favorite:{key}"), Button("🙈 Скрыть лот", callback_data=f"hide:{key}")],
             [Button("🔎 Поиск", callback_data="search"), Button("🏠 Главное меню", callback_data="menu")]]
    return Keyboard(rows)


async def send_card(bot, user, value, settings, store, note=None):
    if not is_admin(user):
        raise PermissionError("Administrator access required")
    # Split on lines, never inside an HTML tag/entity. Keep each Telegram message <4096.
    body = render_card(value, settings, store, user=user, note=note)
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
                               protect_content=True, disable_notification=not settings.sound,
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
            return None
        return await update.effective_message.reply_text(message, reply_markup=markup, disable_web_page_preview=True, protect_content=True)

    async def show(self, update, message, markup=None):
        """Edit the pressed menu in place; formatted cards are never overwritten."""
        if not private_admin(update):
            return
        query = update.callback_query
        origin = query.message if query else None
        if origin is not None and getattr(origin, "text", None) is not None and not origin.entities:
            try:
                await query.edit_message_text(message, reply_markup=markup, disable_web_page_preview=True)
                return
            except BadRequest as exc:
                if "not modified" in str(exc).lower():
                    return  # Repeated press: the screen already shows this state.
            except Exception as exc:
                log.warning("menu_edit_failed category=%s", type(exc).__name__)
        await self.reply(update, message, markup)

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
            await self.reply(update, "🎁 Gift Radar", ReplyKeyboardRemove())
            await self.reply(update, self.home_text(settings), MENU)
        elif cmd == "menu":
            context.user_data.clear()
            await self.show(update, self.home_text(settings), MENU)
        elif cmd == "help":
            await self.show(update, "❔ Помощь\n━━━━━━━━━━━━━━\n" + HELP, keyboard([], back="more"))
        elif cmd == "search":
            listings = len(self.store.listings())
            await self.show(update, "🔎 Поиск\n━━━━━━━━━━━━━━\n"
                            f"Активных лотов в базе: {listings}\nПоследнее получение данных: {self.store.last_sync()}\n\n"
                            "«Найти» показывает до 3 лучших кандидатов. «Обновить данные» запрашивает подключённые площадки сейчас.",
                            keyboard([("▶️ Найти", "scan"), ("🔄 Обновить данные", "refresh"), ("📦 Мои лоты Thermos", "mylots"), ("⭐ Избранное", "favorites")], back="menu"))
        elif cmd == "mylots":
            await self.markets.my_lots(update)
        elif cmd == "refresh":
            await self.markets.load_flow(update, context)
        elif cmd == "settings":
            await self.show(update, "⚙️ Настройки\n━━━━━━━━━━━━━━\n"
                            f"Мин. прибыль {settings.min_profit} {settings.calculation_currency} · ROI от {settings.min_roi}% · "
                            f"цена до {settings.max_price}\nРедкие: {mark(settings.include_rare)} · подписанные: {mark(settings.signature != 'no')}",
                            keyboard([("📊 Анализ", "analysis"), ("🎯 Пороги и фильтры", "filters"), ("🧾 Расходы", "fees"),
                                      ("💱 Валюты", "currency"), ("🛠 Дополнительно", "more")], back="menu"))
        elif cmd == "analysis":
            toggles = {"signed": settings.signature != "no", "rare": settings.include_rare, "incomplete": settings.show_incomplete}
            items = [(f"{mark(toggles[k])} {label}", f"at:{k}") for k, label in ANALYSIS_TOGGLES.items()]
            items += [(f"{self.markets.icon(m)} {TITLES[m]}", f"mt:{m}:on:a") for m in ("portals", "mrkt", "tonnel")]
            await self.show(update, "📊 Анализ\n━━━━━━━━━━━━━━\n"
                            "Подписанные и редкие — участвуют ли такие подарки в поиске и уведомлениях.\n"
                            "Неполные данные — показывать ли в «Поиске» кандидатов без полного расчёта (с пометкой ⚠️). "
                            "В уведомления они не попадают никогда.\n"
                            "Нижний ряд — площадки в анализе (🔒 — сначала подключите доступ).",
                            Keyboard([[Button(label, callback_data=data)] for label, data in items[:3]] +
                                     [[Button(label, callback_data=data) for label, data in items[3:]]] +
                                     [[Button("⬅️ Назад", callback_data="settings")]]))
        elif cmd == "filters":
            await self.show(update, "🎯 Пороги и фильтры\n━━━━━━━━━━━━━━\n" + settings_text(settings),
                            keyboard([(FIELDS[key], f"edit:{key}") for key in FILTER_FIELDS], back="settings"))
        elif cmd == "more":
            await self.show(update, "🛠 Дополнительно\n━━━━━━━━━━━━━━\nДиагностика и редкие действия.",
                            keyboard([("📡 Статус", "status"), ("❔ Помощь", "help"), ("🆔 Мой ID", "id"), ("🗑 Удалить мои данные", "delete_me")], back="settings"))
        elif cmd == "markets":
            await self.markets.overview(update)
        elif cmd == "id":
            await self.show(update, f"🆔 Твой Telegram ID: {user}", keyboard([], back="more"))
        elif cmd == "alerts":
            values = {k: getattr(settings, k) for k in NOTIFY_TOGGLES}
            note = "" if settings.alerts else "\nПока выключено: фоновые карточки и сообщения об ошибках не приходят."
            await self.show(update, "🔔 Уведомления\n━━━━━━━━━━━━━━\n"
                            f"Фоновая проверка раз в {settings.interval_minutes} мин, до 3 карточек за раз.\n"
                            "Только новые лоты: ✅ — один раз на лот; ❌ — ещё раз, если цена снизилась.\n"
                            "Звук: ❌ — сообщения приходят беззвучно." + note,
                            Keyboard([[Button(f"{mark(values[k])} {label}", callback_data=f"nt:{k}")] for k, label in NOTIFY_TOGGLES.items()] +
                                     [[Button(f"⏱ Интервал: {settings.interval_minutes} мин", callback_data="edit:interval_minutes")],
                                      [Button("⬅️ Назад", callback_data="menu")]]))
        elif cmd == "togglealerts":
            settings.alerts = not settings.alerts
            self.store.save_settings(user, settings)
            await self.action("alerts", update, context)
        elif cmd == "scan":
            await self.scan(update, context, settings)
        elif cmd == "favorites":
            keys = self.store.marked("favorites", user)
            if not keys:
                await self.show(update, "⭐ Избранное\n━━━━━━━━━━━━━━\nИзбранное пусто. Сохраняйте лоты кнопкой ⭐ в карточке.", keyboard([], back="search"))
            for key in sorted(keys)[:20]:
                gift = self.store.listing(key)
                if gift:
                    await self.reply(update, f"{gift.name[:300]} · {gift.market} · {gift.price} {gift.currency} · {gift.status}",
                                     keyboard([("🎁 Карточка", f"compare:{key}"), ("🗑 Удалить", f"unfavorite:{key}")]))
        elif cmd == "fees":
            context.user_data["route"] = {}
            await self.show(update, "🧾 Расходы · шаг 1 из 3\n━━━━━━━━━━━━━━\nВыберите площадку покупки. Для каждой пары площадок и валюты расходы отдельные.",
                            keyboard([(m.title(), f"routebuy:{m}") for m in MARKETS], back="settings"))
        elif cmd == "currency":
            await self.show(update, "💱 Валюты\n━━━━━━━━━━━━━━\n"
                            f"Пороги: {settings.calculation_currency}. Показ: {', '.join(settings.display_currencies)}.\n"
                            "Конвертация только по сохранённому курсу с источником и временем, не старше часа.",
                            keyboard([("🎯 Валюта порогов", "edit:calculation_currency"), ("👁 Валюты показа", "edit:display_currencies"), ("✏️ Курс вручную", "ratebase")], back="settings"))
        elif cmd == "status":
            access = self.radar.source.access()
            lines = ["📡 Статус\n━━━━━━━━━━━━━━", f"Thermos: {access.icon} {access.label}",
                     f"Последнее получение данных: {self.store.last_sync()}",
                     f"Активных лотов: {len(self.store.listings())}; продаж за 30 дней: {len(self.store.sales(utcnow()))}."]
            for m in ("portals", "mrkt", "tonnel"):
                state, _, checked, _ = self.store.market_data(m)
                lines.append(f"{TITLES[m]}: данные — {state}" + (f", проверено {checked[:16].replace('T', ' ')} UTC" if checked else ""))
            await self.show(update, "\n".join(lines), keyboard([], back="more"))
        elif cmd == "delete_me":
            await self.show(update, "🗑 Удалить мои настройки, избранное и историю уведомлений? Ключ Thermos и данные площадок останутся.",
                            keyboard([("🗑 Удалить", "deleteconfirm")], back="more"))
        elif cmd == "deleteconfirm":
            self.store.delete_user(user)
            context.user_data.clear()
            await self.show(update, "✅ Ваши данные удалены. При следующей команде будут созданы настройки по умолчанию.", keyboard([], home=True))
        else:
            await self.reply(update, "Это действие из прежнего меню. Отправьте /start, чтобы открыть Gift Radar.", MENU)

    def home_text(self, settings):
        markets = " · ".join(f"{self.markets.icon(m)} {TITLES[m]}" for m in ("portals", "mrkt", "tonnel"))
        return ("🎁 Gift Radar\n━━━━━━━━━━━━━━\n"
                f"Площадки: {markets}\n"
                f"Уведомления: {mark(settings.alerts)} {'включены' if settings.alerts else 'выключены'}")

    async def scan(self, update, context, settings):
        user = update.effective_user.id
        now = utcnow()
        if user in self.last_scan and (now - self.last_scan[user]).total_seconds() < 30:
            await self.reply(update, "⏳ Повторный поиск доступен через 30 секунд.", keyboard([], back="search"))
            return
        self.last_scan[user] = now
        # One best target per gift; the compare button exposes the others.
        seen, chosen = set(), []
        for value in self.radar.analyses(user, now=now):
            key = listing_key(value.listing)
            if key in seen:
                continue
            seen.add(key)
            reason = rejection(value, settings, now)
            if reason in INCOMPLETE and not settings.show_incomplete:
                continue
            chosen.append((value, reason))
        if not chosen:
            _, reasons, analysed = self.radar.evaluate(user, now)
            lines = ["🔎 Поиск\n━━━━━━━━━━━━━━"]
            if not analysed:
                lines.append("Нет данных о лотах для анализа. Подключите площадки и обновите данные; автосбор через Thermos "
                             "станет доступен после публикации метода чтения каталога. Можно загрузить разрешённый экспорт "
                             "(Площадки → площадка → Дополнительно).")
            else:
                lines.append(f"Проверено лотов: {analysed}. Подходящих кандидатов нет.")
                lines += [f"• {REASONS[r]}: {n}" for r, n in reasons.most_common(4)]
                if reasons and set(reasons) <= INCOMPLETE and not settings.show_incomplete:
                    lines.append("Кандидаты с неполными данными скрыты: Настройки → Анализ.")
            await self.show(update, "\n".join(lines), keyboard([("🔄 Обновить данные", "refresh")], back="search"))
            return
        for value, reason in chosen[:3]:
            note = ("✅ Подходит под пороги уведомлений" if reason is None else
                    "⚠️ Неполные данные — не подтверждённая возможность" if reason in INCOMPLETE else
                    f"🔍 Кандидат: {REASONS[reason]}")
            await send_card(context.bot, user, value, settings, self.store, note=note)
        if len(chosen) > 3:
            await self.reply(update, f"Показаны 3 из {len(chosen)} кандидатов. Сузьте фильтры или используйте сравнение площадок.", keyboard([], back="search"))

    async def callback(self, update, context):
        if not private_admin(update):
            return
        query = update.callback_query
        await query.answer()
        if not private_admin(update):
            return
        data = query.data or ""
        cmd, _, value = data.partition(":")
        # Leaving any input screen cancels its draft; later text must not save it.
        context.user_data.pop("awaiting", None)
        try:
            await self.dispatch(cmd, value, update, context)
        except (ValueError, KeyError, IndexError, TypeError):
            # Buttons from old messages may carry formats this version no longer understands.
            await self.reply(update, "Эта кнопка устарела. Откройте актуальное меню.", MENU)

    async def dispatch(self, cmd, value, update, context):
        user = update.effective_user.id
        if await self.markets.callback(cmd, value, update, context):
            return
        if cmd == "nt":
            if value not in NOTIFY_TOGGLES:
                raise ValueError("Unknown toggle")
            settings = self.store.settings(user)
            setattr(settings, value, not getattr(settings, value))
            self.store.save_settings(user, settings)
            await self.action("alerts", update, context)
        elif cmd == "at":
            settings = self.store.settings(user)
            if value == "signed":
                # One underlying setting: the detailed choice stays in Пороги → Подпись.
                settings.signature = "any" if settings.signature == "no" else "no"
            elif value == "rare":
                settings.include_rare = not settings.include_rare
            elif value == "incomplete":
                settings.show_incomplete = not settings.show_incomplete
            else:
                raise ValueError("Unknown toggle")
            self.store.save_settings(user, settings)
            await self.action("analysis", update, context)
        elif cmd in ("favorite", "unfavorite", "hide", "compare", "evidence"):
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
                await self.show(update, f"{FIELDS.get(value, '⚙️ Значение')}\n━━━━━━━━━━━━━━\nВыберите значение:",
                                 keyboard([(label, f"set:{value}:{v}") for label, v in options[value]], back=settings_parent(value), home=True))
            elif value in ("buy_markets", "sell_markets", "display_currencies"):
                await self.list_choices(update, context, value)
            elif value in FIELDS:
                context.user_data["awaiting"] = ("setting", value)
                hint = ("Названия через запятую; - чтобы убрать ограничение." if value in ("collections", "models", "backdrops", "patterns", "symbols") else
                        "Введите число минут от 5 до 10080." if value == "interval_minutes" else "Введите число. Для скидки подписи - означает не задана.")
                await self.show(update, f"{FIELDS[value]}\n━━━━━━━━━━━━━━\n{hint}\n\n⬅️ Назад — отменить ввод.", input_navigation(context))
            else:
                raise ValueError("Unknown field")
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
            await self.show(update, "✅ Сохранено." + (" При смене валюты проверьте числовые пороги." if field == "calculation_currency" else ""),
                             keyboard([], back=settings_parent(field), home=True))
        elif cmd == "listtoggle":
            field, choice = value.split(":", 1)
            choices = CURRENCIES if field == "display_currencies" else MARKETS
            if field not in ("buy_markets", "sell_markets", "display_currencies") or choice not in choices:
                return
            settings = self.store.settings(user)
            old = getattr(settings, field)
            new = [x for x in old if x != choice] if choice in old else old + [choice]
            if not new and field == "display_currencies":
                await self.show(update, "Оставьте хотя бы одну валюту.", keyboard([], back=f"edit:{field}"))
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
            await self.show(update, f"✏️ {FEE_LABELS[value]}\n━━━━━━━━━━━━━━\nСумма в валюте маршрута или процент с %. - означает неизвестно.\n"
                             "0 вводите только если сбор действительно не применяется. Процент продажи/вывода от цены перепродажи, прочие проценты от цены покупки. Значение будет отмечено как пользовательское.", input_navigation(context))
        elif cmd == "ratebase":
            context.user_data.pop("rate_base", None)
            await self.show(update, "💱 Ручной курс · шаг 1 из 3\n━━━━━━━━━━━━━━\nИсходная валюта курса:",
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
            await self.show(update, "💱 Ручной курс · шаг 3 из 3\n━━━━━━━━━━━━━━\nВведите курс: число единиц целевой валюты за одну единицу исходной. Источник будет помечен «введён пользователем». Срок актуальности — один час.", input_navigation(context))
        else:
            await self.action(cmd, update, context)

    async def list_choices(self, update, context, field):
        chosen = getattr(self.store.settings(update.effective_user.id), field)
        options = CURRENCIES if field == "display_currencies" else MARKETS
        parent = settings_parent(field)
        await self.show(update, "✅ Выбор значений\n━━━━━━━━━━━━━━\nНажимайте, чтобы включить или выключить:",
                         keyboard([(mark(c in chosen) + " " + c, f"listtoggle:{field}:{c}") for c in options] + [("✅ Готово", parent)], back=parent))

    async def route_sell_menu(self, update, context):
        if "buy" not in context.user_data.get("route", {}):
            await self.action("fees", update, context)
            return
        await self.show(update, "🧾 Расходы · шаг 2 из 3\n━━━━━━━━━━━━━━\nВыберите площадку перепродажи:",
                         keyboard([(m.title(), f"routesell:{m}") for m in MARKETS], back="fees", home=True))

    async def route_currency_menu(self, update, context):
        if "sell" not in context.user_data.get("route", {}):
            await self.route_sell_menu(update, context)
            return
        await self.show(update, "🧾 Расходы · шаг 3 из 3\n━━━━━━━━━━━━━━\nВалюта расходов:",
                         keyboard([(c, f"routecurrency:{c}") for c in CURRENCIES], back="route_sell_menu", home=True))

    async def rate_quote_menu(self, update, context):
        base = context.user_data.get("rate_base")
        if base not in CURRENCIES:
            await self.reply(update, "Выберите исходную валюту заново.", keyboard([("💱 Выбрать валюту", "ratebase")], back="currency", home=True))
            return
        await self.show(update, f"💱 Ручной курс · шаг 2 из 3\n━━━━━━━━━━━━━━\nСколько единиц целевой валюты стоит 1 {base}? Выберите целевую валюту:",
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
        await self.show(update, "\n".join(lines), keyboard([("✏️ " + label, f"fee:{name}") for name, label in FEE_LABELS.items()], back="route_currency_menu", home=True))

    async def input(self, update, context):
        if not private_admin(update):
            return
        try:
            await self.process_input(update, context)
        finally:
            await erase_input(update)

    async def process_input(self, update, context):
        pending = context.user_data.get("awaiting")
        if pending and pending[0] == "source_key":
            await self.markets.input(update, context)
            return
        names = [self.store.connection(market).key_env for market in MARKETS]
        if contains_secret(update.effective_message.text, names):
            await self.reply(update, "🔒 Здесь секреты не принимаются. Ключ Thermos добавляется в «Площадки» → «Подключить Thermos».", input_navigation(context))
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

    async def notifier(user, message):
        if not is_admin(user):
            raise PermissionError("Administrator access required")
        await app.bot.send_message(chat_id=user, text=message, protect_content=True, disable_web_page_preview=True,
                                   disable_notification=not store.settings(user).sound,
                                   reply_markup=keyboard([("🏪 Площадки", "markets")]))

    async def work():
        while True:
            try:
                await radar.tick(sender, notifier)
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
    for name in ("start", "help", "settings", "markets", "scan", "search", "alerts", "favorites", "fees", "currency", "status", "cancel", "delete_me", "id"):
        app.add_handler(CommandHandler(name, ui.command))
    app.add_handler(CallbackQueryHandler(ui.callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ui.input))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, ui.document))
    app.add_error_handler(on_error)
    return app
