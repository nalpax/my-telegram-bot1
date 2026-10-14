"""Security regressions with synthetic users and credentials, never real API access."""
import asyncio
import logging
import os
import stat
from dataclasses import replace

import pytest
from telegram import Update

from gift_radar.adapters import registry
from gift_radar.connections import Connection
from gift_radar.security import SecretLogFilter, admin_ids
from gift_radar.service import Radar
from gift_radar.storage import Store, listing_key
from gift_radar.telegram_ui import build_app
from test_connections import document_update
from test_telegram import FakeTelegram, callback, command, text_input


@pytest.mark.parametrize("value", ["", "abc", "1,abc", "-1", "0", "1,", "١", "1;2"])
def test_invalid_or_missing_admin_configuration_denies_everyone(monkeypatch, value):
    monkeypatch.setenv("ADMIN_IDS",value)
    assert not admin_ids()


def test_optional_override_is_authoritative(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS","1,2")
    monkeypatch.setenv("GIFT_RADAR_ADMIN_IDS","3")
    assert admin_ids() == frozenset({3})
    monkeypatch.setenv("GIFT_RADAR_ADMIN_IDS","invalid")
    assert not admin_ids()


@pytest.mark.parametrize("cmd", ["/start", "/help", "/settings", "/markets", "/scan", "/alerts", "/favorites",
                                  "/fees", "/currency", "/status", "/cancel", "/delete_me", "/id"])
def test_non_admin_commands_never_read_or_create_user_data(cmd, gift):
    async def run():
        store=Store(":memory:")
        store.ingest([gift],[])
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.process_update(command(app,cmd,user=99))
        assert not store.users()
        sent=[p for name,p in request.calls if name=="sendMessage"]
        assert len(sent)==1 and "Доступ закрыт" in sent[0]["text"]
        assert not sent[0].get("reply_markup")
        assert "TEST gift" not in sent[0]["text"]
        assert not any(name=="setMyCommands" for name,p in request.calls)
    asyncio.run(run())


@pytest.mark.parametrize("data", ["menu","settings","market:tonnel","market_key:mrkt","market_import:portals",
                                   "market_disconnect_yes:mrkt","togglealerts","deleteconfirm"])
def test_forged_buttons_do_not_mutate_or_disclose_data(data, gift):
    async def run():
        store=Store(":memory:")
        store.ingest([gift],[])
        c=Connection("mrkt","MRKT_TEST_KEY")
        store.save_connection(c)
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app,data,user=99))
            await app.process_update(callback(app,f"compare:{listing_key(gift)}",user=99))
        assert not store.users()
        assert store.connection("mrkt")==c
        assert not any(name=="sendMessage" for name,p in request.calls)
        assert any(name=="answerCallbackQuery" and p.get("show_alert") for name,p in request.calls)
    asyncio.run(run())


def test_text_and_documents_cannot_bypass_access_gate():
    async def run():
        store=Store(":memory:")
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.process_update(text_input(app,"99",user=99))
            await app.process_update(document_update(app,user=99))
        assert not store.users()
        assert not any(name=="getFile" for name,p in request.calls)
        assert all("Доступ закрыт" in p["text"] for name,p in request.calls if name=="sendMessage")
    asyncio.run(run())


def test_admin_cannot_disclose_bot_data_in_group():
    async def run():
        store=Store(":memory:")
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        update=Update.de_json({"update_id":1,"message":{"message_id":1,"date":1,
            "chat":{"id":-100,"type":"supergroup","title":"TEST"},"from":{"id":1,"is_bot":False,"first_name":"TEST"},
            "text":"/settings","entities":[{"type":"bot_command","offset":0,"length":9}]}},app.bot)
        async with app:
            await app.process_update(update)
        assert not store.users()
        assert not any(name=="sendMessage" for name,p in request.calls)
    asyncio.run(run())


def test_denial_delivery_failure_never_opens_access():
    class BrokenDenial(FakeTelegram):
        async def do_request(self,url,method,request_data=None,**kwargs):
            if url.endswith('/sendMessage'):
                raise OSError("synthetic send failure")
            return await super().do_request(url,method,request_data,**kwargs)
    async def run():
        store=Store(":memory:")
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=BrokenDenial(),polling_request=FakeTelegram())
        async with app:
            await app.process_update(command(app,"/start",user=99))
        assert not store.users()
    asyncio.run(run())


def test_revoked_admin_cannot_complete_pending_input(monkeypatch):
    async def run():
        store=Store(":memory:")
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app,"edit:min_roi",user=1))
            monkeypatch.setenv("ADMIN_IDS","2")
            await app.process_update(text_input(app,"999",user=1))
        assert store.settings(1).min_roi==10
        assert not app.user_data[1]
    asyncio.run(run())


