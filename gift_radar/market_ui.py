"""Actionable marketplace setup; never pretend a saved key is a working adapter."""
import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import CopyTextButton, InlineKeyboardButton as Button, InlineKeyboardMarkup as Keyboard

from .adapters import read_authorized_export
from .connections import ACCESS_REQUEST, MARKET_LINKS, documentation_url, is_operator, key_name
from .models import MARKETS
from .credentials import credential_value
from .security import private_admin, register_secret


class MarketInterface:
    def __init__(self, interface):
        from .telegram_ui import keyboard
        self.ui = interface
        self.store = interface.store
        self.keyboard = keyboard

    def nav(self, market):
        return self.keyboard([], back=f"market:{market}", home=True)

    async def owner_required(self, update):
        if is_operator(update.effective_user.id):
            return False
        await self.ui.reply(update, "🔒 Подключения и общие данные настраивает владелец бота.\n"
                            "Если ты владелец: узнай свой ID командой /id, добавь его в ADMIN_IDS на хостинге и перезапусти бота.",
                            self.keyboard([], back="markets", home=True))
        return True

    async def overview(self, update):
        lines = ["🔌 Подключение площадок\n━━━━━━━━━━━━━━"]
        for market in MARKETS:
            c = self.store.connection(market)
            _, detail = c.diagnosis()
            lines.append(f"\n🏪 {market.title()}\n{detail}")
        await self.ui.reply(update, "\n".join(lines) + "\n\n👇 Выбери площадку: настройка доступа, инструкция или загрузка данных.",
                            self.keyboard([(f"🔌 {m.title()}", f"market:{m}") for m in MARKETS], home=True))

    async def page(self, market, update):
        c = self.store.connection(market)
        state, detail = c.diagnosis()
        self.store.set_market(market, state, detail)
        owner = is_operator(update.effective_user.id)
        key_status = "🟢 сохранён, зашифрован" if c.has_saved_key else "🟢 задан на сервере" if c.credential_present else "🟡 имя сохранено, значение не задано" if c.key_env else "⚪ не настроен"
        lines = [f"🔌 {market.title()}\n━━━━━━━━━━━━━━", f"🔑 Ключ: {key_status}",
                 f"📚 Документация: {'ссылка сохранена' if c.docs_url else 'не добавлена'}",
                 f"\n{detail}"]
        if owner and c.key_env:
            lines.append(f"\nИмя переменной на хостинге: {c.key_env}")
        items = [("📖 Как получить доступ", f"market_how:{market}"), ("🧪 Проверить настройки", f"market_check:{market}")]
        if owner:
            items += [("🔑 Добавить / заменить ключ", f"market_key:{market}"), ("📚 Добавить документацию", f"market_docs:{market}"),
                      ("📎 Загрузить экспорт", f"market_import:{market}")]
            if c.has_saved_key or c.key_env or c.docs_url:
                items.append(("🔌 Сбросить подключение", f"market_disconnect:{market}"))
        else:
            items.append(("🔑 Настроить доступ", f"market_key:{market}"))
        markup = self.keyboard(items, back="markets", home=True)
        rows = [[Button("🌐 Открыть площадку", url=MARKET_LINKS[market]["open"])]]
        if market == "tonnel":
            rows.insert(0, [Button("🔑 Получить ключ в Thermos", url=MARKET_LINKS[market]["access"])])
        if c.docs_url:
            rows.append([Button("📚 Открыть документацию", url=c.docs_url)])
        elif market == "tonnel":
            rows.append([Button("📚 Документация Thermos API", url=MARKET_LINKS[market]["docs"])])
        await self.ui.reply(update, "\n".join(lines), Keyboard(rows + list(markup.inline_keyboard)))

    async def callback(self, cmd, market, update, context):
        if not private_admin(update):
            return True
        if cmd not in {"market", "market_how", "market_key", "market_env", "market_docs", "market_check", "market_import",
                       "market_disconnect", "market_disconnect_yes", "market_request"}:
            return False
        if market not in MARKETS:
            await self.ui.reply(update, "Площадка не найдена.", self.keyboard([], back="markets", home=True))
            return True
        if cmd in {"market_key", "market_env", "market_docs", "market_import", "market_disconnect", "market_disconnect_yes"} and await self.owner_required(update):
            return True
        if cmd == "market":
            await self.page(market, update)
        elif cmd == "market_how":
            if market == "tonnel":
                body = ("🔑 Получение ключа через Thermos\n━━━━━━━━━━━━━━\n"
                        "Thermos — агрегатор подарков команды swap.coffee. В официальном канале объявлен публичный API для всех.\n\n"
                        "1️⃣ Открой @thermos кнопкой ниже.\n"
                        "2️⃣ Отправь ему команду /api и получи свой ключ — обращаться в поддержку не требуется.\n"
                        "3️⃣ Вернись сюда, нажми «Добавить API-ключ» и отправь сам ключ в этом личном чате администратора.\n"
                        "4️⃣ Бот удалит сообщение и сохранит ключ в зашифрованном виде. Имя TONNEL_API_KEY вводить не нужно.\n\n"
                        "⚠️ По предупреждению Thermos ключ даёт полный доступ к аккаунту, включая переводы подарков и вывод TON. Не публикуй его, не отправляй другим ботам и не добавляй в Git.\n\n"
                        "Это ключ сервиса Thermos. Получение данных Tonnel через него нужно проверить по документации Thermos; автоматический сбор в нашем боте пока не реализован.")
                rows = [[Button("🔑 Открыть Thermos → /api", url=MARKET_LINKS[market]["access"])],
                        [Button("📚 Документация Thermos API", url=MARKET_LINKS[market]["docs"])],
                        [Button("📣 Официальное объявление", url="https://t.me/thermos_news")]]
            else:
                body = (f"📖 Как получить доступ · {market.title()}\n━━━━━━━━━━━━━━\n"
                        "1️⃣ Открой площадку и войди обычным способом. Если она предоставляет самостоятельную выдачу API-ключей, используй её раздел API/разработчика.\n\n"
                        "2️⃣ Проверь документацию: отдельный API-ключ, токен входа Mini App и подписанные данные Telegram — разные способы авторизации. Нужен тот способ, который поддерживает выбранный API.\n\n"
                        "3️⃣ Здесь нажми «Добавить API-ключ» и отправь сам ключ в личном чате администратора. Бот удалит сообщение и сохранит ключ зашифрованным.\n\n"
                        "4️⃣ Добавь ссылку на официальную документацию. Не публикуй секреты и не отправляй их другим пользователям или ботам.\n\n"
                        "Точный самостоятельный способ для этой площадки пока не подтверждён. Поддержка — дополнительный вариант, если нужна помощь; обязательного обращения нет. Сохранение доступа не включает сбор без реализованного формата API.")
                rows = [[Button("🌐 Открыть площадку", url=MARKET_LINKS[market]["open"])]]
                if "support" in MARKET_LINKS[market]:
                    rows.append([Button("💬 Помощь Portals (по желанию)", url=MARKET_LINKS[market]["support"])])
                rows.append([Button("📋 Запросить уточнение (по желанию)", copy_text=CopyTextButton(text=ACCESS_REQUEST))])
            nav = self.keyboard([("🔑 Добавить API-ключ", f"market_key:{market}"), ("📚 Добавить документацию", f"market_docs:{market}")], back=f"market:{market}", home=True)
            await self.ui.reply(update, body, Keyboard(rows + list(nav.inline_keyboard)))
        elif cmd == "market_key":
            context.user_data["awaiting"] = ("market_key", market)
            markup = self.keyboard([("⚙️ Использовать секрет хостинга", f"market_env:{market}")], back=f"market:{market}", home=True)
            await self.ui.reply(update, f"🔑 Добавление ключа · {market.title()}\n━━━━━━━━━━━━━━\n"
                                "Отправь сам API-ключ одним текстовым сообщением в этом личном чате. Имя переменной вводить не нужно.\n\n"
                                "🔒 Доступ только для администратора. Бот удалит сообщение перед сохранением и зашифрует ключ; в ответах его значение не отображается.\n\n"
                                "↩️ «Назад» отменит ввод.", markup)
        elif cmd == "market_env":
            context.user_data["awaiting"] = ("market_env", market)
            await self.ui.reply(update, f"⚙️ Секрет хостинга · {market.title()}\n━━━━━━━━━━━━━━\n"
                                f"Этот вариант — для уже заданного секрета на хостинге. Пришли только имя переменной, например {market.upper()}_API_KEY.\n\n"
                                "Чтобы отправить сам ключ, вернись к кнопке «Добавить / заменить ключ».", self.nav(market))
        elif cmd == "market_docs":
            context.user_data["awaiting"] = ("market_docs", market)
            await self.ui.reply(update, "📚 Пришли публичную HTTPS-ссылку на официальную документацию API этой площадки, без ключей и параметров авторизации.\n\n"
                                "Ссылка будет сохранена для проверки контракта. Это не API endpoint; бот не отправляет на неё ключ.", self.nav(market))
        elif cmd == "market_check":
            c = self.store.connection(market)
            state, detail = c.diagnosis()
            self.store.set_market(market, state, detail)
            await self.ui.reply(update, f"🧪 Проверка настроек · {market.title()}\n━━━━━━━━━━━━━━\n"
                                f"Ключ на сервере: {'задан' if c.credential_present else 'не задан'}\n"
                                f"Документация: {'ссылка сохранена' if c.docs_url else 'не добавлена'}\n\n{detail}\n\n"
                                "Проверено наличие настроек. Запрос к API не выполнялся: официальный формат пока не реализован.", self.nav(market))
        elif cmd == "market_import":
            context.user_data["awaiting"] = ("market_export", market)
            nav = self.nav(market)
            markup = Keyboard([[Button("📄 Формат экспорта", url="https://github.com/nalpax/my-telegram-bot1/blob/main/docs/EXPORT.md")]] + list(nav.inline_keyboard))
            await self.ui.reply(update, f"📎 Загрузка данных · {market.title()}\n━━━━━━━━━━━━━━\n"
                                "Пришли JSON-файл разрешённого экспорта в формате нашего бота (docs/EXPORT.md в GitHub). До 10 MiB.\n\n"
                                "Отправляя файл, ты подтверждаешь право использовать данные. Только реальные лоты и подтверждённые сделки выбранной площадки. Секреты и тестовые данные не загружай.\n\n"
                                "Файл проверяется целиком; при ошибке данные не записываются.", markup)
        elif cmd == "market_disconnect":
            await self.ui.reply(update, f"Удалить сохранённый ключ, привязку и ссылку на документацию {market.title()}? Загруженные лоты, продажи и отдельно заданный секрет на хостинге сохранятся.",
                                self.keyboard([("🔌 Сбросить", f"market_disconnect_yes:{market}")], back=f"market:{market}", home=True))
        elif cmd == "market_disconnect_yes":
            self.store.disconnect(market)
            await self.page(market, update)
        elif cmd == "market_request":
            await self.ui.reply(update, ACCESS_REQUEST, self.nav(market))
        return True

    async def input(self, update, context):
        if not private_admin(update):
            return True
        pending = context.user_data.get("awaiting")
        if not pending or pending[0] not in ("market_key", "market_env", "market_docs", "market_export"):
            return False
        kind, market = pending
        if await self.owner_required(update):
            context.user_data.pop("awaiting", None)
            return True
        if kind == "market_export":
            await self.ui.reply(update, "Пришли экспорт файлом .json, а не текстом сообщения.", self.nav(market))
            return True
        raw = update.effective_message.text.strip()
        if kind == "market_key":
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
                await self.ui.reply(update, "⚠️ Не удалось удалить сообщение. Ключ не сохранён. Удали сообщение вручную и повтори ввод или нажми «Назад».", self.nav(market))
                return True
            if not private_admin(update):
                context.user_data.pop("awaiting", None)
                return True
            if value is None:
                await self.ui.reply(update, "⚠️ Нужен сам API-ключ одним сообщением (8–4096 символов), без переносов строк. Имя переменной и токен Telegram-бота не подходят. Сообщение удалено; повтори ввод или нажми «Назад».", self.nav(market))
                return True
            try:
                self.store.save_credential(market, value)
            except ValueError:
                await self.ui.reply(update, "⚠️ Ключ не сохранён. Проверь доступ к файлу шифрования на хостинге; если он потерян, восстанови его из резервной копии. Сообщение удалено. Повтори ввод или нажми «Назад».", self.nav(market))
                return True
            context.user_data.pop("awaiting", None)
            await self.ui.reply(update, "✅ Ключ сохранён в зашифрованном виде. Сообщение с ключом удалено.\n\nСохранение ключа не проверяет доступ к API; автоматический сбор пока не реализован.", self.nav(market))
            return True
        try:
            c = self.store.connection(market)
            if kind == "market_env":
                c = replace(c, key_env=key_name(market, raw))
            else:
                c = replace(c, docs_url=documentation_url(raw))
            self.store.save_connection(c)
        except ValueError:
            # An accidentally pasted credential should not remain in the conversation.
            try:
                await update.effective_message.delete()
            except Exception:
                pass
            await self.ui.reply(update, "⚠️ Нужны имя переменной выбранной площадки или публичная ссылка на документацию. Секретные значения не сохраняются. Повтори ввод или нажми «Назад».", self.nav(market))
            return True
        context.user_data.pop("awaiting", None)
        await self.ui.reply(update, "✅ Настройка сохранена. Проверка наличия ключа доступна в карточке площадки.", self.nav(market))
        return True

    async def document(self, update, context):
        pending = context.user_data.get("awaiting")
        if not pending or pending[0] != "market_export":
            await self.ui.reply(update, "Для загрузки выбери площадку в «Площадки» → «Загрузить экспорт».", self.keyboard([], back="markets", home=True))
            return
        _, market = pending
        if await self.owner_required(update):
            context.user_data.pop("awaiting", None)
            return
        document = update.effective_message.document
        if not document.file_name or not document.file_name.lower().endswith(".json") or document.file_size is None or document.file_size > 10 * 1024 * 1024:
            await self.ui.reply(update, "Нужен JSON-файл размером до 10 MiB.", self.nav(market))
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
            await self.ui.reply(update, "⚠️ Файл не импортирован. Проверь схему docs/EXPORT.md, площадку, даты и подтверждение сделок. При сетевой ошибке повтори загрузку позже.", self.nav(market))
            return
        context.user_data.pop("awaiting", None)
        await self.ui.reply(update, f"✅ Импортировано записей: лоты {len(listings)}, завершённые продажи {len(sales)}.\n"
                            "Открой «Аналитику». API площадки автоматически не подключён.", self.nav(market))
