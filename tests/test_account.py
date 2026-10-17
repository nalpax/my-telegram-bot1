"""Thermos account data (own lots and sales) with synthetic HTTP following the Swagger schemas."""
import asyncio
import json

import httpx

from gift_radar import key_checks
from gift_radar.service import Radar
from gift_radar.sources import ThermosSource
from gift_radar.storage import Store
from test_platforms import bot, SECRET, JWT
from test_telegram import callback, texts

META = {"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "name": "Desk Calendar", "model": "TEST model", "symbol": "s",
        "backdrop": "TEST backdrop", "number": 1212, "unique_name": "deskcalendar-1212",
        "model_rarity": 1, "symbol_rarity": 1, "backdrop_rarity": 1}
LOTS = [{"id": "11111111-1111-1111-1111-111111111111", "meta": META, "price_ton": "2500000000",
         "market_source": "PORTALS", "sale_status": "LISTED"}]


def history(*ids):
    return [{"id": i, "account_id": META["id"], "occurred_at": "2026-10-17T10:00:00Z",
             "payload": {"type": "GIFT_SELL", "gift": META, "amount": 3_000_000_000 + i, "balance_after": 0,
                         "marketplace": "TONNEL"}} for i in ids]


def server(monkeypatch, sales, calls, expire_once=False):
    state = {"expired": expire_once}
    def response(request):
        calls.append(request)
        path = request.url.path
        if path == "/api/v1/auth/api-token":
            return httpx.Response(200, json={"token": JWT, "account": {}})
        assert request.headers.get("authorization") == f"Bearer {JWT}"
        assert "api_token" not in str(request.url)
        if state["expired"]:
            state["expired"] = False
            return httpx.Response(401, json={"message": "expired"})
        if path == "/api/v1/gifts/me/on-sale":
            return httpx.Response(200, json=LOTS)
        if path == "/api/v1/accounts/history":
            assert json.loads(request.content) == {"event_types": ["GIFT_SELL"], "limit": 50}
            return httpx.Response(200, json=sales())
        raise AssertionError(f"Unexpected Thermos call {path}")
    client = httpx.AsyncClient
    monkeypatch.setattr(key_checks, "AsyncClient", lambda **kw: client(**{**kw, "transport": httpx.MockTransport(response)}))


def test_my_lots_screen_shows_real_account_data(monkeypatch):
    calls = []
    server(monkeypatch, lambda: history(7), calls)
    async def run():
        store = Store(":memory:")
        store.save_credential("thermos", SECRET)
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, "mylots", user=99))
            assert calls == []
            await app.process_update(callback(app, "mylots"))
        shown = "\n".join(texts(request))
        assert "Desk Calendar #1212 — 2.5 TON · PORTALS · на продаже" in shown
        assert "Desk Calendar #1212 — 3.000000007 TON · TONNEL" in shown
        assert JWT not in shown and SECRET not in shown
    asyncio.run(run())


def test_unconnected_account_points_to_connection(monkeypatch):
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, "mylots"))
        assert "Сначала подключи Thermos" in texts(request)[-1]
    asyncio.run(run())


def test_new_sales_notify_once_and_old_history_is_silent(monkeypatch):
    calls, events = [], [history(1, 2)]
    server(monkeypatch, lambda: events[0], calls)
    async def run():
        store = Store(":memory:")
        store.save_credential("thermos", SECRET)
        settings = store.settings(1)
        settings.alerts = True
        store.save_settings(1, settings)
        source = ThermosSource(store, retry_delay=0)
        radar = Radar(store, [], source)
        notices = []
        async def notifier(user, message):
            notices.append((user, message))
        async def sender(user, value):
            pass
        await radar.tick(sender, notifier)
        assert notices == []  # Existing history is not announced.
        events[0] = history(1, 2, 3)
        await radar.tick(sender, notifier)
        await radar.tick(sender, notifier)
        assert len(notices) == 1 and notices[0][0] == 1 and "💰 Продажа в Thermos: Desk Calendar #1212" in notices[0][1]
        settings.alerts = False
        store.save_settings(1, settings)
        events[0] = history(1, 2, 3, 4)
        before = len(calls)
        await radar.tick(sender, notifier)
        assert len(notices) == 1 and len(calls) == before  # Alerts off: no request, no message.
    asyncio.run(run())


def test_expired_authorization_is_refreshed_once(monkeypatch):
    calls = []
    server(monkeypatch, lambda: history(1), calls, expire_once=True)
    async def run():
        store = Store(":memory:")
        store.save_credential("thermos", SECRET)
        source = ThermosSource(store, retry_delay=0)
        lots = await source.my_lots()
        assert lots[0]["title"] == "Desk Calendar #1212"
        assert [c.url.path for c in calls].count("/api/v1/auth/api-token") == 2
    asyncio.run(run())
