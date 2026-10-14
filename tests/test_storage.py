import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from gift_radar.adapters import registry, read_authorized_export
from gift_radar.models import dumps
from gift_radar.service import Radar
from gift_radar.storage import Store, listing_key


def test_sqlite_migration_idempotent(tmp_path):
    path = str(tmp_path / "test.sqlite3")
    for _ in range(2):
        store = Store(path)
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        store.close()


def test_ingest_idempotent_and_price_history(gift, sales, now):
    s = Store(":memory:")
    s.ingest([gift], sales)
    s.ingest([gift], sales)
    assert len(s.listings()) == 1 and len(s.sales(now)) == 7
    assert s.db.execute("SELECT count(*) FROM price_history").fetchone()[0] == 1
    s.ingest([replace(gift, price=12, updated_at=now + timedelta(seconds=1))], [])
    assert s.db.execute("SELECT count(*) FROM price_history").fetchone()[0] == 2
    s.ingest([gift], [])
    assert s.listings()[0].price == 12


def test_sale_closes_listing(gift, sales, now):
    store = Store(":memory:")
    sale = replace(sales[0], gift=replace(gift, status="sold"))
    store.ingest([gift], [sale])
    assert not store.listings()


def test_alert_claim_survives_restart(tmp_path, gift, now):
    path = str(tmp_path / "test.sqlite3")
    store = Store(path)
    key = listing_key(gift)
    assert store.claim_alert(1, key, now)
    assert not store.claim_alert(1, key, now)
    store.close()
    store = Store(path)
    assert not store.claim_alert(1, key, now)
    assert store.claim_alert(2, key, now)


def test_favorites_are_per_user(gift):
    store = Store(":memory:")
    store.ingest([gift], [])
    key = listing_key(gift)
    store.mark("favorites", 1, key)
    assert key in store.marked("favorites", 1)
    assert not store.marked("favorites", 2)
    store.mark("favorites", 1, key, remove=True)
    assert not store.marked("favorites", 1)


def test_deleted_user_data(gift, now):
    store = Store(":memory:")
    store.ingest([gift], [])
    store.mark("hidden", 1, listing_key(gift))
    store.claim_alert(1, listing_key(gift), now)
    store.delete_user(1)
    assert not store.users() and not store.marked("hidden", 1)


def test_disabled_adapters_are_explicit():
    async def check():
        for adapter in registry():
            assert (await adapter.health_check()).state == "disabled"
            assert await adapter.get_active_listings() == []
            assert await adapter.get_completed_sales() == []
    asyncio.run(check())


def test_invalid_export_is_not_committed(tmp_path):
    path = tmp_path / "synthetic.json"
    path.write_text('{"schema_version":1,"source":"synthetic test","listings":[{"price":"NaN"}]}')
    with pytest.raises((ValueError, TypeError)):
        read_authorized_export(str(path), True)
    with pytest.raises(ValueError):
        read_authorized_export(str(path), False)


def test_authorized_export_keeps_unknowns(tmp_path, gift):
    path = tmp_path / "synthetic.json"
    data = json.loads(dumps(replace(gift, signature=None, raw={"token": "TEST-not-real", "price": "10"})))
    path.write_text(json.dumps({"schema_version": 1, "source": "synthetic test export", "listings": [data]}))
    listings, sales = read_authorized_export(str(path), True)
    assert listings[0].signature is None and "token" not in listings[0].raw


def test_partial_source_failure_recorded():
    class Bad:
        market = "portals"
        async def health_check(self):
            raise ValueError("TEST invalid response")
    store = Store(":memory:")
    radar = Radar(store, [Bad()])
    asyncio.run(radar.synchronize())
    assert store.market_status()[0][1] == "unavailable"
    assert store.db.execute("SELECT category FROM sync_errors").fetchone()[0] == "ValueError"


@pytest.mark.parametrize("market", ["portals", "tonnel", "mrkt"])
def test_internal_normalization_preserves_platform_identity(market, gift):
    # Internal export contract only: no claim about real marketplace responses.
    from gift_radar.models import gift_from
    row = json.loads(dumps(replace(gift, market=market, signature=None)))
    result = gift_from(row)
    assert result.market == market and result.signature is None
