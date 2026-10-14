from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from .models import FEE_NAMES, Gift, Route, Sale, Settings

D = Decimal
LIQUIDITY = ("недостаточно данных", "низкая активность", "средняя активность", "высокая активность")


def serial_band(n):
    if n is None:
        return None
    return len(str(n - 1)) if n > 1 else 0


@dataclass
class Stats:
    count: int = 0
    median: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    last: Sale | None = None
    median_hours: Decimal | None = None


def statistics(sales: list[Sale]) -> Stats:
    if not sales:
        return Stats()
    prices = [x.gift.price for x in sales]
    durations = [D(str((x.sold_at - x.gift.listed_at).total_seconds())) / 3600
                 for x in sales if x.gift.listed_at]
    return Stats(len(sales), median(prices), min(prices), max(prices), max(sales, key=lambda x: x.sold_at),
                 median(durations) if len(durations) >= 5 else None)


@dataclass
class Scenario:
    name: str
    resale: Decimal
    expenses: dict[str, Decimal | None]
    acquisition: Decimal | None
    proceeds: Decimal | None
    profit: Decimal | None
    roi: Decimal | None
    warnings: list[str]


def calculate(buy: Decimal, resale: Decimal, route: Route, now: datetime) -> Scenario:
    expenses = {key: route.fees[key].amount(resale if key in ("sell", "withdraw") else buy)
                if key in route.fees else None for key in FEE_NAMES}
    warnings = []
    for key in FEE_NAMES:
        fee = route.fees.get(key)
        if expenses[key] is None:
            warnings.append(f"Неизвестный расход: {key}")
        elif not fee.confirmed:
            warnings.append(f"{key}: задано пользователем, не подтверждено площадкой")
        if fee and fee.value is not None and (not fee.updated_at or not timedelta(0) <= now - fee.updated_at <= timedelta(days=30)):
            warnings.append(f"{key}: дата отсутствует или старше 30 дней")
    # Acquisition includes purchase price, buy fee, transfer, network, conversion, other.
    acquire_names = ("buy", "transfer", "network", "conversion", "other")
    acquisition = buy + sum((expenses[k] for k in acquire_names), D(0)) if all(expenses[k] is not None for k in acquire_names) else None
    proceeds = resale - expenses["sell"] - expenses["withdraw"] if expenses["sell"] is not None and expenses["withdraw"] is not None else None
    profit = proceeds - acquisition if proceeds is not None and acquisition is not None else None
    roi = profit / acquisition * 100 if profit is not None and acquisition and acquisition > 0 else None
    return Scenario("", resale, expenses, acquisition, proceeds, profit, roi, warnings)


