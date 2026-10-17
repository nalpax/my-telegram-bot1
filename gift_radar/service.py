import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field

from .analytics import rejection, estimate
from .models import MARKETS, utcnow
from .sources import ATTENTION, COVERED, TITLES, TRANSIENT, ThermosSource
from .storage import listing_key
from .security import is_admin

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    market: str
    state: str
    detail: str
    listings: int = 0
    sales: int = 0


@dataclass
class LoadReport:
    results: dict
    counts: dict
    best: list = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)
    analysed: int = 0


class Radar:
    def __init__(self, store, adapters, source=None):
        self.store = store
        self.adapters = adapters
        self.source = source or ThermosSource(store)
        self.lock = asyncio.Lock()
        self.last_error = None

    def available(self, market):
        """A marketplace can be switched on once access is confirmed or real data exists."""
        return (market in COVERED and self.source.access().usable) or self.store.has_market_data(market)

    def participates(self, market):
        return self.store.market_flag(market) is not False and self.available(market)

    async def synchronize(self, markets=None):
        results = {}
        for adapter in self.adapters:
            market = adapter.market
            if markets is not None and market not in markets:
                continue
            if self.store.market_flag(market) is False:
                # A switched-off marketplace makes no new background requests.
                results[market] = SyncResult(market, "off", "Площадка выключена: запросы не выполнялись")
                continue
            try:
                health = await asyncio.wait_for(adapter.health_check(), timeout=40)
                if health.state != "ready":
                    self.store.set_market(market, health.state, health.message)
                    results[market] = SyncResult(market, health.state, health.message)
                    continue
                listings = await asyncio.wait_for(adapter.get_active_listings(), timeout=30) if health.active_listings else []
                sales = await asyncio.wait_for(adapter.get_completed_sales(), timeout=30) if health.completed_sales else []
                self.store.ingest(listings, sales)
                self.store.set_market(market, "ready", health.message, data_ok=True)
                results[market] = SyncResult(market, "ready", health.message, len(listings), len(sales))
            except (TimeoutError, OSError, ValueError, TypeError, KeyError) as exc:
                category = type(exc).__name__
                detail = "Источник временно недоступен; данные не обновлены"
                self.store.set_market(market, "unavailable", detail)
                self.store.error(market, category)
                results[market] = SyncResult(market, "unavailable", detail)
                log.warning("sync_error market=%s category=%s", market, category)
        return results

    def analyses(self, user, *, now=None, key=None):
        if not is_admin(user):
            raise PermissionError("Administrator access required")
        now = now or utcnow()
        settings = self.store.settings(user)
        listings = self.store.listings()
        sales = self.store.sales(now, settings.history_days)
        hidden = self.store.marked("hidden", user)
        active = {m for m in MARKETS if self.participates(m)}
        result = []
        for gift in listings:
            if key and listing_key(gift) != key:
                continue
            if not key and (listing_key(gift) in hidden or gift.market not in settings.buy_markets or gift.market not in active
                            or (not settings.include_rare and gift.rare is True)):
                continue
            for target in settings.sell_markets:
                if target not in active:
                    continue
                # Imported snapshots do not establish a complete active market inventory.
                route = self.store.route(user, gift.market, target, gift.currency)
                result.append(estimate(gift, sales, settings, target, route, now, active=None))
        result.sort(key=lambda x: x.scenarios[1].profit if x.scenarios and x.scenarios[1].profit is not None else float('-inf'), reverse=True)
        return result

    def evaluate(self, user, now=None):
        """Best confirmed opportunity per listing, and why the rest are not opportunities."""
        now = now or utcnow()
        settings = self.store.settings(user)
        values = self.analyses(user, now=now)
        best, seen, reasons = [], set(), Counter()
        for value in values:
            reason = rejection(value, settings, now)
            key = listing_key(value.listing)
            if reason is None and key not in seen:
                seen.add(key)
                best.append(value)
            elif reason is not None:
                reasons[reason] += 1
        return best, reasons, len({listing_key(v.listing) for v in values})

    async def first_load(self, user, markets=MARKETS):
        """Immediate load after the admin connects or refreshes; does not wait for the interval."""
        if not is_admin(user):
            raise PermissionError("Administrator access required")
        async with self.lock:
            results = await self.synchronize(markets)
        now = utcnow()
        best, reasons, analysed = self.evaluate(user, now)
        counts = {m: self.store.market_counts(m, now) for m in MARKETS}
        return LoadReport(results, counts, best[:3], reasons, analysed)

    def problems(self):
        """Connection problems worth an error notice, keyed by subject."""
        found = {}
        if self.source.key_saved():
            access = self.source.access()
            if access.state in ATTENTION or (access.state in TRANSIENT and not access.usable):
                found["thermos"] = access.state
            else:
                found["thermos"] = "ok"
        for market in MARKETS:
            state = self.store.market_data(market)[0]
            found[f"data:{market}"] = "unavailable" if state == "unavailable" else "ok"
        return found

    async def notify_problems(self, notifier):
        problems = self.problems()
        for user in self.store.users():
            if not is_admin(user):
                continue
            settings = self.store.settings(user)
            if not settings.alerts or not settings.error_alerts:
                continue
            for subject, state in problems.items():
                previous = self.store.claim_error_notice(user, subject, state)
                if previous is False:
                    continue
                title = "Thermos" if subject == "thermos" else TITLES[subject.split(":")[1]]
                if state == "ok":
                    message = f"✅ {title}: подключение восстановлено."
                elif subject == "thermos":
                    message = f"⚠️ Thermos: {self.source.access().detail}\nОткрой «Площадки», чтобы проверить или заменить ключ."
                else:
                    message = f"⚠️ {title}: не удалось загрузить данные; источник временно недоступен. Остальные площадки работают."
                if not is_admin(user):
                    break
                try:
                    await notifier(user, message)
                except Exception as exc:
                    self.store.error("telegram", type(exc).__name__)
                    log.warning("error_notice_failed category=%s", type(exc).__name__)

    async def tick(self, sender, notifier=None):
        if self.lock.locked():
            return
        async with self.lock:
            await self.synchronize()
            if notifier:
                await self.notify_problems(notifier)
            now = utcnow()
            for user in self.store.users():
                if not is_admin(user):
                    continue
                settings = self.store.settings(user)
                if not settings.alerts or not self.store.due(user, now, settings.interval_minutes):
                    continue
                self.store.attempted(user, now)
                sent = 0
                best, _, _ = self.evaluate(user, now)
                for value in best:
                    key = listing_key(value.listing)
                    if not self.store.claim_alert(user, key, now, value.listing.price, repeat_on_drop=not settings.only_new):
                        continue
                    if not is_admin(user):
                        self.store.finish_alert(user, key, "access_revoked")
                        break
                    try:
                        await sender(user, value)
                    except Exception as exc:
                        self.store.finish_alert(user, key, "unknown_delivery")
                        self.store.error("telegram", type(exc).__name__)
                        log.warning("alert_send_failed category=%s", type(exc).__name__)
                        break
                    else:
                        self.store.finish_alert(user, key, "sent")
                        sent += 1
                    if sent >= 3:
                        break
                    await asyncio.sleep(1)

