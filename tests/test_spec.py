"""Public Thermos OpenAPI download, exercised with synthetic HTTP responses only."""
import asyncio
import json

import httpx
import pytest

from gift_radar import spec
from gift_radar.spec import download_spec
from gift_radar.storage import Store
from test_platforms import bot
from test_telegram import FakeTelegram, callback

SPEC = {"openapi": "3.1.0", "paths": {"/api/v1/gifts/catalog": {"get": {}}, "/api/v1/auth/api-token": {"post": {}}}}
PAGE = '<script>SwaggerUIBundle({ url: "/api/v1/openapi.json", dom_id: "#swagger-ui" })</script>'


def server(routes, seen):
    def response(request):
        seen.append(request)
        assert "api_token" not in str(request.url) and "authorization" not in request.headers
        status, body, headers = routes.get(request.url.path, (404, "", {}))
        return httpx.Response(status, content=body.encode() if isinstance(body, str) else body, headers=headers)
    return httpx.MockTransport(response)


def test_spec_url_is_read_from_swagger_page():
    seen = []
    transport = server({"/swagger-ui": (200, PAGE, {}), "/api/v1/openapi.json": (200, json.dumps(SPEC), {})}, seen)
    result = asyncio.run(download_spec(transport))
    assert result.state == "ok" and result.url.endswith("/api/v1/openapi.json")
    assert "GET /api/v1/gifts/catalog" in result.paths
    assert all(r.url.host == spec.HOST for r in seen)


def test_fallback_and_missing_spec():
    seen = []
    ok = server({"/swagger-ui": (200, "<html></html>", {}), "/openapi.json": (200, json.dumps(SPEC), {})}, seen)
    assert asyncio.run(download_spec(ok)).state == "ok"
    missing = server({"/swagger-ui": (200, "<html></html>", {})}, [])
    assert asyncio.run(download_spec(missing)).state == "not_found"


def test_redirect_off_thermos_host_is_refused():
    seen = []
    transport = server({"/swagger-ui": (302, "", {"location": "https://example.com/openapi.json"})}, seen)
    result = asyncio.run(download_spec(transport))
    assert result.state == "unexpected_response"
    assert all(r.url.host == spec.HOST for r in seen)


@pytest.mark.parametrize("error,state", [(httpx.ConnectError, "network_error"), (httpx.ReadTimeout, "timeout")])
def test_network_errors(error, state):
    def response(request):
        raise error("SYNTHETIC", request=request)
    assert asyncio.run(download_spec(httpx.MockTransport(response))).state == state


def test_admin_receives_spec_file(monkeypatch):
    class WithDocuments(FakeTelegram):
        async def do_request(self, url, method, request_data=None, **kwargs):
            if url.endswith("/sendDocument"):
                self.calls.append(("sendDocument", request_data.parameters if request_data else {}))
                result = {"message_id": 99, "date": 1, "chat": {"id": 1, "type": "private"}}
                return 200, json.dumps({"ok": True, "result": result}).encode()
            return await super().do_request(url, method, request_data, **kwargs)
    transport = server({"/swagger-ui": (200, PAGE, {}), "/api/v1/openapi.json": (200, json.dumps(SPEC), {})}, [])
    client = httpx.AsyncClient
    monkeypatch.setattr(spec, "AsyncClient", lambda **kw: client(**{**kw, "transport": transport}))
    async def run():
        store = Store(":memory:")
        app, _, request = bot(store, request=WithDocuments())
        async with app:
            await app.process_update(callback(app, "source_spec", user=99))
            assert not any(name == "sendDocument" for name, _ in request.calls)
            await app.process_update(callback(app, "source_spec"))
        assert any(name == "sendDocument" for name, _ in request.calls)
        shown = [p["text"] for name, p in request.calls if name == "sendMessage"]
        assert any("GET /api/v1/gifts/catalog" in t for t in shown)
    asyncio.run(run())
