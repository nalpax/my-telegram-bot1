"""Thermos access source: one key for Portals, MRKT and Tonnel, kept separate from data readiness."""
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from httpx import HTTPError, TimeoutException

from . import key_checks
from .key_checks import KeyCheck, MarketKeyChecker
from .models import MARKETS, instant, utcnow

THERMOS = "thermos"
# Thermos documentation lists TONNEL, PORTALS and MRKT as market sources of its API.
COVERED = MARKETS
LINKS = {"bot": "https://t.me/thermos", "docs": "https://docs.swap.coffee/technical-guides/thermos/backend-api",
         "news": "https://t.me/thermos_news"}
MARKET_LINKS = {"portals": "https://t.me/portals", "tonnel": "https://market.tonnel.network/", "mrkt": "https://t.me/mrkt"}
TITLES = {"portals": "Portals", "mrkt": "MRKT", "tonnel": "Tonnel"}
# Exactly what is missing for automatic collection; endpoints are never guessed.
MISSING_CONTRACT = ("Thermos не опубликовал в доступной документации метод чтения рыночных лотов "
                    "и истории продаж (путь, параметры, формат ответа, передача JWT)")

TRANSIENT = frozenset({"network_error", "timeout", "rate_limited", "service_error"})
ATTENTION = frozenset({"invalid", "access_denied", "unexpected_response", "storage_error"})
SHORT = {"missing": "не подключён", "saved": "ключ сохранён, не проверен", "verified": "подтверждён",
         "invalid": "ключ отклонён", "access_denied": "доступ запрещён (403)", "unexpected_response": "неожиданный ответ",
         "storage_error": "ключ не читается", "network_error": "нет соединения", "timeout": "таймаут",
         "rate_limited": "лимит запросов (429)", "service_error": "сервер недоступен"}
JWT_LIFETIME = timedelta(hours=23)  # Documented JWT lifetime is 24 hours.
RETRY_AFTER = timedelta(minutes=10)  # Back-off after a temporary failure in background checks.


@dataclass(frozen=True)
class Access:
    state: str
    detail: str
    checked_at: datetime | None
    verified_at: datetime | None
    key_saved: bool

    @property
    def usable(self):
        """Confirmed now, or confirmed earlier and only a temporary failure since."""
        return self.state == "verified" or (self.state in TRANSIENT and self.verified_at is not None)

    @property
    def icon(self):
        return "✅" if self.state == "verified" else "🔒" if self.state == "missing" else "⚠️"

    @property
    def label(self):
        text = SHORT.get(self.state, self.state)
        if self.state == "verified" and self.verified_at:
            text += f" {self.verified_at:%d.%m %H:%M} UTC"
        elif self.state in TRANSIENT and self.verified_at:
            text = f"подтверждён {self.verified_at:%d.%m %H:%M} UTC; последняя проверка: {text}"
        return text


