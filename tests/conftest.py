"""Synthetic fixtures exclusively for tests; never loaded by the running bot."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from gift_radar.models import FEE_NAMES, Fee, Gift, Route, Sale, Settings


@pytest.fixture(autouse=True)
def synthetic_admins(monkeypatch):
    # Explicit test identities only. Production has no automatic administrator.
    monkeypatch.setenv("ADMIN_IDS", "1,2")
    monkeypatch.delenv("GIFT_RADAR_ADMIN_IDS", raising=False)


@pytest.fixture(autouse=True)
def no_live_marketplace_requests(monkeypatch):
    """Default verification transport is synthetic; explicit mocks remain usable."""
    import httpx
    from gift_radar import key_checks
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(401, json={"detail": "synthetic rejected credential"})
    client = key_checks.AsyncClient
    def mock_client(**kwargs):
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(response)
        return client(**kwargs)
    monkeypatch.setattr(key_checks, "AsyncClient", mock_client)
    return requests


@pytest.fixture
def now():
    return datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def gift(now):
    return Gift("portals", "test-only-lot", "TEST gift", Decimal("10"), "TON", now, "synthetic test fixture",
                collection="TEST collection", model="TEST model", backdrop="TEST background", pattern="TEST pattern",
                symbol="TEST symbol", serial=150, rare=False, signature=False,
                provenance={k: "synthetic fixture" for k in ("collection", "model", "backdrop", "pattern", "symbol", "serial", "rare", "signature")})


@pytest.fixture
def sales(now, gift):
    return [Sale(f"test-only-sale-{i}", replace(gift, market="tonnel", status="sold", price=Decimal(18 + i),
                                             listed_at=now - timedelta(hours=2, minutes=i)),
                 now - timedelta(minutes=i), "synthetic completed sale evidence") for i in range(7)]


@pytest.fixture
def route(now):
    return Route("portals", "tonnel", "TON", {k: Fee(Decimal("0"), source="synthetic test fee", updated_at=now, confirmed=True) for k in FEE_NAMES})


@pytest.fixture
def settings():
    return Settings(min_liquidity="низкая активность")
