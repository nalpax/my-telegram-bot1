"""Synthetic settings and exports only; no real marketplace credentials or requests."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Update
from telegram.ext import ExtBot

from gift_radar.adapters import registry, read_authorized_export
from gift_radar.connections import Connection, documentation_url, key_name
from gift_radar.migrations import MIGRATIONS
from gift_radar.models import dumps
from gift_radar.service import Radar
from gift_radar.storage import Store
from gift_radar.telegram_ui import build_app
from test_telegram import FakeTelegram, callback, last_buttons, text_input


@pytest.mark.parametrize("value", ["BOT_TOKEN", "MRKT_API_KEY", "test-secret-value", "PORTALS_key", "PORTALS_TOKEN=secret"])
def test_only_matching_environment_name_accepted(value):
    with pytest.raises(ValueError):
        key_name("portals", value)


@pytest.mark.parametrize("value", ["http://example.com/docs", "https://name:secret@example.com/docs", "https://example.com/docs?token=secret"])
def test_documentation_cannot_hold_authentication(value):
    with pytest.raises(ValueError):
        documentation_url(value)


def test_metadata_persists_without_secret_values(monkeypatch, tmp_path):
    path = str(tmp_path / "test.sqlite3")
    monkeypatch.setenv("PORTALS_TEST_KEY", "TEST_ONLY_DO_NOT_PERSIST")
    store = Store(path)
    c = Connection("portals", "PORTALS_TEST_KEY", "https://example.com/test-only-docs")
    assert c.credential_present
    assert c.diagnosis()[0] == "needs_adapter"
    store.save_connection(c)
    store.close()
    store = Store(path)
    assert store.connection("portals") == c
    data = "\n".join(store.db.iterdump())
    assert "TEST_ONLY_DO_NOT_PERSIST" not in data
    assert store.connection("tonnel").key_env is None
    store.disconnect("portals")
    assert store.connection("portals").key_env is None
    assert c.credential_present  # Disconnect does not delete the hosting secret.


def test_new_migration_preserves_old_users(tmp_path):
    import sqlite3
    path = str(tmp_path / "test.sqlite3")
    old = sqlite3.connect(path)
    for i in range(2):
        old.executescript(MIGRATIONS[i])
    old.execute("PRAGMA user_version=2")
    old.execute("INSERT INTO metadata VALUES ('test_old','keep')")
    old.commit()
    old.close()
    store = Store(path)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert store.db.execute("SELECT value FROM metadata WHERE name='test_old'").fetchone()[0] == "keep"


def test_binding_never_implies_live_api_readiness(monkeypatch):
    monkeypatch.setenv("MRKT_TEST_KEY", "TEST_PROXY_PLACEHOLDER")
    store = Store(":memory:")
    store.save_connection(Connection("mrkt", "MRKT_TEST_KEY"))
    async def run():
        adapter = next(a for a in registry(store) if a.market == "mrkt")
        health = await adapter.health_check()
        assert health.state == "needs_adapter"
        assert not health.active_listings and not health.completed_sales
    asyncio.run(run())


def test_key_setup_owner_guard_and_presence(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.delenv("GIFT_RADAR_ADMIN_IDS", raising=False)
    monkeypatch.setenv("PORTALS_TEST_KEY", "TEST_ONLY_NEVER_IN_MESSAGES")
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "market_key:portals", user=2))
            assert "awaiting" not in app.user_data[2]
            await app.process_update(callback(app, "market_key:portals", user=1))
            await app.process_update(text_input(app, "PORTALS_TEST_KEY", user=1))
            assert store.connection("portals").key_env == "PORTALS_TEST_KEY"
            await app.process_update(callback(app, "market_check:portals", user=1))
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert any("Ключ на сервере: задан" in t for t in texts)
        assert any("Запрос к API не выполнялся" in t for t in texts)
        assert not any("TEST_ONLY_NEVER_IN_MESSAGES" in t for t in texts)
        assert "needs_adapter" == store.connection("portals").diagnosis()[0]
    asyncio.run(run())


def test_accidental_secret_is_rejected_and_deleted(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.delenv("GIFT_RADAR_ADMIN_IDS", raising=False)
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "market_key:mrkt"))
            await app.process_update(text_input(app, "TEST-secret-value-not-a-name"))
            assert store.connection("mrkt").key_env is None
            assert any(name == "deleteMessage" for name,p in request.calls)
            await app.process_update(callback(app, "market:mrkt"))
            await app.process_update(text_input(app, "MRKT_TEST_KEY"))
            assert store.connection("mrkt").key_env is None
    asyncio.run(run())


def document_update(app, user=1):
    return Update.de_json({"update_id": 4, "message": {"message_id": 4, "date": 1,
        "chat": {"id":user,"type":"private"},"from":{"id":user,"is_bot":False,"first_name":"TEST"},
        "document":{"file_id":"test-file","file_unique_id":"test-unique","file_name":"test.json","file_size":100}}}, app.bot)


@pytest.mark.parametrize("valid", [True, False])
def test_telegram_export_upload_validates_before_commit(monkeypatch, gift, sales, now, valid):
    monkeypatch.setenv("ADMIN_IDS", "1")
    monkeypatch.delenv("GIFT_RADAR_ADMIN_IDS", raising=False)
    monkeypatch.setattr("gift_radar.adapters.utcnow", lambda: now)
    export = {"schema_version":1,"source":"synthetic test export","listings":[json.loads(dumps(gift))],"sales":[]}
    if not valid:
        export["listings"].append({"price":"NaN"})
    downloaded = []
    async def get_file(bot, file_id, **kwargs):
        async def download(path):
            downloaded.append(Path(path))
            Path(path).write_text(json.dumps(export))
        return SimpleNamespace(download_to_drive=download)
    monkeypatch.setattr(ExtBot, "get_file", get_file)
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "market_import:portals"))
            await app.process_update(document_update(app))
        assert len(store.listings()) == (1 if valid else 0)
        assert downloaded and not downloaded[0].exists()
        texts = [p["text"] for name,p in request.calls if name == "sendMessage"]
        assert any(("Импортировано" if valid else "Файл не импортирован") in t for t in texts)
    asyncio.run(run())


def test_export_cannot_impersonate_other_market(tmp_path, gift, monkeypatch, now):
    monkeypatch.setattr("gift_radar.adapters.utcnow", lambda: now)
    path = tmp_path / "test.json"
    path.write_text(json.dumps({"schema_version":1,"source":"synthetic export","listings":[json.loads(dumps(gift))]}))
    with pytest.raises(ValueError):
        read_authorized_export(str(path),True,expected_market="mrkt")
