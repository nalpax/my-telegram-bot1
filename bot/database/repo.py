"""
Repository layer — every database read/write the bot needs, grouped by
entity. Handlers and services call these instead of touching the ORM
directly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.models import (
    Category,
    DigitalItem,
    Favorite,
    Order,
    OrderStatus,
    PromoCode,
    Product,
    Setting,
    User,
)

# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, username: str | None, full_name: str | None
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, full_name=full_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif user.username != username or user.full_name != full_name:
        user.username = username
        user.full_name = full_name
        await session.commit()
    return user


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_all_user_telegram_ids(session: AsyncSession) -> list[int]:
    result = await session.execute(select(User.telegram_id).where(User.is_banned.is_(False)))
    return list(result.scalars().all())


async def count_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(User.id)))
    return result.scalar_one()


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #


async def list_categories(session: AsyncSession, only_active: bool = False) -> list[Category]:
    stmt = select(Category).order_by(Category.sort_order, Category.id)
    if only_active:
        stmt = stmt.where(Category.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_category(session: AsyncSession, category_id: int) -> Category | None:
    return await session.get(Category, category_id)


async def create_category(session: AsyncSession, name: str) -> Category:
    result = await session.execute(select(func.max(Category.sort_order)))
    max_order = result.scalar_one_or_none() or 0
    category = Category(name=name.strip(), sort_order=max_order + 1)
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


async def rename_category(session: AsyncSession, category_id: int, name: str) -> None:
    await session.execute(update(Category).where(Category.id == category_id).values(name=name.strip()))
    await session.commit()


async def set_category_description(session: AsyncSession, category_id: int, description: str) -> None:
    await session.execute(
        update(Category).where(Category.id == category_id).values(description=description.strip())
    )
    await session.commit()


async def toggle_category(session: AsyncSession, category_id: int) -> None:
    category = await session.get(Category, category_id)
    if category:
        category.is_active = not category.is_active
        await session.commit()


async def delete_category(session: AsyncSession, category_id: int) -> None:
    await session.execute(delete(Category).where(Category.id == category_id))
    await session.commit()


async def set_category_photo(session: AsyncSession, category_id: int, file_id: str | None) -> None:
    await session.execute(
        update(Category).where(Category.id == category_id).values(photo_file_id=file_id)
    )
    await session.commit()


async def move_category(session: AsyncSession, category_id: int, direction: int) -> None:
    """direction = -1 (up) or +1 (down)."""
    categories = await list_categories(session)
    ids = [c.id for c in categories]
    if category_id not in ids:
        return
    idx = ids.index(category_id)
    swap_idx = idx + direction
    if swap_idx < 0 or swap_idx >= len(ids):
        return
    a, b = categories[idx], categories[swap_idx]
    a.sort_order, b.sort_order = b.sort_order, a.sort_order
    await session.commit()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


async def list_products(
    session: AsyncSession, category_id: int, only_active: bool = False
) -> list[Product]:
    stmt = select(Product).where(Product.category_id == category_id).order_by(Product.sort_order, Product.id)
    if only_active:
        stmt = stmt.where(Product.is_active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def create_product(
    session: AsyncSession,
    category_id: int,
    name: str,
    description: str,
    price: Decimal,
) -> Product:
    product = Product(
        category_id=category_id,
        name=name.strip(),
        description=description.strip() if description else None,
        price=price,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def update_product_field(session: AsyncSession, product_id: int, **fields) -> None:
    if not fields:
        return
    await session.execute(update(Product).where(Product.id == product_id).values(**fields))
    await session.commit()


async def toggle_product(session: AsyncSession, product_id: int) -> None:
    product = await session.get(Product, product_id)
    if product:
        product.is_active = not product.is_active
        await session.commit()


async def delete_product(session: AsyncSession, product_id: int) -> None:
    await session.execute(delete(Product).where(Product.id == product_id))
    await session.commit()


async def set_product_photo(session: AsyncSession, product_id: int, file_id: str | None) -> None:
    await session.execute(update(Product).where(Product.id == product_id).values(photo_file_id=file_id))
    await session.commit()


async def count_products(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Product.id)))
    return result.scalar_one()


async def count_categories(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Category.id)))
    return result.scalar_one()


# --------------------------------------------------------------------------- #
# Digital items
# --------------------------------------------------------------------------- #


async def add_digital_items(session: AsyncSession, product_id: int, lines: list[str]) -> int:
    items = [DigitalItem(product_id=product_id, content=line) for line in lines if line.strip()]
    session.add_all(items)
    await session.commit()
    return len(items)


async def count_available_items(session: AsyncSession, product_id: int) -> int:
    result = await session.execute(
        select(func.count(DigitalItem.id)).where(
            DigitalItem.product_id == product_id, DigitalItem.is_sold.is_(False)
        )
    )
    return result.scalar_one()


async def count_total_items(session: AsyncSession, product_id: int) -> int:
    result = await session.execute(
        select(func.count(DigitalItem.id)).where(DigitalItem.product_id == product_id)
    )
    return result.scalar_one()


async def delete_available_items(session: AsyncSession, product_id: int) -> int:
    result = await session.execute(
        delete(DigitalItem)
        .where(DigitalItem.product_id == product_id, DigitalItem.is_sold.is_(False))
        .returning(DigitalItem.id)
    )
    deleted = len(result.fetchall())
    await session.commit()
    return deleted


_reserve_lock = asyncio.Lock()


async def reserve_and_sell_item(
    session: AsyncSession, product_id: int, order_id: int, user_id: int
) -> DigitalItem | None:
    """
    Atomically pick one unsold digital item for this product and mark it sold.

    Guarded by a process-wide asyncio.Lock rather than a DB-level
    "SELECT ... FOR UPDATE" clause, so this works identically on SQLite
    (no row locking support) and PostgreSQL. This bot runs as a single
    polling process, so a single asyncio.Lock is sufficient to guarantee
    two concurrent payments can never receive the same item.
    """
    async with _reserve_lock:
        stmt = (
            select(DigitalItem)
            .where(DigitalItem.product_id == product_id, DigitalItem.is_sold.is_(False))
            .order_by(DigitalItem.id)
            .limit(1)
        )
        result = await session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None:
            return None
        item.is_sold = True
        item.order_id = order_id
        item.sold_to_user_id = user_id
        item.sold_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(item)
        return item


# --------------------------------------------------------------------------- #
# Promo codes
# --------------------------------------------------------------------------- #


async def list_promocodes(session: AsyncSession) -> list[PromoCode]:
    result = await session.execute(select(PromoCode).order_by(PromoCode.id.desc()))
    return list(result.scalars().all())


async def get_promocode(session: AsyncSession, promocode_id: int) -> PromoCode | None:
    return await session.get(PromoCode, promocode_id)


async def get_promocode_by_code(session: AsyncSession, code: str) -> PromoCode | None:
    result = await session.execute(select(PromoCode).where(PromoCode.code == code.strip().upper()))
    return result.scalar_one_or_none()


async def create_promocode(
    session: AsyncSession,
    code: str,
    discount_percent: int,
    expires_at: datetime | None = None,
    max_uses: int | None = None,
) -> PromoCode:
    promo = PromoCode(
        code=code.strip().upper(),
        discount_percent=discount_percent,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    session.add(promo)
    await session.commit()
    await session.refresh(promo)
    return promo


async def toggle_promocode(session: AsyncSession, promocode_id: int) -> None:
    promo = await session.get(PromoCode, promocode_id)
    if promo:
        promo.is_active = not promo.is_active
        await session.commit()


async def delete_promocode(session: AsyncSession, promocode_id: int) -> None:
    await session.execute(delete(PromoCode).where(PromoCode.id == promocode_id))
    await session.commit()


async def increment_promocode_usage(session: AsyncSession, promocode_id: int) -> None:
    promo = await session.get(PromoCode, promocode_id)
    if promo:
        promo.used_count += 1
        await session.commit()


def promocode_is_valid(promo: PromoCode) -> bool:
    if not promo.is_active:
        return False
    if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
        return False
    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        return False
    return True


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #


async def create_order(
    session: AsyncSession,
    user_id: int,
    product_id: int,
    product_name: str,
    price: Decimal,
    promo_code_id: int | None = None,
    discount_amount: Decimal = Decimal("0"),
) -> Order:
    order = Order(
        user_id=user_id,
        product_id=product_id,
        product_name=product_name,
        price=price,
        promo_code_id=promo_code_id,
        discount_amount=discount_amount,
        status=OrderStatus.pending,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


async def set_order_status(session: AsyncSession, order_id: int, status: OrderStatus) -> None:
    values: dict = {"status": status}
    if status in (OrderStatus.paid, OrderStatus.completed):
        values["paid_at"] = datetime.now(timezone.utc)
    await session.execute(update(Order).where(Order.id == order_id).values(**values))
    await session.commit()


async def attach_digital_item(session: AsyncSession, order_id: int, digital_item_id: int) -> None:
    await session.execute(
        update(Order).where(Order.id == order_id).values(digital_item_id=digital_item_id)
    )
    await session.commit()


async def get_user_orders(session: AsyncSession, user_id: int, limit: int = 20) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_recent_orders(session: AsyncSession, limit: int = 10) -> list[Order]:
    result = await session.execute(
        select(Order).options(selectinload(Order.user)).order_by(Order.id.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def count_orders(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Order.id)))
    return result.scalar_one()


async def count_completed_orders(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(Order.id)).where(Order.status == OrderStatus.completed)
    )
    return result.scalar_one()


async def sum_sales(session: AsyncSession) -> Decimal:
    result = await session.execute(
        select(func.coalesce(func.sum(Order.price - Order.discount_amount), 0)).where(
            Order.status == OrderStatus.completed
        )
    )
    return result.scalar_one() or Decimal("0")


# --------------------------------------------------------------------------- #
# Settings (key/value)
# --------------------------------------------------------------------------- #


async def get_setting(session: AsyncSession, key: str) -> str | None:
    setting = await session.get(Setting, key)
    return setting.value if setting else None


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    setting = await session.get(Setting, key)
    if setting:
        setting.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.commit()


async def delete_setting(session: AsyncSession, key: str) -> None:
    await session.execute(delete(Setting).where(Setting.key == key))
    await session.commit()


# --------------------------------------------------------------------------- #
# Favorites
# --------------------------------------------------------------------------- #


async def is_favorite(session: AsyncSession, user_id: int, product_id: int) -> bool:
    result = await session.execute(
        select(Favorite.id).where(Favorite.user_id == user_id, Favorite.product_id == product_id)
    )
    return result.scalar_one_or_none() is not None


async def add_favorite(session: AsyncSession, user_id: int, product_id: int) -> None:
    if await is_favorite(session, user_id, product_id):
        return
    session.add(Favorite(user_id=user_id, product_id=product_id))
    await session.commit()


async def remove_favorite(session: AsyncSession, user_id: int, product_id: int) -> None:
    await session.execute(
        delete(Favorite).where(Favorite.user_id == user_id, Favorite.product_id == product_id)
    )
    await session.commit()


async def list_favorite_products(session: AsyncSession, user_id: int) -> list[Product]:
    result = await session.execute(
        select(Product)
        .join(Favorite, Favorite.product_id == Product.id)
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc())
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


async def search_products(session: AsyncSession, query: str, limit: int = 20) -> list[Product]:
    like = f"%{query.strip().lower()}%"
    result = await session.execute(
        select(Product)
        .where(Product.is_active.is_(True), func.lower(Product.name).like(like))
        .order_by(Product.name)
        .limit(limit)
    )
    return list(result.scalars().all())
