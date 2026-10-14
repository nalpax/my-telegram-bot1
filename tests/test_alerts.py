import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from gift_radar.adapters import registry
from gift_radar.analytics import Rate
from gift_radar.service import Radar
from gift_radar.storage import Store, listing_key


def prepare(gift, sales, settings, route):
    store = Store(":memory:")
    settings.alerts = True
    settings.sell_markets = ["tonnel"]
    store.save_settings(1, settings)
    store.save_route(1, route)
    store.ingest([gift], sales)
    return store, Radar(store, registry())


def test_sender_once_after_multiple_cycles(monkeypatch, gift, sales, settings, route, now):
    store, radar = prepare(gift, sales, settings, route)
    clock = [now]
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: clock[0])
    sent = []
    async def sender(user, value):
        sent.append((user, value.listing.listing_id))
    async def run():
        await radar.tick(sender)
        clock[0] += timedelta(minutes=61)
        # Refresh observed listing; no duplicate even on repricing/reobservation.
        store.ingest([replace(gift, updated_at=clock[0])], [])
        await radar.tick(sender)
    asyncio.run(run())
    assert len(sent) == 1
    assert store.db.execute("SELECT state FROM notifications").fetchone()[0] == "sent"


def test_ambiguous_delivery_not_retried(monkeypatch, gift, sales, settings, route, now):
    store, radar = prepare(gift, sales, settings, route)
    clock = [now]
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: clock[0])
    calls = []
    async def sender(user, value):
        calls.append(user)
        raise TimeoutError("synthetic uncertain delivery")
    async def run():
        await radar.tick(sender)
        clock[0] += timedelta(minutes=61)
        store.ingest([replace(gift, updated_at=clock[0])], [])
        await radar.tick(sender)
    asyncio.run(run())
    assert calls == [1]
    assert store.db.execute("SELECT state FROM notifications").fetchone()[0] == "unknown_delivery"


def test_hidden_and_incomplete_not_sent(monkeypatch, gift, sales, settings, route, now):
    store, radar = prepare(gift, sales, settings, route)
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: now)
    store.mark("hidden", 1, listing_key(gift))
    async def sender(user, value):
        raise AssertionError("Must not send hidden")
    asyncio.run(radar.tick(sender))
    assert store.db.execute("SELECT count(*) FROM notifications").fetchone()[0] == 0
    store.mark("hidden", 1, listing_key(gift), remove=True)
    store.save_route(1, replace(route, fees={}))
    store.db.execute("UPDATE users SET last_attempt=NULL")
    store.db.commit()
    asyncio.run(radar.tick(sender))
    assert store.db.execute("SELECT count(*) FROM notifications").fetchone()[0] == 0


def test_three_alert_budget(monkeypatch, gift, sales, settings, route, now):
    store, radar = prepare(gift, sales, settings, route)
    store.ingest([replace(gift, listing_id=f"test-{i}") for i in range(10)], [])
    monkeypatch.setattr("gift_radar.service.utcnow", lambda: now)
    async def no_delay(seconds):
        pass
    monkeypatch.setattr("gift_radar.service.asyncio.sleep", no_delay)
    sent = []
    async def sender(user, value):
        sent.append(value.listing.listing_id)
    asyncio.run(radar.tick(sender))
    assert len(sent) == 3 and len(set(sent)) == 3


def test_manual_rates_are_private(now):
    store = Store(":memory:")
    global_rate = Rate("TON", "USD", Decimal("3"), now, "synthetic global rate")
    manual = replace(global_rate, value=Decimal("4"), source="synthetic user rate")
    store.save_rate(global_rate)
    store.save_rate(manual, user=1)
    assert store.rate("TON", "USD", 1).value == 4
    assert store.rate("TON", "USD", 2).value == 3
