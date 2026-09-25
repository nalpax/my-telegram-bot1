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
from sqlalchemy.exc import IntegrityError
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
from bot.utils.formatting import LOW_STOCK_THRESHOLD, as_utc

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
        try:
            await session.commit()
        except IntegrityError:
            # Two updates from a brand-new user raced to create the same row.
            await session.rollback()
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            return result.scalar_one()
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


async def count_users(session: AsyncSession, since: datetime | None = None) -> int:
    stmt = select(func.count(User.id))
    if since is not None:
        stmt = stmt.where(User.created_at >= since)
    result = await session.execute(stmt)
    return result.scalar_one()


async def find_user(session: AsyncSession, query: str) -> User | None:
    """Look a user up by Telegram ID or @username (case-insensitive)."""
    query = query.strip()
    if query.lstrip("-").isdigit():
        return await get_user_by_telegram_id(session, int(query))
    username = query.lstrip("@").lower()
    if not username:
        return None
    result = await session.execute(select(User).where(func.lower(User.username) == username).limit(1))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def set_user_banned(session: AsyncSession, user_id: int, banned: bool) -> None:
    await session.execute(update(User).where(User.id == user_id).values(is_banned=banned))
    await session.commit()


async def count_banned_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))
    return result.scalar_one()


async def user_order_stats(session: AsyncSession, user_id: int) -> tuple[int, int, Decimal]:
    """(total orders, completed orders, total spent) for one user."""
    total = (
        await session.execute(select(func.count(Order.id)).where(Order.user_id == user_id))
    ).scalar_one()
    completed, spent = (
        await session.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.price), 0)).where(
                Order.user_id == user_id, Order.status == OrderStatus.completed
            )
        )
    ).one()
    return total, completed, Decimal(str(spent or 0))


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


async def move_category(session: AsyncSession, category_id: int, direction: int) -> bool:
    """direction = -1 (up) or +1 (down). Returns False if already at the edge."""
    categories = await list_categories(session)
    ids = [c.id for c in categories]
    if category_id not in ids:
        return False
    idx = ids.index(category_id)
    swap_idx = idx + direction
    if swap_idx < 0 or swap_idx >= len(ids):
        return False
    categories[idx], categories[swap_idx] = categories[swap_idx], categories[idx]
    # Renumber everything: swapping two equal sort_order values would be a no-op.
    for position, category in enumerate(categories, start=1):
        category.sort_order = position
    await session.commit()
    return True


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


async def list_all_products(session: AsyncSession) -> list[Product]:
    result = await session.execute(
        select(Product)
        .join(Category, Category.id == Product.category_id)
        .order_by(Category.sort_order, Category.id, Product.sort_order, Product.id)
    )
    return list(result.scalars().all())


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def is_product_visible(session: AsyncSession, product: Product | None) -> bool:
    """A product is visible to buyers only if both it and its category are enabled."""
    if product is None or not product.is_active:
        return False
    category = await session.get(Category, product.category_id)
    return category is not None and category.is_active


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
    items = [DigitalItem(product_id=product_id, content=line.strip()) for line in lines if line.strip()]
    if not items:
        return 0
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
        delete(DigitalItem).where(DigitalItem.product_id == product_id, DigitalItem.is_sold.is_(False))
    )
    await session.commit()
    return result.rowcount or 0


async def list_low_stock_products(session: AsyncSession) -> list[tuple[Product, int]]:
    """Active products with LOW_STOCK_THRESHOLD or fewer unsold items, emptiest first."""
    available = (
        select(DigitalItem.product_id, func.count(DigitalItem.id).label("cnt"))
        .where(DigitalItem.is_sold.is_(False))
        .group_by(DigitalItem.product_id)
        .subquery()
    )
    cnt = func.coalesce(available.c.cnt, 0)
    result = await session.execute(
        select(Product, cnt)
        .outerjoin(available, available.c.product_id == Product.id)
        .where(Product.is_active.is_(True), cnt <= LOW_STOCK_THRESHOLD)
        .order_by(cnt, Product.name)
    )
    return [(row[0], int(row[1])) for row in result.all()]


_reserve_lock = asyncio.Lock()