def test_legacy_non_admin_alerts_are_never_sent(monkeypatch,gift,sales,settings,route,now):
    store=Store(":memory:")
    settings.alerts=True
    settings.sell_markets=["tonnel"]
    store.save_settings(99,settings)
    store.save_route(99,route)
    store.ingest([gift],sales)
    monkeypatch.setattr("gift_radar.service.utcnow",lambda:now)
    radar=Radar(store,registry(store))
    async def sender(user,value):
        raise AssertionError("Unauthorized recipient")
    asyncio.run(radar.tick(sender))
    assert store.db.execute("SELECT count(*) FROM notifications").fetchone()[0]==0
    with pytest.raises(PermissionError):
        radar.analyses(99,now=now)


def test_admin_responses_are_protected_and_input_removed():
    async def run():
        store=Store(":memory:")
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app,"edit:min_roi",user=1))
            await app.process_update(text_input(app,"20",user=1))
        assert store.settings(1).min_roi==20
        sent=[p for name,p in request.calls if name=="sendMessage"]
        assert sent and all(p.get("protect_content") for p in sent)
        assert any(name=="deleteMessage" for name,p in request.calls)
        menus=[p for name,p in request.calls if name=="setMyCommands"]
        assert menus and all('"type": "chat"' in str(p.get("scope")) or (isinstance(p.get("scope"),dict) and p["scope"]["type"]=="chat") for p in menus)
    asyncio.run(run())


@pytest.mark.skipif(os.name!="posix",reason="POSIX permissions only; Windows uses NTFS ACLs")
def test_database_and_journal_have_private_permissions(tmp_path):
    path=tmp_path/"test.sqlite3"
    path.touch(mode=0o644)
    store=Store(str(path))
    store.settings(1)
    for file in tmp_path.iterdir():
        assert stat.S_IMODE(file.stat().st_mode)==0o600
    store.close()


def test_log_filter_masks_configured_keys_and_exception_bodies(monkeypatch):
    secret="TEST_ONLY_PRIVATE_CREDENTIAL"
    monkeypatch.setenv("TONNEL_API_KEY",secret)
    error=ValueError(secret)
    record=logging.LogRecord("gift_radar",logging.ERROR,"test",1,"request key=%s",(secret,),
                             (ValueError,error,None))
    assert SecretLogFilter().filter(record)
    assert secret not in record.getMessage() and "[REDACTED]" in record.getMessage()
    assert record.exc_info is None and record.args==()


def test_secret_pasted_into_unrelated_setting_is_not_persisted(monkeypatch):
    secret="TEST_ONLY_PRIVATE_CREDENTIAL"
    monkeypatch.setenv("TONNEL_API_KEY",secret)
    async def run():
        store=Store(":memory:")
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app,"edit:collections",user=1))
            await app.process_update(text_input(app,secret,user=1))
        assert store.settings(1).collections==[]
        assert secret not in "\n".join(store.db.iterdump())
        assert all(secret not in p["text"] for name,p in request.calls if name=="sendMessage")
        assert any(name=="deleteMessage" for name,p in request.calls)
    asyncio.run(run())


@pytest.mark.skipif(os.name!="posix",reason="POSIX permissions only")
def test_log_files_are_private_and_actual_logging_redacts(monkeypatch,tmp_path):
    from gift_radar.cli import configure_logging
    secret="TEST_ONLY_LOG_CREDENTIAL"
    monkeypatch.setenv("PORTALS_API_KEY",secret)
    logger=logging.getLogger("gift_radar")
    old_handlers,old_level,old_propagate=list(logger.handlers),logger.level,logger.propagate
    mask=os.umask(0o077)
    try:
        configure_logging(str(tmp_path))
        logger.error("synthetic secret=%s",secret)
        for h in logger.handlers:
            h.flush()
        file=tmp_path/"radar.log"
        assert stat.S_IMODE(file.stat().st_mode)==0o600
        assert secret not in file.read_text() and "[REDACTED]" in file.read_text()
    finally:
        for h in logger.handlers:
            if h not in old_handlers:
                h.close()
        logger.handlers,logger.level,logger.propagate=old_handlers,old_level,old_propagate
        os.umask(mask)


def test_startup_removes_legacy_command_menus():
    async def run():
        store=Store(":memory:")
        store.settings(99)
        request=FakeTelegram()
        app=build_app("123:TEST_ONLY_TOKEN",store,Radar(store,registry(store)),request=request,polling_request=FakeTelegram())
        async with app:
            await app.post_init(app)
            await app.post_stop(app)
        calls=[p for name,p in request.calls if name=="deleteMyCommands"]
        assert len(calls)==3
        assert not any(name=="setMyCommands" for name,p in request.calls)
    asyncio.run(run())
