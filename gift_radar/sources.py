"""Thermos access source: one key for Portals, MRKT and Tonnel, kept separate from data readiness."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

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
        self.jwt_until = None  # The JWT itself is discarded; only its expected lifetime is tracked.
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
        self.jwt_until = self.not_before = None

    def delete_key(self):
        self.store.delete_credential(THERMOS)
        self.store.delete_credential("tonnel")
        self.store.set_source_state(THERMOS, "missing", "Ключ Thermos удалён.")
        self.jwt_until = self.not_before = None

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
        if self.jwt_until and now < self.jwt_until and access.state == "verified":
            return access
        if self.not_before and now < self.not_before:
            return access
        await self.verify()
        return self.access()
