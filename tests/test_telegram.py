"""End-to-end handlers against a synthetic Telegram transport; NO network calls."""
import asyncio
import json

from telegram import Update
from telegram.request import BaseRequest
import pytest

from gift_radar.adapters import registry
from gift_radar.service import Radar
from gift_radar.storage import Store
from gift_radar.telegram_ui import build_app


class FakeTelegram(BaseRequest):
    def __init__(self):
        self.calls = []

    @property
    def read_timeout(self):
        return 20

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((name, params))
        if name == "getMe":
            result = {"id": 123, "is_bot": True, "first_name": "TEST", "username": "test_bot"}
        elif name == "sendMessage":
            result = {"message_id": len(self.calls), "date": 1, "chat": {"id": params["chat_id"], "type": "private"}, "text": params["text"]}
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


def command(app, message, user=1):
    return Update.de_json({"update_id": 1, "message": {"message_id": 1, "date": 1, "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "TEST"}, "text": message,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(message)}]}}, app.bot)


def callback(app, data, user=1):
    return Update.de_json({"update_id": 2, "callback_query": {"id": "test-callback", "chat_instance": "test-instance",
             "from": {"id": user, "is_bot": False, "first_name": "TEST"}, "data": data,
             "message": {"message_id": 1, "date": 1, "chat": {"id": user, "type": "private"}, "text": "TEST menu"}}}, app.bot)


def text_input(app, raw, user=1):
    return Update.de_json({"update_id": 3, "message": {"message_id": 2, "date": 1, "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": "TEST"}, "text": raw}}, app.bot)


def test_commands_empty_source_and_settings():
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            for cmd in ("/start", "/help", "/settings", "/markets", "/scan", "/alerts", "/favorites", "/fees", "/currency", "/status"):
                await app.process_update(command(app, cmd))
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert any("Нет активных данных" in t for t in texts)
        assert any("Официальный разрешённый API" in t for t in texts)
        assert any("Минимальная чистая прибыль" in t for t in texts)
        assert not store.settings(1).alerts
        assert not any("Временная ошибка" in t for t in texts)
        assert any("remove_keyboard" in str(p.get("reply_markup")) for name,p in request.calls if name == "sendMessage")
    asyncio.run(run())


def test_telegram_scan_renders_real_analytics_fixture(gift, sales, settings, route):
    async def run():
        store = Store(":memory:")
        store.ingest([gift], sales)
        settings.sell_markets = ["tonnel"]
        store.save_settings(1, settings)
        store.save_route(1, route)
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(command(app, "/scan"))
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert any("Завершённые продажи" in t for t in texts)
        assert any("TEST collection" in t for t in texts)
        assert not any("Временная ошибка" in t for t in texts)
    asyncio.run(run())


def test_button_settings_fees_and_shutdown():
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            await app.post_init(app)
            for data in ("edit:min_roi",):
                await app.process_update(callback(app, data))
            await app.process_update(text_input(app, "25"))
            assert store.settings(1).min_roi == 25 and store.settings(2).min_roi == 10
            for data in ("routebuy:portals", "routesell:tonnel", "routecurrency:TON", "fee:sell"):
                await app.process_update(callback(app, data))
            await app.process_update(text_input(app, "5%"))
            fee = store.route(1, "portals", "tonnel", "TON").fees["sell"]
            assert fee.percent and fee.value == 5 and not fee.confirmed
            await app.process_update(callback(app, "fee:buy"))
            await app.process_update(text_input(app, "NaN"))
            assert "buy" not in store.route(1, "portals", "tonnel", "TON").fees
            await app.post_stop(app)
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert any("Некорректное значение" in t for t in texts)
        assert not any("Временная ошибка" in t for t in texts)
    asyncio.run(run())


def test_old_shop_button_opens_new_menu():
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "catalog"))
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert any("прежнего меню" in t and "/start" in t for t in texts)
    asyncio.run(run())


def last_buttons(request):
    params = next(p for name,p in reversed(request.calls) if name == "sendMessage")
    markup = params["reply_markup"]
    if isinstance(markup, str):
        markup = json.loads(markup)
    return [b for row in markup["inline_keyboard"] for b in row]


@pytest.mark.parametrize("destination", ["settings", "menu"])
def test_navigation_cancels_setting_input(destination):
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "edit:min_roi"))
            assert {b["callback_data"] for b in last_buttons(request)} == {"settings", "menu"}
            await app.process_update(callback(app, destination))
            await app.process_update(text_input(app, "999"))
        assert store.settings(1).min_roi == 10
        assert "awaiting" not in app.user_data[1]
    asyncio.run(run())


def test_fee_and_rate_back_keep_previous_step_without_saving():
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            for data in ("fees", "routebuy:portals", "routesell:tonnel", "routecurrency:TON", "fee:sell"):
                await app.process_update(callback(app, data))
            assert {b["callback_data"] for b in last_buttons(request)} == {"feesview", "menu"}
            await app.process_update(callback(app, "feesview"))
            await app.process_update(text_input(app, "50%"))
            assert "sell" not in store.route(1, "portals", "tonnel", "TON").fees
            await app.process_update(callback(app, "route_currency_menu"))
            assert "route_sell_menu" in {b["callback_data"] for b in last_buttons(request)}
            await app.process_update(callback(app, "route_sell_menu"))
            assert "fees" in {b["callback_data"] for b in last_buttons(request)}
            for data in ("ratebase", "ratequote:TON", "ratevalue:USD", "rate_quote_menu"):
                await app.process_update(callback(app, data))
            assert app.user_data[1]["rate_base"] == "TON"
            assert "ratebase" in {b["callback_data"] for b in last_buttons(request)}
            await app.process_update(text_input(app, "999"))
            assert store.rate("TON", "USD", 1) is None
            await app.process_update(callback(app, "menu"))
            assert not app.user_data[1]
            # An old message after returning home must offer a fresh route, not crash.
            await app.process_update(callback(app, "feesview"))
            assert {b["callback_data"] for b in last_buttons(request)} == {"fees", "menu"}
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert not any("Временная ошибка" in t for t in texts)
    asyncio.run(run())


def test_currency_controls_return_to_currency_section():
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry()), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "edit:display_currencies"))
            buttons = last_buttons(request)
            assert next(b["callback_data"] for b in buttons if "Готово" in b["text"]) == "currency"
            assert next(b["callback_data"] for b in buttons if "Назад" in b["text"]) == "currency"
            await app.process_update(callback(app, "edit:calculation_currency"))
            assert "currency" in {b["callback_data"] for b in last_buttons(request)}
    asyncio.run(run())