class ThermosSource:
    name = THERMOS

    def __init__(self, store, checker=None, retry_delay=1.5):
        self.store = store
        self.checker = checker or MarketKeyChecker()
        self.retry_delay = retry_delay
        self.jwt = None  # Memory only; a restart simply requests a new one.
        self.jwt_until = None
        self.not_before = None
        self.checking = False
        self.lock = asyncio.Lock()

    def key(self):
        # Previous versions saved the Thermos key in the "tonnel" slot.
        return self.store.credential(THERMOS) or self.store.credential("tonnel")

    def key_saved(self):
        return self.store.has_credential(THERMOS) or self.store.has_credential("tonnel")

    def access(self):
        state, detail, checked, verified = self.store.source_state(THERMOS)
        saved = self.key_saved()
        if not saved:
            state, detail = "missing", "Ключ Thermos не добавлен."
        elif state == "missing":
            state, detail = "saved", "Ключ сохранён, проверка ещё не выполнялась."
        return Access(state, detail, instant(checked) if checked else None, instant(verified) if verified else None, saved)

    def save_key(self, value):
        self.store.save_credential(THERMOS, value)
        self.store.delete_credential("tonnel")
        self.store.set_source_state(THERMOS, "saved", "Ключ сохранён, проверка ещё не выполнялась.")
        self.jwt = self.jwt_until = self.not_before = None

    def delete_key(self):
        self.store.delete_credential(THERMOS)
        self.store.delete_credential("tonnel")
        self.store.set_source_state(THERMOS, "missing", "Ключ Thermos удалён.")
        self.jwt = self.jwt_until = self.not_before = None

    async def verify(self) -> KeyCheck:
        """User-initiated or expiry-driven auth check. Never trades, never reads balances."""
        async with self.lock:
            try:
                key = self.key()
            except ValueError:
                result = KeyCheck("storage_error", "⚠️ Не удалось прочитать сохранённый ключ. Восстанови файл шифрования из резервной копии.")
                self.store.set_source_state(THERMOS, result.state, result.message)
                return result
            if not key:
                return KeyCheck("missing", "🔒 Ключ Thermos не добавлен.")
            self.checking = True
            try:
                result = await self.checker.check(THERMOS, key)
                if result.state in ("network_error", "timeout", "service_error"):
                    # One bounded retry; a network failure never marks the key invalid.
                    await asyncio.sleep(self.retry_delay)
                    result = await self.checker.check(THERMOS, key)
            finally:
                self.checking = False
            now = utcnow()
            verified = result.state == "verified"
            self.store.set_source_state(THERMOS, result.state, result.message, verified=verified)
            self.jwt = result.token if verified else None
            self.jwt_until = now + JWT_LIFETIME if verified else None
            self.not_before = None if verified else now + RETRY_AFTER if result.state in TRANSIENT else None
            return result

    async def ensure_access(self) -> Access:
        """Background use: refresh auth only when the JWT lifetime ran out."""
        access = self.access()
        now = utcnow()
        if access.state == "missing" or access.state in ATTENTION:
            # A rejected key is not retried automatically; the admin replaces or re-checks it.
            return access
        if self.jwt and self.jwt_until and now < self.jwt_until and access.state == "verified":
            return access
        if self.not_before and now < self.not_before:
            return access
        await self.verify()
        return self.access()

    async def read(self, method, path, body=None, _retry=True):
        """Documented read-only Thermos request with the in-memory JWT (Bearer scheme from Swagger)."""
        if path not in READ_PATHS:
            raise ThermosError("unexpected_response")  # Only reading endpoints are ever called.
        access = await self.ensure_access()
        if not access.usable or not self.jwt:
            raise ThermosError(access.state if access.state != "verified" else "missing")
        async with self.checker.lock:
            await self.checker.pace()
            try:
                async with key_checks.AsyncClient(timeout=15, follow_redirects=False, transport=self.checker.transport) as client:
                    response = await client.request(method, API + path, json=body,
                                                    headers={"Authorization": f"Bearer {self.jwt}", "Accept": "application/json"})
            except (TimeoutError, TimeoutException):
                raise ThermosError("timeout") from None
            except (HTTPError, OSError):
                raise ThermosError("network_error") from None
        if response.status_code == 401 and _retry:
            # Expired authorization: refresh once with the saved key.
            self.jwt = self.jwt_until = None
            return await self.read(method, path, body, _retry=False)
        if response.status_code != 200:
            raise ThermosError({401: "invalid", 403: "access_denied", 429: "rate_limited"}.get(
                response.status_code, "service_error" if response.status_code >= 500 else "unexpected_response"))
        if len(response.content) > 2 * 1024 * 1024:
            raise ThermosError("unexpected_response")
        try:
            return json.loads(response.content)
        except (ValueError, UnicodeError, RecursionError):
            raise ThermosError("unexpected_response") from None

    async def my_lots(self):
        data = await self.read("GET", "/api/v1/gifts/me/on-sale")
        if not isinstance(data, list):
            raise ThermosError("unexpected_response")
        return [lot(x) for x in data if isinstance(x, dict)]

    async def my_sales(self):
        data = await self.read("POST", "/api/v1/accounts/history", {"event_types": ["GIFT_SELL"], "limit": 50})
        if not isinstance(data, list):
            raise ThermosError("unexpected_response")
        result = []
        for event in data:
            payload = event.get("payload") if isinstance(event, dict) else None
            if isinstance(payload, dict) and isinstance(event.get("id"), int):
                gift = payload.get("gift") if isinstance(payload.get("gift"), dict) else {}
                result.append({"id": event["id"], "at": str(event.get("occurred_at", ""))[:16].replace("T", " "),
                               "title": title(gift), "amount": ton(payload.get("amount")),
                               "market": str(payload.get("marketplace") or "")})
        return sorted(result, key=lambda x: x["id"])


API = "https://backend.thermos.gifts"
READ_PATHS = frozenset({"/api/v1/gifts/me/on-sale", "/api/v1/accounts/history"})
ERRORS = {"timeout": "Thermos не ответил вовремя", "network_error": "нет соединения с Thermos",
          "rate_limited": "лимит запросов Thermos, повтори через минуту", "service_error": "сервер Thermos временно недоступен",
          "access_denied": "Thermos отказал в доступе (403)", "invalid": "Thermos не принял авторизацию — проверь ключ",
          "unexpected_response": "Thermos ответил в неожиданном формате", "missing": "доступ Thermos не подтверждён"}


class ThermosError(Exception):
    def __init__(self, state):
        super().__init__(state)
        self.state = state
        self.text = ERRORS.get(state, ERRORS["missing"] if state in SHORT else state)


def ton(value):
    """Amounts in the Thermos API are nanotons (1 TON = 10^9); decimals with a point are already TON."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    if "." not in str(value) and amount >= 1_000_000:
        amount /= Decimal(1_000_000_000)
    return amount


def title(meta):
    name = meta.get("name") or "Подарок"
    return f"{name} #{meta['number']}" if isinstance(meta.get("number"), int) else str(name)


def lot(item):
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    return {"title": title(meta), "price": ton(item.get("price_ton")), "market": str(item.get("market_source") or ""),
            "status": str(item.get("sale_status") or ""), "sold": ton(item.get("sold_price_ton")) if item.get("sold_price_ton") else None,
            "model": meta.get("model"), "backdrop": meta.get("backdrop")}
