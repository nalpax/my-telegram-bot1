from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

MARKETS = ("portals", "tonnel", "mrkt")
CURRENCIES = ("TON", "USD", "RUB")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def money(value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean is not an amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid decimal amount") from None
    if not result.is_finite() or result < 0 or (positive and result == 0) or result > Decimal("1e15"):
        raise ValueError("Amount must be finite and nonnegative")
    return result


def instant(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    p = urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password or any(ord(c) < 33 for c in value):
        raise ValueError("Public HTTPS URL required")
    return value


@dataclass(frozen=True)
class Gift:
    market: str
    listing_id: str
    name: str
    price: Decimal
    currency: str
    updated_at: datetime
    source: str
    url: str | None = None
    collection: str | None = None
    model: str | None = None
    backdrop: str | None = None
    pattern: str | None = None
    symbol: str | None = None
    serial: int | None = None
    rare: bool | None = None
    signature: bool | None = None
    signature_text: str | None = None
    listed_at: datetime | None = None
    status: str = "active"
    provenance: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in ("listing_id", "name", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not 1 <= len(value) <= 1000:
                raise ValueError("Invalid textual identity")
        for name in ("collection", "model", "backdrop", "pattern", "symbol", "signature_text"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 1000):
                raise ValueError("Invalid gift attribute")
        if not isinstance(self.raw, dict) or not isinstance(self.provenance, dict):
            raise ValueError("raw/provenance must be objects")
        if self.market not in MARKETS or not self.listing_id or not self.name or not self.source:
            raise ValueError("Missing or invalid identity/provenance")
        if self.currency not in CURRENCIES or self.status not in ("active", "sold", "withdrawn"):
            raise ValueError("Unsupported currency/status")
        object.__setattr__(self, "price", money(self.price, positive=True))
        object.__setattr__(self, "updated_at", instant(self.updated_at))
        object.__setattr__(self, "url", safe_url(self.url))
        if self.listed_at is not None:
            object.__setattr__(self, "listed_at", instant(self.listed_at))
        for v in (self.signature, self.rare):
            if v is not None and type(v) is not bool:
                raise ValueError("Unknown booleans must be null")
        if self.serial is not None and (type(self.serial) is not int or self.serial < 1):
            raise ValueError("Invalid serial number")
        if self.signature_text and self.signature is not True:
            raise ValueError("Signature text requires confirmed signature")
        for attr in ("collection", "model", "backdrop", "pattern", "symbol", "serial", "rare", "signature", "signature_text"):
            if getattr(self, attr) is not None and not self.provenance.get(attr):
                raise ValueError(f"Missing provenance for {attr}")


@dataclass(frozen=True)
class Sale:
    sale_id: str
    gift: Gift
    sold_at: datetime
    evidence: str

    def __post_init__(self):
        object.__setattr__(self, "sold_at", instant(self.sold_at))
        if not isinstance(self.sale_id, str) or not isinstance(self.evidence, str) or not self.sale_id or not self.evidence or self.gift.status != "sold":
            raise ValueError("Completed sale requires evidence, ID and sold status")
        if self.gift.listed_at and self.gift.listed_at > self.sold_at:
            raise ValueError("Sale precedes listing")


@dataclass(frozen=True)
class Fee:
    value: Decimal | None = None
    percent: bool = False
    source: str = "неизвестно"
    updated_at: datetime | None = None
    confirmed: bool = False

    def __post_init__(self):
        if self.value is not None:
            object.__setattr__(self, "value", money(self.value))
            if self.percent and self.value > 100:
                raise ValueError("Fee percentage above 100")
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", instant(self.updated_at))

    def amount(self, basis: Decimal) -> Decimal | None:
        if self.value is None:
            return None
        return basis * self.value / 100 if self.percent else self.value


FEE_NAMES = ("buy", "sell", "transfer", "withdraw", "network", "conversion", "other")


@dataclass(frozen=True)
class Route:
    buy_market: str
    sell_market: str
    currency: str
    fees: dict[str, Fee] = field(default_factory=dict)
    conditions: str = "Расходы для указанного маршрута"

    def __post_init__(self):
        if self.buy_market not in MARKETS or self.sell_market not in MARKETS or self.currency not in CURRENCIES:
            raise ValueError("Invalid route")
        if set(self.fees) - set(FEE_NAMES):
            raise ValueError("Unknown fee name")


@dataclass
class Settings:
    calculation_currency: str = "TON"
    min_profit: Decimal = Decimal("1")
    min_roi: Decimal = Decimal("10")
    max_price: Decimal = Decimal("100")
    min_sales: int = 5
    min_liquidity: str = "средняя активность"
    collections: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    backdrops: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    signature: str = "any"
    buy_markets: list[str] = field(default_factory=lambda: list(MARKETS))
    sell_markets: list[str] = field(default_factory=lambda: list(MARKETS))
    display_currencies: list[str] = field(default_factory=lambda: ["TON", "USD", "RUB"])
    history_days: int = 7
    alerts: bool = False
    interval_minutes: int = 60
    signature_discount: Decimal | None = None

    def validate(self):
        for key in ("min_profit", "min_roi", "max_price"):
            setattr(self, key, money(getattr(self, key)))
        if self.signature_discount is not None:
            self.signature_discount = money(self.signature_discount)
            if self.signature_discount > 100:
                raise ValueError("Скидка должна быть от 0 до 100%")
        if self.history_days not in (7, 14, 30) or type(self.min_sales) is not int or not 3 <= self.min_sales <= 10000:
            raise ValueError("Период: 7/14/30; минимум продаж: 3–10000")
        if type(self.interval_minutes) is not int or not 5 <= self.interval_minutes <= 10080:
            raise ValueError("Интервал: 5–10080 минут")
        if self.signature not in ("any", "yes", "no", "unknown"):
            raise ValueError("Некорректный фильтр подписи")
        if self.calculation_currency not in CURRENCIES:
            raise ValueError("Неизвестная валюта")
        if not 1 <= len(self.display_currencies) <= 3 or len(set(self.display_currencies)) != len(self.display_currencies) or set(self.display_currencies) - set(CURRENCIES):
            raise ValueError("Выберите 1–3 разные валюты: TON, USD, RUB")
        if not self.buy_markets or not self.sell_markets or (set(self.buy_markets) | set(self.sell_markets)) - set(MARKETS):
            raise ValueError("Выберите площадки: portals, tonnel, mrkt")
        if self.min_liquidity not in ("высокая активность", "средняя активность", "низкая активность", "недостаточно данных"):
            raise ValueError("Некорректная ликвидность")
        for name in ("collections", "models", "backdrops", "patterns", "symbols"):
            v = getattr(self, name)
            if not isinstance(v, list) or len(v) > 50 or any(not isinstance(x, str) or not 1 <= len(x) <= 100 for x in v):
                raise ValueError("Список должен содержать до 50 коротких названий")
        return self


def dumps(value) -> str:
    def encode(obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(type(obj).__name__)
    return json.dumps(asdict(value), default=encode, ensure_ascii=False)


def gift_from(data: dict) -> Gift:
    return Gift(**data)


def sale_from(data: dict) -> Sale:
    return Sale(**{**data, "gift": gift_from(data["gift"])})


def route_from(data: dict) -> Route:
    return Route(**{**data, "fees": {k: Fee(**v) for k, v in data.get("fees", {}).items()}})
