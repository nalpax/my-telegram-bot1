"""Credential checks against documented auth endpoints, without market operations."""
import asyncio
import json
from dataclasses import dataclass
from urllib.parse import quote, quote_plus

from httpx import AsyncClient, HTTPError, TimeoutException

from .security import register_secret


THERMOS_AUTH_URL = "https://backend.thermos.gifts/api/v1/auth/api-token"


@dataclass(frozen=True)
class KeyCheck:
    state: str
    message: str


class MarketKeyChecker:
    def __init__(self, transport=None):
        self.transport = transport
        self.lock = asyncio.Lock()
        self.next_request = 0.0

    async def check(self, market, key):
        if not key:
            return KeyCheck("missing", "⚪ API-ключ не добавлен.")
        if market != "tonnel":
            return KeyCheck("unsupported", "🟡 Ключ сохранён. Проверка авторизации этой площадки пока недоступна: официальный запрос проверки ещё не подтверждён.")
        # The official API requires a query parameter. Mask its encoded forms too.
        for value in (key, quote(key, safe=""), quote_plus(key, safe="")):
            register_secret(value)
        async with self.lock:
            loop = asyncio.get_running_loop()
            delay = self.next_request - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_request = loop.time() + 0.5  # Official basic limit: 2 requests/s.
            try:
                return await asyncio.wait_for(self._thermos(key), timeout=12)
            except (TimeoutError, TimeoutException):
                return KeyCheck("timeout", "🟡 Проверка не завершена: Thermos не ответил вовремя. Ключ сохранён; попробуй проверить ещё раз позже.")
            except (HTTPError, OSError):
                return KeyCheck("network_error", "🟡 Проверка не завершена: нет соединения с API Thermos. Ключ сохранён; проверь доступ хостинга к backend.thermos.gifts и повтори позже.")

    async def _thermos(self, key):
        # No redirects: never forward a credential to a different endpoint.
        async with AsyncClient(timeout=10, follow_redirects=False, transport=self.transport) as client:
            async with client.stream("POST", THERMOS_AUTH_URL, params={"api_token": key}) as response:
                status = response.status_code
                if status in (400, 401, 422):
                    return KeyCheck("invalid", "❌ Thermos отклонил ключ. Проверь, что скопирован сам ключ из @thermos → /api, и добавь его заново.")
                if status == 403:
                    return KeyCheck("access_denied", "🟡 API Thermos отказал в доступе (403). Это может быть ограничение ключа или сети хостинга; действительность ключа не подтверждена.")
                if status == 429:
                    return KeyCheck("rate_limited", "🟡 Лимит запросов Thermos исчерпан. Ключ сохранён; повтори проверку позже.")
                if status >= 500:
                    return KeyCheck("service_error", "🟡 API Thermos временно недоступен. Ключ сохранён; повтори проверку позже.")
                if status != 200:
                    return KeyCheck("unexpected_response", "🟡 API Thermos вернул неожиданный ответ. Действительность ключа не подтверждена.")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > 65536:
                        return KeyCheck("unexpected_response", "🟡 Ответ Thermos не соответствует формату проверки. Действительность ключа не подтверждена.")
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    payload = None
                token = payload.get("token") if isinstance(payload, dict) else None
                if not isinstance(token, str) or not 8 <= len(token) <= 16384 or not token.strip():
                    return KeyCheck("unexpected_response", "🟡 Thermos не вернул токен авторизации. Действительность ключа не подтверждена.")
                register_secret(token)
                # Discard the JWT and account details; no trading or balance calls.
                return KeyCheck("verified", "✅ Thermos подтвердил API-ключ. Авторизация работает. Автоматический сбор данных пока не реализован.")
