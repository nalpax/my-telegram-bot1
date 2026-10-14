import asyncio
import logging

from .analytics import eligible, estimate
from .models import utcnow
from .storage import listing_key
from .security import is_admin

log = logging.getLogger(__name__)


class Radar:
    def __init__(self, store, adapters):
        self.store = store
        self.adapters = adapters
        self.lock = asyncio.Lock()
        self.last_error = None

    async def synchronize(self):
        for adapter in self.adapters:
            try:
                health = await asyncio.wait_for(adapter.health_check(), timeout=20)
                self.store.set_market(adapter.market, health.state, health.message)
                if health.state != "ready":
                    continue
                listings = await asyncio.wait_for(adapter.get_active_listings(), timeout=30) if health.active_listings else []
                sales = await asyncio.wait_for(adapter.get_completed_sales(), timeout=30) if health.completed_sales else []
                self.store.ingest(listings, sales)
            except (TimeoutError, OSError, ValueError, TypeError, KeyError) as exc:
                category = type(exc).__name__
                self.store.set_market(adapter.market, "unavailable", "Источник временно недоступен; данные не обновлены")
                self.store.error(adapter.market, category)
                log.warning("sync_error market=%s category=%s", adapter.market, category)

    def analyses(self, user, *, now=None, key=None):
        if not is_admin(user):
            raise PermissionError("Administrator access required")
        now = now or utcnow()
        settings = self.store.settings(user)
        listings = self.store.listings()
        sales = self.store.sales(now, settings.history_days)
        hidden = self.store.marked("hidden", user)
        result = []
        for gift in listings:
            if key and listing_key(gift) != key:
                continue
            if not key and (listing_key(gift) in hidden or gift.market not in settings.buy_markets):
                continue
            for target in settings.sell_markets:
                # Imported snapshots do not establish a complete active market inventory.
                route = self.store.route(user, gift.market, target, gift.currency)
                result.append(estimate(gift, sales, settings, target, route, now, active=None))
        result.sort(key=lambda x: x.scenarios[1].profit if x.scenarios and x.scenarios[1].profit is not None else float('-inf'), reverse=True)
        return result

    async def tick(self, sender):
        if self.lock.locked():
            return
        async with self.lock:
            await self.synchronize()
            now = utcnow()
            for user in self.store.users():
                if not is_admin(user):
                    continue
                settings = self.store.settings(user)
                if not settings.alerts or not self.store.due(user, now, settings.interval_minutes):
                    continue
                self.store.attempted(user, now)
                sent = 0
                for value in self.analyses(user, now=now):
                    if not eligible(value, settings, now):
                        continue
                    key = listing_key(value.listing)
                    if not self.store.claim_alert(user, key, now):
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
