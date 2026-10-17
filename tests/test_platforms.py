"""Toggles, Thermos connection and first load. Synthetic keys and HTTP responses only: no live API is contacted."""
import asyncio
import json
import logging
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from gift_radar import key_checks
from gift_radar.adapters import Health, registry
from gift_radar.security import SecretLogFilter
from gift_radar.service import Radar
from gift_radar.sources import ThermosSource
from gift_radar.storage import Store
from gift_radar.telegram_ui import MENU, build_app, send_card
from test_telegram import FakeTelegram, callback, last_buttons, text_input, texts

SECRET = "SYNTHETIC-THERMOS-KEY-0001"
JWT = "SYNTHETIC-THERMOS-JWT-0001"


def thermos(monkeypatch, status=200, calls=None, body=None):
    """Synthetic Thermos auth endpoint; records every request it receives."""
    calls = [] if calls is None else calls
    def response(request):
        calls.append(request)
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, json=body if body is not None else {"token": JWT, "account": {"id": "PRIVATE"}})
    client = httpx.AsyncClient
    monkeypatch.setattr(key_checks, "AsyncClient", lambda **kw: client(**{**kw, "transport": httpx.MockTransport(response)}))
    return calls


def bot(store, adapters=None, source=None, request=None):
    source = source or ThermosSource(store, retry_delay=0)
    radar = Radar(store, adapters if adapters is not None else registry(store, source), source)
    request = request or FakeTelegram()
    return build_app("123:TEST_ONLY_TOKEN", store, radar, request=request, polling_request=FakeTelegram()), radar, request


def buttons(request):
    return {b["text"]: b.get("callback_data") for b in last_buttons(request)}


def test_main_menu_is_compact():
    labels = [b.text for row in MENU.inline_keyboard for b in row]
    assert labels == ["🔎 Поиск", "🏪 Площадки", "🔔 Уведомления", "⚙️ Настройки"]


@pytest.mark.parametrize("data", ["nt:alerts", "nt:sound", "at:rare", "mt:portals:on:c", "source_delete_yes", "source_key", "refresh"])
def test_outsider_cannot_toggle_or_connect(data):
    async def run():
        store = Store(":memory:")
        store.save_credential("thermos", SECRET)
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, data, user=99))
        assert store.has_credential("thermos")
        assert not store.users() and store.market_flag("portals") is None
        assert not any(name in ("sendMessage", "editMessageText") for name, _ in request.calls)
    asyncio.run(run())


def test_toggles_edit_in_place_and_survive_restart(tmp_path):
    path = str(tmp_path / "radar.sqlite3")
    async def run():
        store = Store(path)
        app, _, request = bot(store)
        async with app:
            for data in ("nt:alerts", "nt:sound", "nt:only_new", "nt:error_alerts", "at:rare", "at:signed", "at:incomplete"):
                await app.process_update(callback(app, data))
            assert "❌ Редкие варианты" in buttons(request) and "❌ Подписанные подарки" in buttons(request)
            await app.process_update(callback(app, "alerts"))
            assert "❌ Звук" in buttons(request) and "✅ Автоуведомления" in buttons(request)
            sends = [name for name, _ in request.calls if name == "sendMessage"]
            assert not sends  # Every toggle edited the pressed message.
        store.close()
    asyncio.run(run())
    s = Store(path).settings(1)
    assert (s.alerts, s.sound, s.only_new, s.error_alerts, s.include_rare, s.signature, s.show_incomplete) == \
        (True, False, False, False, False, "no", False)


def test_repeated_press_with_unchanged_content_is_silent():
    class NotModified(FakeTelegram):
        async def do_request(self, url, method, request_data=None, **kwargs):
            if url.endswith("/editMessageText"):
                self.calls.append(("editMessageText", request_data.parameters))
                return 400, json.dumps({"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"}).encode()
            return await super().do_request(url, method, request_data, **kwargs)
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store, request=NotModified())
        async with app:
            await app.process_update(callback(app, "alerts"))
        assert not any(name == "sendMessage" for name, _ in request.calls)
    asyncio.run(run())


@pytest.mark.parametrize("data", ["mt:portals", "mt:nowhere:on:c", "set:history_days", "nt:unknown", "at:", "edit:unknown",
                                  "listtoggle:buy_markets", "market:nowhere", "catalog"])
def test_old_or_malformed_buttons_are_safe(data):
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, data))
        shown = texts(request)
        assert shown and not any("Временная ошибка" in t for t in shown)
    asyncio.run(run())


