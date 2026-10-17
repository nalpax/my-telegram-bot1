import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from .analytics import Rate
from .connections import Connection
from .credentials import SOURCES, CredentialVault, credential_value
from .migrations import MIGRATIONS
from .models import MARKETS, Gift, Route, Settings, dumps, gift_from, instant, route_from, sale_from, utcnow
from .security import private_file, register_secret
from .adapters import redact_raw


def listing_key(gift: Gift) -> str:
    return hashlib.sha256(f"{gift.market}:{gift.listing_id}".encode()).hexdigest()[:24]


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_file(path)
            for suffix in ("-wal", "-shm", "-journal"):
                companion = Path(str(path) + suffix)
                if companion.exists():
                    private_file(companion)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise RuntimeError("Database schema newer than application")
        for i in range(version, len(MIGRATIONS)):
            self.db.executescript(f"BEGIN IMMEDIATE;\n{MIGRATIONS[i]}\nPRAGMA user_version={i+1};\nCOMMIT;")
        self.vault = CredentialVault(path)
        try:
            for market, ciphertext in self.db.execute("SELECT market,ciphertext FROM market_credentials"):
                register_secret(self.vault.decrypt(market, ciphertext))
        except ValueError:
            self.db.close()
            raise
        with self.db:
            for market in MARKETS:
                self.db.execute("INSERT OR IGNORE INTO markets(market,state,detail,checked_at) VALUES (?,?,?,?)",
                                (market, "disabled", "Данные ещё не загружались", None))

    def close(self):
        self.db.close()

    def settings(self, user: int) -> Settings:
        row = self.db.execute("SELECT settings FROM users WHERE user_id=?", (user,)).fetchone()
        if row:
            return Settings(**json.loads(row[0])).validate()
        result = Settings().validate()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO users(user_id,settings) VALUES (?,?)", (user, dumps(result)))
        return result

    def save_settings(self, user: int, settings: Settings):
        settings.validate()
        with self.db:
            self.db.execute("INSERT INTO users(user_id,settings) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET settings=excluded.settings", (user, dumps(settings)))

    def users(self):
        return [r[0] for r in self.db.execute("SELECT user_id FROM users")]

    def ingest(self, listings, sales):
        with self.db:
            for gift in listings:
                gift = replace(gift, raw=redact_raw(gift.raw))
                key = listing_key(gift)
                old = self.db.execute("SELECT payload FROM listings WHERE key=?", (key,)).fetchone()
                previous = gift_from(json.loads(old[0])) if old else None
                if previous and previous.updated_at >= gift.updated_at:
                    continue
                self.db.execute("INSERT INTO listings VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                                (key, gift.market, gift.listing_id, dumps(gift), gift.updated_at.isoformat()))
                if not previous or previous.price != gift.price or previous.currency != gift.currency:
                    self.db.execute("INSERT OR IGNORE INTO price_history VALUES (?,?,?,?)", (key, str(gift.price), gift.currency, gift.updated_at.isoformat()))
            for sale in sales:
                sale = replace(sale, gift=replace(sale.gift, raw=redact_raw(sale.gift.raw)))
                self.db.execute("INSERT INTO sales VALUES (?,?,?,?) ON CONFLICT(market,sale_id) DO UPDATE SET payload=excluded.payload,sold_at=excluded.sold_at",
                                (sale.gift.market, sale.sale_id, dumps(sale), sale.sold_at.isoformat()))
                key = listing_key(sale.gift)
                row = self.db.execute("SELECT payload FROM listings WHERE key=?", (key,)).fetchone()
                if row:
                    old = gift_from(json.loads(row[0]))
                    if old.updated_at <= sale.sold_at:
                        sold = replace(old, status="sold", updated_at=sale.sold_at)
                        self.db.execute("UPDATE listings SET payload=?,updated_at=? WHERE key=?", (dumps(sold), sold.updated_at.isoformat(), key))
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_sync',?)", (utcnow().isoformat(),))

    def listings(self, *, active_only=True):
        values = [gift_from(json.loads(r[0])) for r in self.db.execute("SELECT payload FROM listings ORDER BY updated_at DESC")]
        return [x for x in values if x.status == "active"] if active_only else values

    def listing(self, key):
        r = self.db.execute("SELECT payload FROM listings WHERE key=?", (key,)).fetchone()
        return gift_from(json.loads(r[0])) if r else None

    def sales(self, now, days=30):
        rows = self.db.execute("SELECT payload FROM sales WHERE sold_at>=?", ((now - timedelta(days=days)).isoformat(),))
        return [sale_from(json.loads(r[0])) for r in rows]

    def save_route(self, user: int, route: Route):
        self.settings(user)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?)", (user, route.buy_market, route.sell_market, route.currency, dumps(route)))

    def route(self, user: int, buy: str, sell: str, currency: str):
        row = self.db.execute("SELECT payload FROM routes WHERE user_id=? AND buy_market=? AND sell_market=? AND currency=?", (user, buy, sell, currency)).fetchone()
        return route_from(json.loads(row[0])) if row else Route(buy, sell, currency)

    def save_rate(self, rate: Rate, user=0):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO rates VALUES (?,?,?,?,?,?)", (user, rate.base, rate.quote, str(rate.value), rate.at.isoformat(), rate.source))

    def rate(self, base, quote, user=0):
        row = self.db.execute("SELECT value,at,source FROM rates WHERE user_id=? AND base=? AND quote=?", (user, base, quote)).fetchone()
        if row is None and user:
            row = self.db.execute("SELECT value,at,source FROM rates WHERE user_id=0 AND base=? AND quote=?", (base, quote)).fetchone()
        return Rate(base, quote, Decimal(row[0]), instant(row[1]), row[2]) if row else None

    def mark(self, table: str, user: int, key: str, remove=False):
        if table not in ("favorites", "hidden"):
            raise ValueError("Invalid marker")
        if not self.listing(key):
            raise ValueError("Лот больше не доступен")
        self.settings(user)
        with self.db:
            if remove:
                self.db.execute(f"DELETE FROM {table} WHERE user_id=? AND key=?", (user, key))
            else:
                self.db.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?)", (user, key))

    def marked(self, table: str, user: int):
        if table not in ("favorites", "hidden"):
            raise ValueError("Invalid marker")
        return {r[0] for r in self.db.execute(f"SELECT key FROM {table} WHERE user_id=?", (user,))}

    def claim_alert(self, user: int, key: str, now, price=None, repeat_on_drop=False) -> bool:
        # Commit BEFORE sending. Ambiguous transport failures stay claimed: no duplicate on restart.
        self.settings(user)
        price = None if price is None else str(price)
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,'sending',?,?)", (user, key, now.isoformat(), price))
            if cur.rowcount == 1:
                return True
            if not repeat_on_drop or price is None:
                return False
            # Optional repeat only after a delivered alert and a strictly lower price.
            row = self.db.execute("SELECT state,price FROM notifications WHERE user_id=? AND key=?", (user, key)).fetchone()
            if not row or row[0] != "sent" or row[1] is None or Decimal(price) >= Decimal(row[1]):
                return False
            cur = self.db.execute("UPDATE notifications SET state='sending',at=?,price=? WHERE user_id=? AND key=? AND state='sent' AND price=?",
                                  (now.isoformat(), price, user, key, row[1]))
            return cur.rowcount == 1

    def finish_alert(self, user, key, state):
        with self.db:
            self.db.execute("UPDATE notifications SET state=? WHERE user_id=? AND key=?", (state, user, key))

    def due(self, user, now, minutes):
        r = self.db.execute("SELECT last_attempt FROM users WHERE user_id=?", (user,)).fetchone()
        return not r or not r[0] or now - instant(r[0]) >= timedelta(minutes=minutes)

    def attempted(self, user, now):
        with self.db:
            self.db.execute("UPDATE users SET last_attempt=? WHERE user_id=?", (now.isoformat(), user))

    def market_status(self):
        return list(self.db.execute("SELECT market,state,detail,checked_at FROM markets"))

    def connection(self, market):
        row = self.db.execute("SELECT key_env,docs_url FROM market_connections WHERE market=?", (market,)).fetchone()
        saved = self.db.execute("SELECT 1 FROM market_credentials WHERE market=?", (market,)).fetchone() is not None
        return Connection(market, *(row or (None, None)), has_saved_key=saved)

    def credential(self, market):
        if market not in SOURCES:
            raise ValueError("Invalid market")
        connection = self.connection(market) if market in MARKETS else None
        row = self.db.execute("SELECT ciphertext FROM market_credentials WHERE market=?", (market,)).fetchone()
        value = self.vault.decrypt(market, row[0]) if row else os.environ.get(connection.key_env) if connection and connection.key_env else None
        register_secret(value)
        return value

    def has_credential(self, market):
        return self.db.execute("SELECT 1 FROM market_credentials WHERE market=?", (market,)).fetchone() is not None

    def delete_credential(self, market):
        with self.db:
            self.db.execute("DELETE FROM market_credentials WHERE market=?", (market,))

    def save_credential(self, market, value):
        if market not in SOURCES:
            raise ValueError("Invalid market")
        connection = self.connection(market) if market in MARKETS else None
        value = credential_value(value)
        # Also mask a submitted value if disk protection fails before it is saved.
        register_secret(value)
        exists = self.db.execute("SELECT 1 FROM market_credentials LIMIT 1").fetchone() is not None
        for saved_market, saved_ciphertext in self.db.execute("SELECT market,ciphertext FROM market_credentials"):
            self.vault.decrypt(saved_market, saved_ciphertext)
        ciphertext = self.vault.encrypt(market, value, allow_create=not exists)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO market_credentials VALUES (?,?,?)",
                            (market, ciphertext, utcnow().isoformat()))
            if connection:
                self.db.execute("INSERT OR REPLACE INTO market_connections VALUES (?,?,?,?)",
                                (market, None, connection.docs_url, utcnow().isoformat()))

    def save_connection(self, connection):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO market_connections VALUES (?,?,?,?)",
                            (connection.market, connection.key_env, connection.docs_url, utcnow().isoformat()))
            if connection.key_env:
                self.db.execute("DELETE FROM market_credentials WHERE market=?", (connection.market,))

    def disconnect(self, market):
        with self.db:
            self.db.execute("DELETE FROM market_connections WHERE market=?", (market,))
            self.db.execute("DELETE FROM market_credentials WHERE market=?", (market,))

    def set_market(self, market, state, detail, data_ok=False):
        # data_at changes only after real data was actually received.
        now = utcnow().isoformat()
        with self.db:
            self.db.execute("UPDATE markets SET state=?,detail=?,checked_at=?,data_at=CASE WHEN ? THEN ? ELSE data_at END WHERE market=?",
                            (state, detail, now, data_ok, now, market))

    def market_data(self, market):
        row = self.db.execute("SELECT state,detail,checked_at,data_at FROM markets WHERE market=?", (market,)).fetchone()
        return row or ("disabled", "", None, None)

    def market_counts(self, market, now=None):
        now = now or utcnow()
        listings = sum(1 for g in self.listings() if g.market == market)
        sales = self.db.execute("SELECT count(*) FROM sales WHERE market=? AND sold_at>=?",
                                (market, (now - timedelta(days=30)).isoformat())).fetchone()[0]
        return listings, sales

    def has_market_data(self, market):
        return (self.db.execute("SELECT 1 FROM listings WHERE market=? LIMIT 1", (market,)).fetchone() is not None or
                self.db.execute("SELECT 1 FROM sales WHERE market=? LIMIT 1", (market,)).fetchone() is not None)

    def market_flag(self, market):
        """None means never chosen: the marketplace takes part once it is available."""
        row = self.db.execute("SELECT enabled FROM market_settings WHERE market=?", (market,)).fetchone()
        return None if row is None else bool(row[0])

    def set_market_flag(self, market, enabled):
        if market not in MARKETS:
            raise ValueError("Invalid market")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO market_settings VALUES (?,?)", (market, int(bool(enabled))))

    def source_state(self, name):
        row = self.db.execute("SELECT state,detail,checked_at,verified_at FROM sources WHERE name=?", (name,)).fetchone()
        return row or ("missing", "", None, None)

    def set_source_state(self, name, state, detail, verified=False):
        now = utcnow().isoformat()
        with self.db:
            self.db.execute("INSERT INTO sources VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET state=excluded.state,"
                            "detail=excluded.detail,checked_at=excluded.checked_at,"
                            "verified_at=CASE WHEN ? THEN excluded.checked_at ELSE CASE WHEN excluded.state='missing' THEN NULL ELSE sources.verified_at END END",
                            (name, state, detail, now, now if verified else None, verified))

    def claim_error_notice(self, user, subject, state):
        """Return the previously notified state when it changed, else False."""
        row = self.db.execute("SELECT state FROM error_notices WHERE user_id=? AND subject=?", (user, subject)).fetchone()
        previous = row[0] if row else "ok"
        if previous == state:
            return False
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO error_notices VALUES (?,?,?,?)", (user, subject, state, utcnow().isoformat()))
        return previous

    def error(self, market, category):
        # Store categories, never response bodies, auth URLs or exception strings.
        with self.db:
            self.db.execute("INSERT INTO sync_errors(market,category,at) VALUES (?,?,?)", (market, category, utcnow().isoformat()))

    def meta(self, name, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
        return row[0] if row else default

    def set_meta(self, name, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (name, str(value)))

    def last_sync(self):
        r = self.db.execute("SELECT value FROM metadata WHERE name='last_sync'").fetchone()
        return r[0] if r else "данные ещё не получены"

    def cleanup(self, now, days=90):
        cutoff = (now - timedelta(days=max(30, days))).isoformat()
        with self.db:
            for table, column in (("price_history", "observed_at"), ("sales", "sold_at"), ("sync_errors", "at")):
                self.db.execute(f"DELETE FROM {table} WHERE {column}<?", (cutoff,))
            self.db.execute("DELETE FROM listings WHERE updated_at<? AND key NOT IN (SELECT key FROM favorites)", (cutoff,))

    def delete_user(self, user):
        with self.db:
            for table in ("routes", "notifications", "favorites", "hidden", "error_notices"):
                self.db.execute(f"DELETE FROM {table} WHERE user_id=?", (user,))
            self.db.execute("DELETE FROM rates WHERE user_id=?", (user,))
            self.db.execute("DELETE FROM users WHERE user_id=?", (user,))
