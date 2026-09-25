from datetime import datetime, timedelta
from decimal import Decimal

from bot.database import repo
from bot.database.models import OrderStatus


async def _product(session, name="Ключ Windows", price="10", items=("A", "B")):
    cat = await repo.create_category(session, "Софт")
    product = await repo.create_product(session, cat.id, name, "desc", Decimal(price))
    await repo.add_digital_items(session, product.id, list(items))
    return cat, product


async def test_reserve_never_sells_same_item_twice(session):
    _, product = await _product(session)
    user = await repo.get_or_create_user(session, 5, "u", "U")
    o1 = await repo.create_order(session, user.id, product.id, product.name, product.price)
    o2 = await repo.create_order(session, user.id, product.id, product.name, product.price)
    o3 = await repo.create_order(session, user.id, product.id, product.name, product.price)
    i1 = await repo.reserve_and_sell_item(session, product.id, o1.id, user.id)
    i2 = await repo.reserve_and_sell_item(session, product.id, o2.id, user.id)
    i3 = await repo.reserve_and_sell_item(session, product.id, o3.id, user.id)
    assert {i1.content, i2.content} == {"A", "B"}
    assert i3 is None


async def test_search_is_case_insensitive_for_cyrillic(session):
    await _product(session)
    assert [p.name for p in await repo.search_products(session, "КЛЮЧ")] == ["Ключ Windows"]
    assert await repo.search_products(session, "нет такого") == []


async def test_search_hides_disabled_categories(session):
    cat, _ = await _product(session)
    await repo.toggle_category(session, cat.id)
    assert await repo.search_products(session, "ключ") == []


async def test_promocode_expiry_works_with_naive_sqlite_datetimes(session):
    promo = await repo.create_promocode(session, "old", 10, expires_at=datetime.utcnow() - timedelta(days=1))
    await session.refresh(promo)
    assert repo.promocode_is_valid(promo) is False


async def test_move_category_with_equal_sort_orders(session):
    a = await repo.create_category(session, "A")
    b = await repo.create_category(session, "B")
    a.sort_order = b.sort_order = 0
    await session.commit()
    assert await repo.move_category(session, b.id, -1)
    assert [c.name for c in await repo.list_categories(session)] == ["B", "A"]


async def test_delivered_content_survives_product_deletion(session):
    _, product = await _product(session)
    user = await repo.get_or_create_user(session, 5, "u", "U")
    order = await repo.create_order(session, user.id, product.id, product.name, product.price)
    item = await repo.reserve_and_sell_item(session, product.id, order.id, user.id)
    await repo.complete_order(session, order.id, item)
    await repo.delete_product(session, product.id)
    order = await repo.get_order(session, order.id)
    await session.refresh(order)
    assert order.status == OrderStatus.completed
    assert order.delivered_content == "A"


async def test_user_orders_hide_unpaid(session):
    _, product = await _product(session)
    user = await repo.get_or_create_user(session, 5, "u", "U")
    await repo.create_order(session, user.id, product.id, product.name, product.price)
    assert await repo.get_user_orders(session, user.id) == []
    assert len(await repo.get_user_orders(session, user.id, include_unpaid=True)) == 1