def test_unconnected_marketplace_cannot_be_shown_enabled():
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, "market:portals"))
            assert buttons(request)["🔒 Участвует в анализе"] == "source"
            await app.process_update(callback(app, "mt:portals:on:c"))
        assert store.market_flag("portals") is None
        assert "пока не подключена" in texts(request)[-1]
    asyncio.run(run())


def test_connection_checks_then_loads_and_reports_without_commands(monkeypatch):
    calls = thermos(monkeypatch)
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, "market:mrkt"))
            await app.process_update(callback(app, "source_key"))
            await app.process_update(text_input(app, SECRET))
        shown = texts(request)
        assert str(calls[0].url).startswith(key_checks.THERMOS_AUTH_URL)
        assert [c.url.path for c in calls[1:]] == ["/api/v1/gifts/me/on-sale"]  # Account summary read only.
        assert calls[1].headers["authorization"] == f"Bearer {JWT}" and SECRET not in str(calls[1].url)
        assert any("Thermos подтвердил API-ключ" in t for t in shown)
        assert any("Загружаю первые данные" in t for t in shown)
        report = next(t for t in shown if t.startswith("📦 Первая загрузка"))
        # Access is real but no documented read method exists: never reported as received data.
        assert "новые данные не получены" in report and "автосбор недоступен" in report
        assert "Почему" in report and "нет данных о лотах" in report and "Фоновый мониторинг: ❌" in report
        assert not store.settings(1).alerts  # Monitoring is never switched on silently.
        for private in (SECRET, JWT, "PRIVATE"):
            assert all(private not in t for t in shown)
            assert private not in "\n".join(store.db.iterdump())
    asyncio.run(run())


def test_first_load_sends_real_candidates_once(monkeypatch, gift, sales, settings, route, now):
    thermos(monkeypatch)
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: now)
    monkeypatch.setattr("gift_radar.market_ui.utcnow", lambda: now)
    async def run():
        store = Store(":memory:")
        store.ingest([gift], sales)
        store.save_settings(1, settings)
        store.save_route(1, route)
        app, radar, request = bot(store)
        async with app:
            await app.process_update(callback(app, "source_key"))
            await app.process_update(text_input(app, SECRET))
        shown = texts(request)
        assert any("Подходящих предложений: 1" in t for t in shown)
        assert any("Подходящее предложение" in t and "TEST collection" in t for t in shown)
        settings.alerts = True
        store.save_settings(1, settings)
        sent = []
        async def sender(user, value):
            sent.append(value)
        await radar.tick(sender)
        assert sent == []  # Already delivered by the first load.
    asyncio.run(run())


def test_rejected_key_offers_retry_and_no_load(monkeypatch):
    thermos(monkeypatch, status=401)
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store)
        async with app:
            await app.process_update(callback(app, "source_key"))
            await app.process_update(text_input(app, SECRET))
            assert "🔁 Проверить снова" in buttons(request)
            await app.process_update(callback(app, "market:portals"))
        shown = texts(request)
        assert not any("Загружаю первые данные" in t for t in shown)
        assert "⚠️ ключ отклонён" in shown[-1]
        assert store.has_credential("thermos")
    asyncio.run(run())


def test_network_failure_never_marks_key_invalid(monkeypatch):
    calls = thermos(monkeypatch, status=httpx.ConnectError("SYNTHETIC"))
    async def run():
        store = Store(":memory:")
        source = ThermosSource(store, retry_delay=0)
        store.save_credential("thermos", SECRET)
        store.set_source_state("thermos", "verified", "ok", verified=True)
        result = await source.verify()
        assert result.state == "network_error" and len(calls) == 2  # One bounded retry.
        access = source.access()
        assert access.usable and access.verified_at is not None and "нет соединения" in access.label
        await source.ensure_access()
        assert len(calls) == 2  # Back-off: no hammering after a temporary failure.
    asyncio.run(run())


def test_background_auth_is_cached_and_rejected_key_not_retried(monkeypatch):
    calls = thermos(monkeypatch)
    async def run():
        store = Store(":memory:")
        source = ThermosSource(store, retry_delay=0)
        store.save_credential("thermos", SECRET)
        radar = Radar(store, registry(store, source), source)
        results = await radar.synchronize()
        assert len(calls) == 1  # One auth for all three marketplaces.
        assert {r.state for r in results.values()} == {"no_contract"}
        await radar.synchronize()
        assert len(calls) == 1  # JWT lifetime is reused.
        thermos(monkeypatch, status=401, calls=calls)
        source.jwt_until = None
        await radar.synchronize()
        await radar.synchronize()
        assert len(calls) == 2 and source.access().state == "invalid"
    asyncio.run(run())


