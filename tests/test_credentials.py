"""Synthetic credentials only: encrypted persistence and the private-admin flow."""
import asyncio
import logging
import os
import stat

import pytest
from cryptography.fernet import Fernet

from gift_radar.adapters import registry
from gift_radar.connections import Connection
from gift_radar.credentials import credential_value
from gift_radar.security import SecretLogFilter
from gift_radar.service import Radar
from gift_radar.storage import Store
from gift_radar.telegram_ui import build_app
from test_telegram import FakeTelegram, callback, text_input


@pytest.fixture(autouse=True)
def encryption_environment(monkeypatch):
    monkeypatch.delenv("GIFT_RADAR_ENCRYPTION_KEY", raising=False)


def test_encrypted_key_survives_restart_without_plaintext(tmp_path):
    path = tmp_path / "test.sqlite3"
    secret = "SYNTHETIC-PRIVATE-KEY-RESTART"
    store = Store(str(path))
    store.save_connection(Connection("tonnel", docs_url="https://example.com/api"))
    store.save_credential("tonnel", secret)
    assert store.credential("tonnel") == secret
    assert secret not in "\n".join(store.db.iterdump())
    assert store.connection("tonnel").has_saved_key
    assert secret not in repr(store.connection("tonnel"))
    assert store.connection("tonnel").diagnosis()[0] == "needs_adapter"
    store.close()
    for file in tmp_path.iterdir():
        assert secret.encode() not in file.read_bytes()
        if os.name == "posix":
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
    store = Store(str(path))
    assert store.credential("tonnel") == secret
    assert store.connection("tonnel").docs_url == "https://example.com/api"
    record = logging.LogRecord("gift_radar", logging.ERROR, "test", 1, "key=%s", (secret,), None)
    SecretLogFilter().filter(record)
    assert secret not in record.getMessage()
    store.close()


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_valid_key"])
def test_master_key_loss_cannot_overwrite_saved_credentials(tmp_path, damage):
    path = tmp_path / "test.sqlite3"
    store = Store(str(path))
    store.save_credential("tonnel", "SYNTHETIC-OLD-KEY")
    before = store.db.execute("SELECT ciphertext FROM market_credentials").fetchone()[0]
    master = store.vault.path
    original = master.read_bytes()
    if damage == "missing":
        master.unlink()
    else:
        master.write_bytes(b"broken" if damage == "corrupt" else Fernet.generate_key())
    with pytest.raises(ValueError):
        store.save_credential("tonnel", "SYNTHETIC-NEW-KEY")
    assert store.db.execute("SELECT ciphertext FROM market_credentials").fetchone()[0] == before
    store.close()
    with pytest.raises(ValueError):
        Store(str(path))
    master.write_bytes(original)
    store = Store(str(path))
    assert store.credential("tonnel") == "SYNTHETIC-OLD-KEY"
    store.close()


