"""Only documented, authorized data adapters belong here. No private Mini App API calls."""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import Gift, Sale, gift_from, instant, utcnow


@dataclass(frozen=True)
class Health:
    state: str
    message: str
    active_listings: bool = False
    completed_sales: bool = False


class MarketAdapter(Protocol):
    market: str
    async def get_active_listings(self) -> list[Gift]: ...
    async def get_completed_sales(self) -> list[Sale]: ...
    async def get_listing_details(self, listing_id: str) -> Gift | None: ...
    async def health_check(self) -> Health: ...


class DisabledAdapter:
    market = ""
    def __init__(self, store=None):
        self.store = store
    async def get_active_listings(self):
        return []
    async def get_completed_sales(self):
        return []
    async def get_listing_details(self, listing_id):
        return None
    async def health_check(self):
        if self.store:
            state, detail = self.store.connection(self.market).diagnosis()
            return Health(state, detail)
        return Health("disabled", "Официальный разрешённый API не подтверждён; автоматический сбор отключён")


class PortalsAdapter(DisabledAdapter):
    market = "portals"


class TonnelAdapter(DisabledAdapter):
    market = "tonnel"


class MRKTAdapter(DisabledAdapter):
    market = "mrkt"


def registry(store=None) -> list[MarketAdapter]:
    return [PortalsAdapter(store), TonnelAdapter(store), MRKTAdapter(store)]


def redact_raw(value):
    if isinstance(value, dict):
        return {k: redact_raw(v) for k, v in value.items() if not any(term in k.lower() for term in ("token", "auth", "cookie", "secret", "session", "password", "wallet", "buyer", "seller", "user"))}
    if isinstance(value, list):
        return [redact_raw(x) for x in value]
    return value


def read_authorized_export(path: str, authorized: bool, expected_market=None):
    """Internal interchange format; NOT a guessed marketplace response schema."""
    if not authorized:
        raise ValueError("Нужно подтвердить право использования экспорта: --authorized")
    p = Path(path)
    if p.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Экспорт превышает 10 MiB")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("source"), str) or not data["source"].strip():
        raise ValueError("Требуются schema_version=1 и реальный source")
    listings, sales = [], []
    now = utcnow()
    for name in ("listings", "sales"):
        if not isinstance(data.get(name, []), list) or len(data.get(name, [])) > 10000:
            raise ValueError("Некорректный размер списка")
    def normalize(row):
        if not isinstance(row, dict):
            raise ValueError("Лот должен быть объектом")
        return gift_from({**row, "source": data["source"], "raw": redact_raw(row.get("raw", {}))})
    for row in data.get("listings", []):
        gift = normalize(row)
        if expected_market and gift.market != expected_market:
            raise ValueError("Экспорт содержит другую площадку")
        if gift.updated_at > now or (gift.listed_at and gift.listed_at > gift.updated_at):
            raise ValueError("Время лота находится в будущем или противоречиво")
        listings.append(gift)
    for row in data.get("sales", []):
        gift = normalize(row["gift"])
        if expected_market and gift.market != expected_market:
            raise ValueError("Экспорт содержит другую площадку")
        sale = Sale(row["sale_id"], gift, instant(row["sold_at"]), row["evidence"])
        if sale.sold_at > now:
            raise ValueError("Продажа находится в будущем")
        sales.append(sale)
    # Validate the entire file before committing anything.
    return listings, sales