def test_legacy_tonnel_slot_is_the_thermos_key(monkeypatch):
    calls = thermos(monkeypatch)
    async def run():
        store = Store(":memory:")
        store.save_credential("tonnel", SECRET)
        source = ThermosSource(store, retry_delay=0)
        assert source.access().state == "saved"
        assert (await source.verify()).state == "verified"
        assert calls[0].url.params["api_token"] == SECRET
    asyncio.run(run())


class Recording:
    def __init__(self, market, fail=False):
        self.market, self.fail, self.calls = market, fail, 0

    async def health_check(self):
        self.calls += 1
        if self.fail:
            raise OSError("SYNTHETIC outage")
        return Health("no_contract", "synthetic")


def test_switched_off_marketplace_gets_no_requests_and_one_failure_spares_others():
    async def run():
        store = Store(":memory:")
        adapters = [Recording("portals"), Recording("mrkt", fail=True), Recording("tonnel")]
        radar = Radar(store, adapters)
        store.set_market_flag("tonnel", False)
        results = await radar.synchronize()
        assert [a.calls for a in adapters] == [1, 1, 0]
        assert results["portals"].state == "no_contract" and results["mrkt"].state == "unavailable"
        assert results["tonnel"].state == "off"
    asyncio.run(run())


def test_disable_keeps_key_and_deletion_needs_confirmation(monkeypatch, gift):
    thermos(monkeypatch)
    async def run():
        store = Store(":memory:")
        store.ingest([gift], [])
        store.save_credential("thermos", SECRET)
        app, radar, request = bot(store)
        async with app:
            await app.process_update(callback(app, "mt:portals:on:c"))
            assert store.market_flag("portals") is False and store.has_credential("thermos")
            assert "❌ Участвует в анализе" in buttons(request)
            assert radar.analyses(1) == []
            await app.process_update(callback(app, "mt:portals:on:c"))
            assert store.market_flag("portals") is True
            await app.process_update(callback(app, "source_delete"))
            assert store.has_credential("thermos")
            await app.process_update(callback(app, "source_delete_yes"))
        assert not store.has_credential("thermos")
        assert store.market_flag("portals") is True  # Settings survive key deletion.
    asyncio.run(run())


def test_back_cancels_key_input():
    async def run():
        store = Store(":memory:")
        app, _, _ = bot(store)
        async with app:
            await app.process_update(callback(app, "source_key"))
            await app.process_update(callback(app, "source"))
            await app.process_update(text_input(app, SECRET))
        assert not store.has_credential("thermos")
    asyncio.run(run())


def test_buy_sell_and_analysis_toggles_change_analysis(gift, sales, settings, route, now):
    store = Store(":memory:")
    store.ingest([gift, replace(gift, listing_id="test-rare", rare=True)], sales)
    store.save_settings(1, settings)
    radar = Radar(store, [])
    keys = lambda: {(v.listing.listing_id, v.target) for v in radar.analyses(1, now=now)}
    assert ("test-only-lot", "tonnel") in keys() and ("test-rare", "tonnel") in keys()
    settings.include_rare = False
    store.save_settings(1, settings)
    assert all(lot != "test-rare" for lot, _ in keys())
    settings.sell_markets = []
    store.save_settings(1, settings)
    assert keys() == set()


def test_sound_switch_changes_delivery(gift, sales, settings, route, now, monkeypatch):
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: now)
    async def run():
        store = Store(":memory:")
        store.ingest([gift], sales)
        settings.alerts, settings.sound = True, False
        store.save_settings(1, settings)
        store.save_route(1, route)
        app, radar, request = bot(store, adapters=[])
        async with app:
            async def sender(user, value):
                await send_card(app.bot, user, value, store.settings(user), store)
            await radar.tick(sender)
        cards = [p for name, p in request.calls if name == "sendMessage"]
        assert cards and all(p.get("disable_notification") for p in cards)
    asyncio.run(run())


