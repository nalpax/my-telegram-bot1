"""Documented Thermos auth contract, exercised with a synthetic HTTP transport."""
import asyncio
import logging

import httpx
import pytest

from gift_radar.adapters import registry
from gift_radar.key_checks import MarketKeyChecker, THERMOS_AUTH_URL
from gift_radar.security import SecretLogFilter
from gift_radar.service import Radar
from gift_radar.storage import Store
from gift_radar.telegram_ui import build_app
from test_telegram import FakeTelegram, callback, text_input, last_buttons


@pytest.mark.parametrize("status,state", [(400,"invalid"), (401,"invalid"), (422,"invalid"),
    (403,"access_denied"), (429,"rate_limited"), (500,"service_error"), (503,"service_error"),
    (302,"unexpected_response"), (404,"unexpected_response"), (204,"unexpected_response")])
def test_api_failures_are_never_reported_as_verified(status, state):
    calls = []
    secret = "SYNTHETIC/key with+query&characters"
    def response(request):
        calls.append(request)
        assert request.method == "POST"
        assert str(request.url).split("?")[0] == THERMOS_AUTH_URL
        assert request.url.params["api_token"] == secret
        return httpx.Response(status, text=secret, headers={"location": "https://example.com/untrusted"})
    result = asyncio.run(MarketKeyChecker(httpx.MockTransport(response)).check("tonnel", secret))
    assert result.state == state
    assert secret not in result.message
    assert len(calls) == 1
    record = logging.LogRecord("gift_radar", logging.ERROR, "test", 1, "url=%s", (str(calls[0].url),), None)
    SecretLogFilter().filter(record)
    assert str(calls[0].url) not in record.getMessage()


@pytest.mark.parametrize("body", [b"not json", b"null", b"[]", b'{"token":null}', b'{"token":""}',
    b'{"token":123}', b'{"token":"tiny"}', b'{"token":"' + b'x' * 70000 + b'"}'])
def test_http_success_without_auth_token_is_not_verified(body):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    result = asyncio.run(MarketKeyChecker(transport).check("tonnel", "SYNTHETIC-KEY"))
    assert result.state == "unexpected_response"


@pytest.mark.parametrize("error,state", [(httpx.ReadTimeout,"timeout"), (httpx.ConnectError,"network_error")])
def test_network_failures_do_not_label_key_invalid(error, state):
    def response(request):
        raise error("SYNTHETIC-PRIVATE-ERROR", request=request)
    result = asyncio.run(MarketKeyChecker(httpx.MockTransport(response)).check("tonnel", "SYNTHETIC-KEY"))
    assert result.state == state
    assert "SYNTHETIC-PRIVATE-ERROR" not in result.message


def test_unknown_market_or_missing_key_never_sends_credentials():
    def response(_):
        pytest.fail("No confirmed authentication endpoint for this marketplace")
    checker = MarketKeyChecker(httpx.MockTransport(response))
    async def run():
        assert (await checker.check("tonnel", None)).state == "missing"
        assert (await checker.check("portals", "SYNTHETIC-KEY")).state == "unsupported"
        assert (await checker.check("mrkt", "SYNTHETIC-KEY")).state == "unsupported"
    asyncio.run(run())


def test_addition_triggers_auth_check_without_host_variables(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "1")
    for name in ("TONNEL_API_KEY", "GIFT_RADAR_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)
    secret, jwt = "SYNTHETIC-AUTO-CHECK-KEY", "SYNTHETIC-RESPONSE-JWT"
    calls = []
    store = Store(":memory:")
    def response(request):
        calls.append(request)
        assert store.credential("tonnel") == secret  # Saved before verification.
        assert request.url.params["api_token"] == secret
        return httpx.Response(200, json={"token": jwt, "account": {"id":"PRIVATE_ACCOUNT", "total_balance":"PRIVATE_BALANCE"}})
    async def run():
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        # The app uses the real checker with a synthetic HTTP transport.
        from gift_radar import key_checks
        client = httpx.AsyncClient
        monkeypatch.setattr(key_checks, "AsyncClient", lambda **kwargs: client(**{**kwargs, "transport":httpx.MockTransport(response)}))
        async with app:
            await app.process_update(callback(app, "market_key:tonnel", user=2))
            await app.process_update(text_input(app, secret, user=2))
            assert calls == []
            await app.process_update(callback(app, "market_key:tonnel"))
            assert "market_env:tonnel" not in [b.get("callback_data") for b in last_buttons(request)]
            await app.process_update(text_input(app, secret))
            assert len(calls) == 1
            await app.process_update(callback(app, "market_check:tonnel"))
            assert len(calls) == 2
        messages = [p["text"] for name, p in request.calls if name == "sendMessage"]
        assert any("Thermos подтвердил API-ключ" in t for t in messages)
        dump = "\n".join(store.db.iterdump())
        for private in (secret, jwt, "PRIVATE_ACCOUNT", "PRIVATE_BALANCE"):
            assert private not in dump
            assert all(private not in message for message in messages)
    asyncio.run(run())


def test_admin_removed_during_api_check_receives_no_result(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", "1")
    from gift_radar import key_checks
    def response(_):
        monkeypatch.setenv("ADMIN_IDS", "2")
        return httpx.Response(200, json={"token":"SYNTHETIC-REVOKED-RESULT-JWT"})
    client = httpx.AsyncClient
    monkeypatch.setattr(key_checks, "AsyncClient", lambda **kwargs: client(**{**kwargs, "transport":httpx.MockTransport(response)}))
    async def run():
        store = Store(":memory:")
        request = FakeTelegram()
        app = build_app("123:TEST_ONLY_TOKEN", store, Radar(store, registry(store)), request=request, polling_request=FakeTelegram())
        async with app:
            await app.process_update(callback(app, "market_key:tonnel"))
            await app.process_update(text_input(app, "SYNTHETIC-REVOKED-CHECK-KEY"))
        assert not any("Thermos подтвердил" in p["text"] for name,p in request.calls if name == "sendMessage")
    asyncio.run(run())
