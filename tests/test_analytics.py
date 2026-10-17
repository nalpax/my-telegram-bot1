from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from gift_radar.analytics import Rate, calculate, eligible, estimate, statistics
from gift_radar.models import Fee, Gift, Route, Settings, dumps, gift_from, money


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", True, "oops", "0"])
def test_invalid_price(value, gift):
    with pytest.raises(ValueError):
        replace(gift, price=value)


def test_unknown_is_not_clean(gift):
    unknown = replace(gift, signature=None)
    assert unknown.signature is None
    with pytest.raises(ValueError):
        replace(unknown, signature_text="TEST inscription")


def test_property_provenance_required(gift):
    with pytest.raises(ValueError):
        replace(gift, provenance={})


def test_roundtrip(gift):
    import json
    assert gift_from(json.loads(dumps(gift))) == gift


def test_completed_median_and_daily(gift, sales, settings, route, now):
    v = estimate(gift, sales, settings, "tonnel", route, now)
    assert v.period.count == 7 and v.day.count == 7
    assert v.period.median == D("21")
    assert v.period.minimum == 18 and v.period.maximum == 24
    assert v.period.last.sold_at == now
    assert v.scenarios[0].resale == D("19.5")
    assert v.scenarios[2].resale == D("22.5")
    assert v.scenarios[1].profit == 11 and v.scenarios[1].roi == 110
    assert eligible(v, settings, now)


def test_exact_analog_expansion_disclosed(gift, sales, settings, route, now):
    sales = [replace(s, gift=replace(s.gift, backdrop="other TEST background")) for s in sales]
    v = estimate(gift, sales, settings, "tonnel", route, now)
    assert v.level == 2 and v.confidence == "низкая"
    assert any("Расширено" in x for x in v.warnings)
    assert not eligible(v, settings, now)


@pytest.mark.parametrize("attr,value", [("collection", "other"), ("model", "other"), ("currency", "USD"), ("signature", True), ("rare", True)])
def test_incomparable_sales_rejected(attr, value, gift, sales, settings, route, now):
    wrong = [replace(s, gift=replace(s.gift, **{attr: value})) for s in sales]
    assert estimate(gift, wrong, settings, "tonnel", route, now).period.count == 0


def test_future_and_old_sales_rejected(gift, sales, settings, route, now):
    wrong = [replace(s, sold_at=now + timedelta(hours=1)) for s in sales]
    wrong += [replace(s, gift=replace(s.gift, listed_at=None), sold_at=now - timedelta(days=31)) for s in sales]
    assert estimate(gift, wrong, settings, "tonnel", route, now).period.count == 0


def test_insufficient_data_no_scenarios(gift, sales, settings, route, now):
    v = estimate(gift, sales[:2], settings, "tonnel", route, now)
    assert not v.scenarios and not eligible(v, settings, now)
    assert v.liquidity == "недостаточно данных"


def test_unknown_cost_blocks_profit_filter(gift, sales, settings, now):
    v = estimate(gift, sales, settings, "tonnel", None, now)
    assert v.scenarios[1].profit is None and v.scenarios[1].roi is None
    assert not eligible(v, settings, now)


def test_full_cost_breakdown_roi_denominator(route, now):
    fees = {**route.fees, "buy": Fee(D(10), True), "sell": Fee(D(5), True),
            "transfer": Fee(D(2)), "withdraw": Fee(D(1)), "network": Fee(D(1)),
            "conversion": Fee(D(1)), "other": Fee(D(1))}
    v = calculate(D(100), D(200), replace(route, fees=fees), now)
    assert v.acquisition == D(115)
    assert v.proceeds == D(189)
    assert v.profit == D(74)
    assert v.roi == D(74) / D(115) * 100
    assert v.expenses["buy"] == 10 and v.expenses["sell"] == 10


def test_stale_fee_blocks_alert(gift, sales, settings, route, now):
    stale = replace(route, fees={k: replace(f, updated_at=now - timedelta(days=31)) for k, f in route.fees.items()})
    v = estimate(gift, sales, settings, "tonnel", stale, now)
    assert v.scenarios[1].profit is not None
    assert not eligible(v, settings, now)


def test_signature_manual_discount_explicit(gift, sales, settings, route, now):
    signed = replace(gift, signature=True)
    v = estimate(signed, sales, settings, "tonnel", route, now)
    assert not v.scenarios
    settings.signature_discount = D(20)
    v = estimate(signed, sales, settings, "tonnel", route, now)
    assert v.scenarios[1].resale == D("16.8")
    assert any("пользователем" in x for x in v.warnings)
    assert not eligible(v, settings, now)


def test_signed_exact_comparables_no_double_discount(gift, sales, settings, route, now):
    signed = replace(gift, signature=True)
    settings.signature_discount = D(20)
    same = [replace(s, gift=replace(s.gift, signature=True)) for s in sales]
    assert estimate(signed, same, settings, "tonnel", route, now).scenarios[1].resale == 21


def test_unknown_signature_low_confidence(gift, sales, settings, route, now):
    unknown = replace(gift, signature=None)
    same = [replace(s, gift=replace(s.gift, signature=None)) for s in sales]
    v = estimate(unknown, same, settings, "tonnel", route, now)
    assert v.confidence == "низкая" and not eligible(v, settings, now)


def test_stale_listing_not_alerted(gift, sales, settings, route, now):
    stale = replace(gift, updated_at=now - timedelta(minutes=31))
    assert not eligible(estimate(stale, sales, settings, "tonnel", route, now), settings, now)


@pytest.mark.parametrize("field,value", [("min_profit", D(100)), ("min_roi", D(999)), ("max_price", D(1)),
                                       ("collections", ["other"]), ("models", ["other"]), ("signature", "yes"), ("calculation_currency", "USD")])
def test_user_filters(field, value, gift, sales, settings, route, now):
    setattr(settings, field, value)
    assert not eligible(estimate(gift, sales, settings, "tonnel", route, now), settings, now)


def test_route_mismatch_rejected(gift, sales, settings, route, now):
    with pytest.raises(ValueError):
        estimate(gift, sales, settings, "tonnel", replace(route, currency="USD"), now)


def test_fx_source_and_freshness(now):
    rate = Rate("TON", "USD", D("5"), now, "synthetic rate")
    assert rate.convert(D(2), now) == 10
    assert rate.convert(D(2), now + timedelta(hours=2)) is None
    assert replace(rate, at=now + timedelta(hours=1)).convert(D(2), now) is None
    assert replace(rate, value=D("NaN")).convert(D(2), now) is None


def test_duration_needs_five_samples(sales):
    assert statistics(sales[:4]).median_hours is None
    assert statistics(sales).median_hours == D(2)


@pytest.mark.parametrize("changes", [{"min_sales": 0}, {"history_days": 1}, {"signature_discount": 101},
                                      {"display_currencies": ["USD", "USD"]}, {"buy_markets": ["unknown"]}, {"sell_markets": ["mrkt", "mrkt"]},
                                      {"min_roi": "NaN"}, {"sound": "yes"}])
def test_settings_validation(changes):
    with pytest.raises(ValueError):
        Settings(**changes).validate()