@dataclass
class Estimate:
    listing: Gift
    target: str
    period_days: int
    sales: list[Sale] = field(default_factory=list)
    day: Stats = field(default_factory=Stats)
    period: Stats = field(default_factory=Stats)
    level: int | None = None
    liquidity: str = LIQUIDITY[0]
    confidence: str = "низкая"
    active_count: int | None = None
    scenarios: list[Scenario] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def estimate(listing: Gift, sales: list[Sale], settings: Settings, target: str,
             route: Route | None, now: datetime, active: list[Gift] | None = None) -> Estimate:
    result = Estimate(listing, target, settings.history_days)
    if not listing.collection or not listing.model:
        result.warnings.append("Нет коллекции или модели: сопоставление недоступно")
        return result
    lower = now - timedelta(days=settings.history_days)
    # Unknown signatures are never converted to clean; incomparable signatures excluded.
    candidates = [s for s in sales if lower <= s.sold_at <= now and
                  s.gift.market == target and s.gift.currency == listing.currency and
                  s.gift.collection == listing.collection and s.gift.model == listing.model and
                  s.gift.signature == listing.signature and s.gift.rare == listing.rare]
    discounted = False
    if len(candidates) < settings.min_sales and listing.signature is True and settings.signature_discount is not None:
        unsigned = [s for s in sales if lower <= s.sold_at <= now and s.gift.market == target and
                    s.gift.currency == listing.currency and s.gift.collection == listing.collection and
                    s.gift.model == listing.model and s.gift.signature is False and s.gift.rare == listing.rare]
        if len(unsigned) >= settings.min_sales:
            candidates = unsigned
            discounted = True
            result.warnings.append(f"Аналоги без подписи; скидка {settings.signature_discount}% задана пользователем, не рынком")
    # Keep unique properties strict; never use ordinary gifts to value rare ones.
    if listing.rare is True:
        candidates = [s for s in candidates if s.gift.serial == listing.serial and s.gift.pattern == listing.pattern and s.gift.backdrop == listing.backdrop and s.gift.symbol == listing.symbol]
    levels = [
        [s for s in candidates if all(getattr(s.gift, k) == getattr(listing, k) for k in ("backdrop", "pattern", "symbol")) and serial_band(s.gift.serial) == serial_band(listing.serial)],
        [s for s in candidates if s.gift.backdrop == listing.backdrop],
        candidates,
    ]
    chosen = levels[0]
    result.level = 0
    for i, matches in enumerate(levels):
        chosen, result.level = matches, i
        if len(matches) >= settings.min_sales:
            break
    result.sales = sorted(chosen, key=lambda s: s.sold_at, reverse=True)
    result.period = statistics(chosen)
    result.day = statistics([s for s in chosen if s.sold_at >= now - timedelta(hours=24)])
    if active is not None:
        result.active_count = sum(x.status == "active" and x.market == target and x.currency == listing.currency and
                                  x.collection == listing.collection and x.model == listing.model and
                                  x.signature == listing.signature for x in active)
    if len(chosen) < settings.min_sales:
        result.warnings.append("Оценка ненадёжна: найдено мало похожих продаж")
        return result
    if result.level:
        result.warnings.append("Расширено сравнение: " + ("узор, символ и диапазон номера могут отличаться" if result.level == 1 else "фон, узор, символ и диапазон номера могут отличаться"))
    if listing.signature is None:
        result.warnings.append("Подпись на подарке: не удалось проверить")
    missing = [k for k in ("backdrop", "pattern", "symbol", "serial", "rare") if getattr(listing, k) is None]
    if missing:
        result.warnings.append("Неизвестные характеристики: " + ", ".join(missing))
    recent = result.period.last and now - result.period.last.sold_at <= timedelta(days=2)
    frequency = D(len(chosen)) / settings.history_days
    result.liquidity = LIQUIDITY[3] if frequency >= 5 and recent else LIQUIDITY[2] if frequency >= 1 and recent else LIQUIDITY[1]
    result.confidence = "высокая" if len(chosen) >= 20 and result.level == 0 else "средняя"
    if result.level == 2 or missing or listing.signature is None or discounted:
        result.confidence = "низкая"
    if route is None:
        route = Route(listing.market, target, listing.currency)
    if (route.buy_market, route.sell_market, route.currency) != (listing.market, target, listing.currency):
        raise ValueError("Route does not match listing/target")
    prices = sorted(s.gift.price for s in chosen)
    def quantile(q):
        index = D(len(prices) - 1) * q
        lo = int(index)
        hi = min(lo + 1, len(prices) - 1)
        return prices[lo] + (prices[hi] - prices[lo]) * (index - lo)
    for name, price in zip(("Осторожный (Q25)", "Медианный", "Оптимистичный (Q75)"), (quantile(D("0.25")), median(prices), quantile(D("0.75")))):
        if discounted:
            price *= 1 - settings.signature_discount / 100
        scenario = calculate(listing.price, price, route, now)
        scenario.name = name
        result.scenarios.append(scenario)
    return result


def eligible(value: Estimate, settings: Settings, now: datetime) -> bool:
    x = value.listing
    if x.status != "active" or x.updated_at < now - timedelta(minutes=30) or x.updated_at > now:
        return False
    if x.currency != settings.calculation_currency or x.market not in settings.buy_markets or value.target not in settings.sell_markets:
        return False
    if x.price > settings.max_price or value.period.count < settings.min_sales or value.confidence == "низкая":
        return False
    if LIQUIDITY.index(value.liquidity) < LIQUIDITY.index(settings.min_liquidity):
        return False
    for attr, values in (("collection", settings.collections), ("model", settings.models), ("backdrop", settings.backdrops), ("pattern", settings.patterns), ("symbol", settings.symbols)):
        if values and getattr(x, attr) not in values:
            return False
    if settings.signature != "any" and {"yes": True, "no": False, "unknown": None}[settings.signature] is not x.signature:
        return False
    if not value.scenarios:
        return False
    v = value.scenarios[1]
    if any("дата отсутствует" in w for w in v.warnings):
        return False
    return v.profit is not None and v.roi is not None and v.profit >= settings.min_profit and v.roi >= settings.min_roi


@dataclass(frozen=True)
class Rate:
    base: str
    quote: str
    value: Decimal
    at: datetime
    source: str

    def convert(self, amount: Decimal, now: datetime) -> Decimal | None:
        if not self.value.is_finite() or self.value <= 0 or not self.source or not timedelta(0) <= now - self.at <= timedelta(hours=1):
            return None
        return amount * self.value