async def reserve_and_sell_item(
    session: AsyncSession, product_id: int, order_id: int, user_id: int
) -> DigitalItem | None:
    """
    Atomically pick one unsold digital item for this product and mark it sold.

    Guarded by a process-wide asyncio.Lock, which works identically on
    SQLite (no row locking support) and PostgreSQL. On PostgreSQL the row
    is additionally locked with "SELECT ... FOR UPDATE SKIP LOCKED", so even
    several bot processes sharing one database can never hand out the same
    item twice.
    """
    async with _reserve_lock:
        stmt = (
            select(DigitalItem)
            .where(DigitalItem.product_id == product_id, DigitalItem.is_sold.is_(False))
            .order_by(DigitalItem.id)
            .limit(1)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
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
    expires_at = as_utc(promo.expires_at)
    if expires_at and expires_at < datetime.now(timezone.utc):
        return False
    if promo.max_uses is not None and promo.used_count >= promo.max_uses:
        return False
    return True


async def user_used_promocode(session: AsyncSession, user_id: int, promocode_id: int) -> bool:
    """Each promo code works once per buyer."""
    result = await session.execute(
        select(Order.id)
        .where(
            Order.user_id == user_id,
            Order.promo_code_id == promocode_id,
            Order.status.in_([OrderStatus.paid, OrderStatus.completed]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


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
    if status == OrderStatus.paid:
        values["paid_at"] = datetime.now(timezone.utc)
    await session.execute(update(Order).where(Order.id == order_id).values(**values))
    await session.commit()


async def mark_order_paid(session: AsyncSession, order_id: int, charge_id: str | None) -> None:
    await session.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(status=OrderStatus.paid, paid_at=datetime.now(timezone.utc), charge_id=charge_id)
    )
    await session.commit()


async def complete_order(session: AsyncSession, order_id: int, item: DigitalItem) -> None:
    await session.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(status=OrderStatus.completed, digital_item_id=item.id, delivered_content=item.content)
    )
    await session.commit()


async def cancel_pending_orders(session: AsyncSession, user_id: int, product_id: int) -> None:
    """Invalidate older unpaid invoices for the same product so they can't be paid anymore."""
    await session.execute(
        update(Order)
        .where(
            Order.user_id == user_id,
            Order.product_id == product_id,
            Order.status == OrderStatus.pending,
        )
        .values(status=OrderStatus.cancelled)
    )
    await session.commit()


async def get_user_orders(
    session: AsyncSession, user_id: int, limit: int = 20, include_unpaid: bool = False
) -> list[Order]:
    stmt = select(Order).where(Order.user_id == user_id)
    if not include_unpaid:
        # Abandoned/replaced invoices are noise for the buyer.
        stmt = stmt.where(Order.status.notin_([OrderStatus.pending, OrderStatus.cancelled]))
    result = await session.execute(stmt.order_by(Order.id.desc()).limit(limit))
    return list(result.scalars().all())


async def get_recent_orders(
    session: AsyncSession, limit: int = 10, offset: int = 0, only_paid: bool = True
) -> list[Order]:
    stmt = select(Order).options(selectinload(Order.user))
    if only_paid:
        stmt = stmt.where(Order.status.notin_([OrderStatus.pending, OrderStatus.cancelled]))
    result = await session.execute(stmt.order_by(Order.id.desc()).offset(offset).limit(limit))
    return list(result.scalars().all())


async def get_order_with_user(session: AsyncSession, order_id: int) -> Order | None:
    result = await session.execute(
        select(Order).options(selectinload(Order.user)).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def count_orders(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Order.id)))
    return result.scalar_one()


async def count_completed_orders(session: AsyncSession, since: datetime | None = None) -> int:
    stmt = select(func.count(Order.id)).where(Order.status == OrderStatus.completed)
    if since is not None:
        stmt = stmt.where(Order.paid_at >= since)
    result = await session.execute(stmt)
    return result.scalar_one()


async def count_failed_orders(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Order.id)).where(Order.status == OrderStatus.failed))
    return result.scalar_one()


async def sum_sales(session: AsyncSession, since: datetime | None = None) -> Decimal:
    # Order.price is already the final (discounted) amount the buyer paid.
    stmt = select(func.coalesce(func.sum(Order.price), 0)).where(Order.status == OrderStatus.completed)
    if since is not None:
        stmt = stmt.where(Order.paid_at >= since)
    result = await session.execute(stmt)
    return Decimal(str(result.scalar_one() or 0))


async def top_products(session: AsyncSession, limit: int = 5) -> list[tuple[str, int]]:
    result = await session.execute(
        select(Order.product_name, func.count(Order.id).label("cnt"))
        .where(Order.status == OrderStatus.completed)
        .group_by(Order.product_name)
        .order_by(func.count(Order.id).desc())
        .limit(limit)
    )
    return [(row[0], int(row[1])) for row in result.all()]


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
        .join(Category, Category.id == Product.category_id)
        .where(Favorite.user_id == user_id, Product.is_active.is_(True), Category.is_active.is_(True))
        .order_by(Favorite.created_at.desc(), Favorite.id.desc())
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


async def search_products(session: AsyncSession, query: str, limit: int = 20) -> list[Product]:
    """
    Case-insensitive substring search over names and descriptions of visible products.

    Matching is done in Python on purpose: SQLite's lower()/LIKE only fold
    ASCII, so a Cyrillic query like "ключ" would never match "Ключ" there.
    A shop catalog is small enough for this to be instant.
    """
    needle = query.strip().casefold()
    if not needle:
        return []
    result = await session.execute(
        select(Product)
        .join(Category, Category.id == Product.category_id)
        .where(Product.is_active.is_(True), Category.is_active.is_(True))
        .order_by(Product.name)
    )
    by_name: list[Product] = []
    by_description: list[Product] = []
    for product in result.scalars().all():
        if needle in product.name.casefold():
            by_name.append(product)
        elif product.description and needle in product.description.casefold():
            by_description.append(product)
    return (by_name + by_description)[:limit]