def test_alerts_off_sends_nothing_and_errors_respect_switches(gift, sales, settings, route, now, monkeypatch):
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: now)
    async def run():
        store = Store(":memory:")
        store.ingest([gift], sales)
        store.save_settings(1, settings)
        store.save_route(1, route)
        store.save_credential("thermos", SECRET)
        store.set_source_state("thermos", "invalid", "⚠️ Thermos отклонил ключ.")
        radar = Radar(store, [])
        cards, notices = [], []
        async def sender(user, value):
            cards.append(value)
        async def notifier(user, message):
            notices.append(message)
        await radar.tick(sender, notifier)
        assert cards == [] and notices == []
        settings.alerts = True
        settings.error_alerts = False
        store.save_settings(1, settings)
        store.db.execute("UPDATE users SET last_attempt=NULL")
        store.db.commit()
        await radar.tick(sender, notifier)
        assert len(cards) == 1 and notices == []
        settings.error_alerts = True
        store.save_settings(1, settings)
        await radar.tick(sender, notifier)
        await radar.tick(sender, notifier)
        assert len(notices) == 1 and "Thermos" in notices[0]  # Once per state change.
        store.set_source_state("thermos", "verified", "ok", verified=True)
        await radar.tick(sender, notifier)
        assert len(notices) == 2 and "восстановлено" in notices[1]
    asyncio.run(run())


def test_price_drop_repeat_only_when_only_new_is_off(gift, sales, settings, route, now, monkeypatch, tmp_path):
    clock = [now]
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: clock[0])
    path = str(tmp_path / "radar.sqlite3")
    sent = []
    async def sender(user, value):
        sent.append(value.listing.price)
    async def cycle(cheaper=None):
        store = Store(path)  # Reopened every cycle: restart must not resend.
        if cheaper is not None:
            store.ingest([replace(gift, price=cheaper, updated_at=clock[0])], [])
        await Radar(store, []).tick(sender)
        store.close()
    store = Store(path)
    store.ingest([gift], sales)
    settings.alerts = True
    store.save_settings(1, settings)
    store.save_route(1, route)
    store.close()
    asyncio.run(cycle())
    clock[0] += timedelta(minutes=61)
    asyncio.run(cycle())
    assert len(sent) == 1
    clock[0] += timedelta(minutes=61)
    asyncio.run(cycle(cheaper=gift.price - 1))
    assert len(sent) == 1  # Only new lots by default.
    store = Store(path)
    settings.only_new = False
    store.save_settings(1, settings)
    store.close()
    clock[0] += timedelta(minutes=61)
    asyncio.run(cycle(cheaper=gift.price - 2))
    assert len(sent) == 2
    clock[0] += timedelta(minutes=61)
    asyncio.run(cycle())
    assert len(sent) == 2  # Same price again: no repeat.


def test_incomplete_candidates_are_marked_or_hidden(gift, sales, settings, now, monkeypatch):
    monkeypatch.setattr("gift_radar.telegram_ui.utcnow", lambda: now)
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: now)
    async def run():
        store = Store(":memory:")
        store.ingest([gift], sales)
        store.save_settings(1, settings)  # No route fees: costs unknown.
        app, _, request = bot(store, adapters=[])
        async with app:
            await app.process_update(callback(app, "scan"))
            assert any("Неполные данные — не подтверждённая возможность" in t for t in texts(request))
            await app.process_update(callback(app, "at:incomplete"))
            before = len(request.calls)
            monkeypatch.setattr("gift_radar.telegram_ui.utcnow", lambda: now + timedelta(seconds=31))
            await app.process_update(callback(app, "scan"))
        later = [p["text"] for name, p in request.calls[before:] if name in ("sendMessage", "editMessageText")]
        assert later and not any("TEST collection" in t for t in later)
        assert any("не заданы расходы маршрута" in t and "скрыты" in t for t in later)
    asyncio.run(run())


def test_log_filter_masks_url_encoded_key(monkeypatch):
    thermos(monkeypatch)
    secret = "SYNTHETIC/KEY+WITH&CHARS"
    async def run():
        store = Store(":memory:")
        store.save_credential("thermos", secret)
        await ThermosSource(store, retry_delay=0).verify()
    asyncio.run(run())
    from urllib.parse import quote, quote_plus
    for value in (secret, quote(secret, safe=""), quote_plus(secret, safe=""), JWT):
        record = logging.LogRecord("gift_radar", logging.ERROR, "test", 1, "value=%s", (value,), None)
        SecretLogFilter().filter(record)
        assert value not in record.getMessage()
