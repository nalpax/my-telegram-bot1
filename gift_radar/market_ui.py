"""Marketplace screens: one Thermos connection, per-marketplace toggles, honest key/access/data states."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard

from .adapters import read_authorized_export
from .analytics import INCOMPLETE, REASONS
from .connections import is_operator
from .credentials import credential_value
from .models import MARKETS, utcnow
from .security import private_admin, register_secret
from .sources import LINKS, MARKET_LINKS, MISSING_CONTRACT, SHORT, TITLES
from .storage import listing_key

ORDER = ("portals", "mrkt", "tonnel")


def mark(on):
    return "✅" if on else "❌"


def moment(value):
    return value[:16].replace("T", " ") + " UTC" if value else "—"


class MarketInterface:
    def __init__(self, interface):
        from .telegram_ui import keyboard, send_card
        self.ui = interface
        self.store = interface.store
        self.radar = interface.radar
        self.source = interface.radar.source
        self.keyboard = keyboard
        self.send_card = send_card
        self.last_load = {}

    def nav(self, back):
        return self.keyboard([], back=back)

    # ---- state shown on buttons -------------------------------------------------
    def icon(self, market):
        if self.source.checking:
            return "⏳"
        if not self.radar.available(market):
            return "⚠️" if self.source.access().state not in ("missing", "saved") else "🔒"
        if self.store.market_flag(market) is False:
            return "❌"
        return "⚠️" if self.store.market_data(market)[0] == "unavailable" else "✅"

    def data_line(self, market):
        state, _, checked, data_at = self.store.market_data(market)
        if state == "ready":
            return f"✅ получены {moment(data_at)}"
        if state == "no_contract":
            return "🔒 автосбор недоступен — " + MISSING_CONTRACT
        if state == "unavailable":
            return f"⚠️ ошибка загрузки {moment(checked)}; данные не обновлены" + (f" (последние получены {moment(data_at)})" if data_at else "")
        if state in SHORT:
            return "— не загружались: нет доступа"
        return "— ещё не загружались"

    # ---- screens ----------------------------------------------------------------
    async def overview(self, update):
        access = self.source.access()
        lines = ["🏪 Площадки\n━━━━━━━━━━━━━━", f"🔑 Thermos: {access.icon} {access.label}",
                 "Один ключ Thermos открывает Portals, MRKT и Tonnel.", ""]
        for m in ORDER:
            icon = self.icon(m)
            state = ("в анализе" if icon == "✅" else "выключена" if icon == "❌" else
                     "нужен доступ" if icon == "🔒" else "проверка…" if icon == "⏳" else "требует внимания")
            data = self.store.market_data(m)[0]
            if icon in ("✅", "❌") and data != "ready":
                # Participation is not data: say plainly when nothing is being collected.
                state += " · автосбор недоступен" if data == "no_contract" else " · данные не загружены"
            lines.append(f"{icon} {TITLES[m]} — {state}")
        lines.append("\nНажми на площадку, чтобы открыть её настройки.")
        items = [(f"{self.icon(m)} {TITLES[m]}", f"market:{m}") for m in ORDER]
        items.append(("🔑 Доступ Thermos" if access.key_saved else "🔑 Подключить Thermos", "source"))
        await self.ui.show(update, "\n".join(lines), self.keyboard(items, width=3, back="menu"))

    async def page(self, market, update, notice=""):
        access = self.source.access()
        settings = self.store.settings(update.effective_user.id)
        listings, sales = self.store.market_counts(market)
        available = self.radar.available(market)
        enabled = self.store.market_flag(market) is not False
        lines = [f"🏪 {TITLES[market]}\n━━━━━━━━━━━━━━",
                 f"Доступ: {access.icon} {access.label} (через Thermos)",
                 f"Данные: {self.data_line(market)}",
                 f"В базе: лотов {listings} · продаж за 30 дн. {sales}"]
        if notice:
            lines.append("\n" + notice)
        if available:
            rows = [[Button(f"{mark(enabled)} Участвует в анализе", callback_data=f"mt:{market}:on:c")],
                    [Button(f"{mark(market in settings.buy_markets)} Поиск покупок", callback_data=f"mt:{market}:buy:c"),
                     Button(f"{mark(market in settings.sell_markets)} Поиск перепродажи", callback_data=f"mt:{market}:sell:c")]]
        else:
            # Locked toggles lead to the connection path instead of a fake ✅.
            rows = [[Button("🔒 Участвует в анализе", callback_data="source")],
                    [Button("🔒 Поиск покупок", callback_data="source"), Button("🔒 Поиск перепродажи", callback_data="source")]]
        rows.append([Button("🔑 Заменить доступ" if access.key_saved else "🔑 Подключить", callback_data="source_key" if access.key_saved else "source")])
        rows.append([Button("⚙️ Дополнительно", callback_data=f"market_more:{market}")])
        rows.append([Button("⬅️ Назад", callback_data="markets")])
        await self.ui.show(update, "\n".join(lines), Keyboard(rows))

    async def more(self, market, update):
        rows = []
        if self.source.key_saved():
            rows.append([Button("🔁 Проверить доступ", callback_data="source_check")])
        rows.append([Button("📎 Загрузить экспорт", callback_data=f"market_import:{market}")])
        legacy = market != "tonnel" and (self.store.has_credential(market) or self.store.connection(market).key_env)
        if legacy:
            rows.append([Button(f"🗑 Удалить старый ключ {TITLES[market]}", callback_data=f"market_disconnect:{market}")])
        rows.append([Button("🌐 Открыть площадку", url=MARKET_LINKS[market])])
        rows.append([Button("⬅️ Назад", callback_data=f"market:{market}")])
        text = (f"⚙️ {TITLES[market]} · дополнительно\n━━━━━━━━━━━━━━\n"
                "Экспорт — JSON с реальными лотами и подтверждёнными продажами в формате бота (docs/EXPORT.md).")
        if legacy:
            text += f"\nСохранён старый ключ {TITLES[market]}: официальный способ его проверки не подтверждён, бот его не использует."
        await self.ui.show(update, text, Keyboard(rows))

    async def source_page(self, update, notice=""):
        access = self.source.access()
        lines = ["🔑 Thermos\n━━━━━━━━━━━━━━", "Агрегатор подарков swap.coffee. Один ключ — для Portals, MRKT и Tonnel.",
                 f"Ключ: {'сохранён, зашифрован' if access.key_saved else '🔒 не добавлен'}",
                 f"Доступ: {access.icon} {access.label}"]
        if access.state not in ("verified", "missing", "saved"):
            lines.append(access.detail)
        if notice:
            lines.append("\n" + notice)
        if access.key_saved:
            items = [("🔁 Проверить доступ", "source_check"), ("🔑 Заменить ключ", "source_key"), ("🗑 Удалить доступ", "source_delete")]
        else:
            items = [("🔑 Подключить", "source_key"), ("📖 Как получить ключ", "source_how")]
        items.append(("📄 Описание API Thermos", "source_spec"))
        await self.ui.show(update, "\n".join(lines), self.keyboard(items, width=1, back="markets"))

    async def ask_key(self, update, context):
        context.user_data["awaiting"] = ("source_key", "thermos")
        rows = [[Button("🤖 Открыть @thermos", url=LINKS["bot"])], [Button("📖 Как получить ключ", callback_data="source_how")],
                [Button("⬅️ Отмена", callback_data="source")]]
        await self.ui.show(update, "🔑 Подключение Thermos\n━━━━━━━━━━━━━━\n"
                           "1. В @thermos отправь команду /api.\n"
                           "2. Отправь сам API-ключ сюда одним сообщением.\n\n"
                           "Бот удалит сообщение, сохранит ключ зашифрованным, проверит доступ и сразу загрузит первые данные. "
                           "Переменные на хостинге не нужны. «Отмена» прекращает ввод.", Keyboard(rows))

    async def how(self, update):
        rows = [[Button("🤖 Открыть @thermos", url=LINKS["bot"])], [Button("📚 Документация Thermos API", url=LINKS["docs"])],
                [Button("📣 Объявление Thermos /API", url=LINKS["news"])],
                [Button("🔑 Ввести ключ", callback_data="source_key")], [Button("⬅️ Назад", callback_data="source")]]
        await self.ui.show(update, "📖 Как получить ключ Thermos\n━━━━━━━━━━━━━━\n"
                           "Thermos выдаёт ключ сам, без поддержки: открой @thermos и отправь /api. "
                           "Повторная команда возвращает тот же ключ.\n\n"
                           "⚠️ Ключ даёт полный доступ к аккаунту Thermos, включая переводы подарков и вывод TON. "
                           "Не публикуй его и не пересылай. Gift Radar использует ключ только для проверки авторизации "
                           "и не выполняет торговых операций.", Keyboard(rows))

    # ---- actions ----------------------------------------------------------------
    async def callback(self, cmd, value, update, context):
        if not private_admin(update):
            return True
        if cmd == "markets":
            await self.overview(update)
        elif cmd == "source":
            await self.source_page(update)
        elif cmd in ("source_key", "market_key", "market_env"):
            await self.ask_key(update, context)
        elif cmd in ("source_how", "market_how"):
            await self.how(update)
        elif cmd in ("source_check", "market_check"):
            await self.check_flow(update)
        elif cmd == "source_delete":
            await self.ui.show(update, "🗑 Удалить сохранённый ключ Thermos?\n\nНастройки площадок и уже полученные данные останутся. "
                               "Ключ перестанет использоваться ботом; отозвать его у Thermos нужно через сам Thermos.",
                               self.keyboard([("🗑 Удалить ключ", "source_delete_yes")], back="source"))
        elif cmd == "source_spec":
            await self.spec_flow(update, context)
        elif cmd == "source_delete_yes":
            self.source.delete_key()
            await self.source_page(update, "Ключ удалён.")
        elif cmd == "mt":
            await self.toggle(value, update, context)
        elif cmd in ("market", "market_more", "market_import", "market_docs", "market_request", "market_disconnect", "market_disconnect_yes"):
            if value not in MARKETS:
                await self.ui.show(update, "Площадка не найдена.", self.nav("markets"))
            elif cmd == "market_more":
                await self.more(value, update)
            elif cmd == "market_import":
                await self.ask_export(value, update, context)
            elif cmd == "market_disconnect":
                if value == "tonnel":
                    return await self.callback("source_delete", "", update, context)
                await self.ui.show(update, f"Удалить старый ключ и привязку {TITLES[value]}? Ключ Thermos и данные останутся.",
                                   self.keyboard([("🗑 Удалить", f"market_disconnect_yes:{value}")], back=f"market_more:{value}"))
            elif cmd == "market_disconnect_yes":
                if value == "tonnel":
                    return await self.callback("source_delete", "", update, context)
                self.store.disconnect(value)
                await self.page(value, update, "Старый ключ удалён.")
            else:
                await self.page(value, update)
        else:
            return False
        return True

    async def toggle(self, value, update, context):
        market, what, back = value.split(":")
        if market not in MARKETS or what not in ("on", "buy", "sell") or back not in ("a", "c"):
            raise ValueError("Unknown toggle")
        if not self.radar.available(market):
            await self.source_page(update, f"🔒 {TITLES[market]} пока не подключена. Добавь ключ Thermos — переключатели станут доступны.")
            return
        user = update.effective_user.id
        notice = ""
        if what == "on":
            enable = self.store.market_flag(market) is False
            self.store.set_market_flag(market, enable)
            if enable and self.source.key_saved():
                # Re-enabling reuses the saved key and refreshes authorization only when needed.
                access = await self.source.ensure_access()
                if not private_admin(update):
                    return
                if not access.usable:
                    notice = f"⚠️ Площадка включена, но доступ Thermos не подтверждён: {access.label}."
        else:
            settings = self.store.settings(user)
            field = "buy_markets" if what == "buy" else "sell_markets"
            chosen = getattr(settings, field)
            setattr(settings, field, [m for m in chosen if m != market] if market in chosen else chosen + [market])
            self.store.save_settings(user, settings)
        if back == "a":
            await self.ui.action("analysis", update, context)
        else:
            await self.page(market, update, notice)

    async def spec_flow(self, update, context):
        """Public OpenAPI file for the developer; the key is never sent."""
        from .spec import INTERESTING, download_spec
        await self.ui.show(update, "⏳ Скачиваю публичное описание API Thermos…", None)
        result = await download_spec()
        if not private_admin(update):
            return
        if result.state != "ok":
            await self.ui.reply(update, f"⚠️ {result.message}", self.keyboard([("🔁 Повторить", "source_spec")], back="source"))
            return
        found = [p for p in result.paths if any(word in p.lower() for word in INTERESTING)]
        caption = (f"📄 Описание API Thermos: {len(result.paths)} методов.\n"
                   "Перешли этот файл разработчику — по нему подключается сбор лотов. Ключа в файле нет.")
        await context.bot.send_document(chat_id=update.effective_user.id, document=result.content,
                                        filename="thermos-openapi.json", caption=caption,
                                        protect_content=False)  # Public, key-free file the admin must be able to forward.
        lines = ["Методы, похожие на каталог, рынок и историю продаж:"] + [f"• {p}" for p in found[:30]] if found else \
                ["Методов со словами catalog/market/search/history в описании не найдено."]
        await self.ui.reply(update, "\n".join(lines)[:3800], self.nav("source"))

    async def account_summary(self):
        """Real data from the Thermos account; returns text lines, never raises."""
        from .sources import ThermosError
        try:
            lots = await self.source.my_lots()
            sales = await self.source.my_sales()
        except ThermosError as exc:
            return [f"Аккаунт Thermos: ⚠️ данные не получены — {exc.text}."]
        on_sale = [x for x in lots if x["status"] in ("LISTED", "AWAITING_PRICE_CHANGE", "AWAITING_MARKET_EXPORT", "AWAITING_IMPORT")]
        return [f"Аккаунт Thermos: ✅ данные получены — лотов на продаже {len(on_sale)}, продаж в истории {len(sales)}."]

    async def my_lots(self, update):
        from .sources import ThermosError
        if not self.source.key_saved():
            await self.source_page(update, "🔒 Сначала подключи Thermos — тогда здесь появятся твои лоты и продажи.")
            return
        await self.ui.show(update, "⏳ Загружаю твои лоты из Thermos…", None)
        try:
            lots = await self.source.my_lots()
            sales = await self.source.my_sales()
        except ThermosError as exc:
            if private_admin(update):
                await self.ui.reply(update, f"⚠️ Не удалось загрузить: {exc.text}.", self.keyboard([("🔁 Повторить", "mylots")], back="search"))
            return
        if not private_admin(update):
            return
        def money(value):
            return f"{value.normalize():f} TON" if value is not None else "цена неизвестна"
        lines = ["📦 Мои лоты Thermos\n━━━━━━━━━━━━━━"]
        if lots:
            for x in lots[:25]:
                status = {"LISTED": "на продаже", "SOLD": "продан", "FILLED": "исполнен"}.get(x["status"], x["status"].lower())
                lines.append(f"• {x['title']} — {money(x['price'])} · {x['market']} · {status}")
        else:
            lines.append("Лотов на продаже нет.")
        if sales:
            lines.append("\nПоследние продажи:")
            lines += [f"• {s['title']} — {money(s['amount'])} · {s['market']} · {s['at']}" for s in sales[-5:][::-1]]
        lines.append("\nО новых продажах бот сообщает сам, если включены «🔔 Автоуведомления».")
        await self.ui.reply(update, "\n".join(lines)[:3900], self.keyboard([("🔄 Обновить", "mylots")], back="search"))

    async def check_flow(self, update):
        if not private_admin(update):
            return
        if not self.source.key_saved():
            await self.source_page(update)
            return
        await self.ui.show(update, "⏳ Проверяю доступ в Thermos…", None)
        result = await self.source.verify()
        if not private_admin(update):
            return
        items = [("📦 Загрузить данные", "refresh")] if result.state == "verified" else [("🔁 Проверить снова", "source_check"), ("🔑 Ввести другой ключ", "source_key")]
        await self.ui.reply(update, f"🧪 Проверка ключа · Thermos\n━━━━━━━━━━━━━━\n{result.message}", self.keyboard(items, width=1, back="markets"))

    async def load_flow(self, update, context):
        """First load: runs right away after connection or on «Обновить данные»."""
        user = update.effective_user.id
        now = utcnow()
        if user in self.last_load and (now - self.last_load[user]).total_seconds() < 30:
            await self.ui.reply(update, "⏳ Данные только что обновлялись. Повтори через 30 секунд.", self.nav("search"))
            return
        self.last_load[user] = now
        status = await self.ui.reply(update, "⏳ Загружаю первые данные…")
        report = await self.radar.first_load(user)
        if not private_admin(update):
            return
        body = self.report_text(user, report)
        if self.source.access().usable:
            body += "\n\n" + "\n".join(await self.account_summary())
            if not private_admin(update):
                return
        markup = self.keyboard([("🔎 Поиск", "search"), ("🔔 Уведомления", "alerts"), ("🏪 Площадки", "markets")], width=3)
        try:
            await status.edit_text(body, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            await self.ui.reply(update, body, markup)
        settings = self.store.settings(user)
        for value in report.best:
            key = listing_key(value.listing)
            # Mark as notified so the background cycle does not resend the same card.
            if not self.store.claim_alert(user, key, utcnow(), value.listing.price):
                continue
            if not private_admin(update):
                self.store.finish_alert(user, key, "access_revoked")
                return
            try:
                await self.send_card(context.bot, user, value, settings, self.store, note="✅ Подходящее предложение")
            except Exception:
                self.store.finish_alert(user, key, "unknown_delivery")
                raise
            self.store.finish_alert(user, key, "sent")

    def report_text(self, user, report):
        settings = self.store.settings(user)
        received = any(r.state == "ready" for r in report.results.values())
        lines = ["📦 Первая загрузка" + ("" if received else ": новые данные не получены"), "━━━━━━━━━━━━━━"]
        groups = {}
        for m in ORDER:
            r = report.results.get(m)
            if r is None:
                continue
            if r.state == "ready":
                status = f"✅ получено лотов {r.listings}, продаж {r.sales}"
            elif r.state == "no_contract":
                status = "🔒 доступ есть, но автосбор недоступен (нет документированного метода чтения каталога)"
            elif r.state == "off":
                status = "❌ выключена — не проверялась"
            elif r.state == "unavailable":
                status = "⚠️ ошибка загрузки, данные не обновлены"
            else:
                status = f"⚠️ нет доступа: {SHORT.get(r.state, r.state)}"
            groups.setdefault(status, []).append(TITLES[m])
        lines += [f"{', '.join(names)}: {status}" for status, names in groups.items()]
        with_data = [m for m in MARKETS if report.counts[m] != (0, 0)]
        listings = sum(c[0] for c in report.counts.values())
        sales = sum(c[1] for c in report.counts.values())
        lines.append(f"В базе для анализа: лотов {listings}, продаж за 30 дн. {sales}.")
        lines.append("")
        if report.best:
            lines.append(f"Подходящих предложений: {len(report.best)} — карточки ниже.")
        else:
            lines.append("Подходящих предложений нет.")
            lines.append("Почему:")
            if not report.analysed:
                lines.append("• нет данных о лотах: автосбор через Thermos ждёт документированного метода каталога, экспорт не загружен")
            for reason, count in report.reasons.most_common(4):
                lines.append(f"• {REASONS[reason]} — {count}")
            lines.append("Что можно сделать:")
            if not report.analysed:
                lines.append("• загрузить разрешённый экспорт: Площадки → площадка → ⚙️ Дополнительно")
            elif set(report.reasons) & INCOMPLETE:
                lines.append("• задать расходы маршрутов и проверить «Минимум аналогов» в ⚙️ Настройках")
            else:
                lines.append("• снизить пороги прибыли/ROI или расширить фильтры в ⚙️ Настройках")
        if len(with_data) == 1:
            lines.append(f"Данные есть только по {TITLES[with_data[0]]}: межплощадочная прибыль не рассчитывается. Добавьте данные второй площадки.")
        lines.append(f"\nФоновый мониторинг: {mark(settings.alerts)} {'включён' if settings.alerts else 'выключен — включается в «🔔 Уведомления»'}.")
        return "\n".join(lines)

    async def ask_export(self, market, update, context):
        context.user_data["awaiting"] = ("market_export", market)
        rows = [[Button("📄 Формат экспорта", url="https://github.com/nalpax/my-telegram-bot1/blob/main/docs/EXPORT.md")],
                [Button("⬅️ Назад", callback_data=f"market_more:{market}")]]
        await self.ui.show(update, f"📎 Загрузка данных · {TITLES[market]}\n━━━━━━━━━━━━━━\n"
                           "Пришли JSON-файл разрешённого экспорта (до 10 MiB). Отправляя файл, ты подтверждаешь право использовать данные. "
                           "Файл проверяется целиком; при ошибке ничего не записывается.", Keyboard(rows))

    # ---- text and files ------------------------------------------------------------
    async def input(self, update, context):
        if not private_admin(update):
            return True
        pending = context.user_data.get("awaiting")
        if not pending or pending[0] not in ("source_key", "market_export"):
            return False
        if pending[0] == "market_export":
            await self.ui.reply(update, "Пришли экспорт файлом .json, а не текстом сообщения.", self.nav(f"market_more:{pending[1]}"))
            return True
        raw = update.effective_message.text.strip()
        # Delete before writing anything; failed deletion must not save a key.
        try:
            value = credential_value(raw)
        except ValueError:
            value = None
        if value is not None:
            register_secret(value)
        try:
            if not await update.effective_message.delete():
                raise ValueError("Message deletion unsuccessful")
        except Exception:
            await self.ui.reply(update, "⚠️ Не удалось удалить сообщение. Ключ не сохранён. Удали сообщение вручную и повтори ввод или нажми «Отмена».", self.nav("source"))
            return True
        if not private_admin(update):
            context.user_data.pop("awaiting", None)
            return True
        if value is None:
            await self.ui.reply(update, "⚠️ Нужен сам API-ключ одним сообщением (8–4096 символов), без переносов строк. Имя переменной и токен Telegram-бота не подходят. Сообщение удалено; повтори ввод или нажми «Отмена».", self.nav("source"))
            return True
        try:
            self.source.save_key(value)
        except ValueError:
            await self.ui.reply(update, "⚠️ Ключ не сохранён. Проверь доступ к файлу шифрования на хостинге; если он потерян, восстанови его из резервной копии. Сообщение удалено.", self.nav("source"))
            return True
        context.user_data.pop("awaiting", None)
        saved = "🔒 Ключ сохранён в зашифрованном виде. Сообщение с ключом удалено."
        status = await self.ui.reply(update, saved + "\n⏳ Проверяю доступ в Thermos…")
        result = await self.source.verify()
        if not private_admin(update):
            return True
        try:
            await status.edit_text(f"{saved}\n{result.message}")
        except Exception:
            await self.ui.reply(update, result.message)
        if result.state == "verified":
            await self.load_flow(update, context)
        else:
            await self.ui.reply(update, "Подключение не завершено. Ключ сохранён; можно повторить проверку или ввести другой ключ.",
                                self.keyboard([("🔁 Проверить снова", "source_check"), ("🔑 Ввести другой ключ", "source_key")], width=1, back="markets"))
        return True

    async def document(self, update, context):
        pending = context.user_data.get("awaiting")
        if not pending or pending[0] != "market_export":
            await self.ui.reply(update, "Для загрузки выбери «Площадки» → площадка → «⚙️ Дополнительно» → «Загрузить экспорт».", self.nav("markets"))
            return
        _, market = pending
        if not is_operator(update.effective_user.id):
            context.user_data.pop("awaiting", None)
            return
        document = update.effective_message.document
        back = self.nav(f"market:{market}")
        if not document.file_name or not document.file_name.lower().endswith(".json") or document.file_size is None or document.file_size > 10 * 1024 * 1024:
            await self.ui.reply(update, "Нужен JSON-файл размером до 10 MiB.", back)
            return
        try:
            with TemporaryDirectory(prefix="gift-radar-export-") as temp:
                path = Path(temp) / "export.json"
                remote = await asyncio.wait_for(context.bot.get_file(document.file_id), timeout=20)
                await asyncio.wait_for(remote.download_to_drive(path), timeout=30)
                if not is_operator(update.effective_user.id):
                    raise PermissionError("Administrator access revoked")
                listings, sales = read_authorized_export(str(path), True, expected_market=market)
                self.store.ingest(listings, sales)
        except Exception as exc:
            self.store.error(market, type(exc).__name__)
            await self.ui.reply(update, "⚠️ Файл не импортирован. Проверь схему docs/EXPORT.md, площадку, даты и подтверждение сделок. При сетевой ошибке повтори загрузку позже.", back)
            return
        context.user_data.pop("awaiting", None)
        await self.ui.reply(update, f"✅ Импортировано записей: лоты {len(listings)}, завершённые продажи {len(sales)}.\n"
                            "Открой «🔎 Поиск».", back)