def test_optional_host_master_key_and_ciphertext_context(monkeypatch, tmp_path):
    monkeypatch.setenv("GIFT_RADAR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = Store(str(tmp_path / "test.sqlite3"))
    store.save_credential("tonnel", "SYNTHETIC-ENV-MASTER-KEY")
    assert not store.vault.path.exists()
    ciphertext = store.db.execute("SELECT ciphertext FROM market_credentials").fetchone()[0]
    with pytest.raises(ValueError):
        store.vault.decrypt("mrkt", ciphertext)
    store.close()


def test_replacement_and_reset_preserve_docs_and_external_secret(monkeypatch):
    monkeypatch.setenv("TONNEL_TEST_KEY", "SYNTHETIC-HOST-VALUE")
    store = Store(":memory:")
    docs = "https://example.com/api"
    store.save_connection(Connection("tonnel", "TONNEL_TEST_KEY", docs))
    store.save_credential("tonnel", "SYNTHETIC-FIRST-KEY")
    store.save_credential("tonnel", "SYNTHETIC-SECOND-KEY")
    assert store.credential("tonnel") == "SYNTHETIC-SECOND-KEY"
    assert store.connection("tonnel").key_env is None
    assert store.connection("tonnel").docs_url == docs
    store.save_connection(Connection("tonnel", "TONNEL_TEST_KEY", docs))
    assert not store.connection("tonnel").has_saved_key
    assert store.credential("tonnel") == "SYNTHETIC-HOST-VALUE"
    store.save_credential("tonnel", "SYNTHETIC-THIRD-KEY")
    store.disconnect("tonnel")
    assert store.credential("tonnel") is None
    assert not store.connection("tonnel").credential_present
    assert os.environ["TONNEL_TEST_KEY"] == "SYNTHETIC-HOST-VALUE"


@pytest.mark.parametrize("raw", ["TONNEL_API_KEY", "short", "secret\nmultiline", "secret\x00value",
                                "123456789:" + "x" * 30, "x" * 4097])
def test_wrong_input_rejected_without_retaining_it(raw):
    with pytest.raises(ValueError) as error:
        credential_value(raw)
    assert raw not in str(error.value)


@pytest.mark.parametrize("button", ["source_key", "market_key:tonnel", "market_key:portals", "market_key:mrkt"])
def test_admin_enters_actual_key_and_back_cancels(monkeypatch, button):
    market = button.partition(":")[2] or "tonnel"
    monkeypatch.setenv("ADMIN_IDS", "1")
    secret = "SYNTHETIC-DIRECT-KEY-" + market
    monkeypatch.setenv(f"{market.upper()}_API_KEY", secret)
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        save = store.save_credential
        def checked_save(selected, value):
            assert any(name == "deleteMessage" for name, _ in request.calls)
            save(selected, value)
        store.save_credential = checked_save
        async with app:
            await app.process_update(callback(app, button, user=2))
            await app.process_update(text_input(app, secret, user=2))
            assert not store.has_credential("thermos")
            await app.process_update(callback(app, button))
            await app.process_update(text_input(app, f"{market.upper()}_API_KEY"))
            assert not store.has_credential("thermos")
            await app.process_update(text_input(app, secret))
            # Every marketplace button stores one Thermos key, never a per-market copy.
            assert store.credential("thermos") == secret
            assert not store.connection(market).has_saved_key
            assert "awaiting" not in app.user_data[1]
            await app.process_update(callback(app, button))
            await app.process_update(callback(app, f"market:{market}"))
            await app.process_update(text_input(app, "SYNTHETIC-CANCELLED-REPLACEMENT"))
            assert store.credential("thermos") == secret
            await app.process_update(callback(app, "edit:collections"))
            await app.process_update(text_input(app, secret))
            assert store.settings(1).collections == []
        texts = [p["text"] for name, p in request.calls if name in ("sendMessage", "editMessageText")]
        assert any("Отправь сам API-ключ" in text for text in texts)
        assert any("Ключ сохранён в зашифрованном виде" in text for text in texts)
        assert all(secret not in text for text in texts)
        assert secret not in "\n".join(store.db.iterdump())
        assert all(p.get("protect_content") for name, p in request.calls if name == "sendMessage")
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["delete", "false", "revoked"])
def test_deletion_failure_or_admin_revocation_prevents_save(monkeypatch, failure):
    from telegram import Message
    async def delete(message, **kwargs):
        if failure == "delete":
            raise RuntimeError("SYNTHETIC-PRIVATE-KEY-FAILED-DELETION")
        if failure == "false":
            return False
        monkeypatch.setenv("ADMIN_IDS", "2")
        return True
    monkeypatch.setattr(Message, "delete", delete)
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "market_key:tonnel"))
            await app.process_update(text_input(app, "SYNTHETIC-PRIVATE-KEY-FAILED-DELETION"))
        assert store.credential("tonnel") is None
        assert all("SYNTHETIC-PRIVATE-KEY-FAILED-DELETION" not in p["text"] for name, p in request.calls if name == "sendMessage")
    asyncio.run(run())


def test_migration_from_previous_key_bindings_preserves_configuration(tmp_path):
    import sqlite3
    from gift_radar.migrations import MIGRATIONS
    path = str(tmp_path / "previous.sqlite3")
    old = sqlite3.connect(path)
    for migration in MIGRATIONS[:3]:
        old.executescript(migration)
    old.execute("PRAGMA user_version=3")
    old.execute("INSERT INTO market_connections VALUES (?,?,?,?)",
                ("tonnel", "TONNEL_TEST_KEY", "https://example.com/api", "test"))
    old.commit()
    old.close()
    store = Store(path)
    assert store.connection("tonnel") == Connection("tonnel", "TONNEL_TEST_KEY", "https://example.com/api")
    assert not store.vault.path.exists()
    store.close()
