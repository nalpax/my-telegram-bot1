"""Append-only SQLite schema migrations, applied transactionally."""

MIGRATIONS = [
    """
    CREATE TABLE users (user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL, last_attempt TEXT);
    CREATE TABLE markets (market TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT NOT NULL, checked_at TEXT);
    CREATE TABLE listings (key TEXT PRIMARY KEY, market TEXT NOT NULL, listing_id TEXT NOT NULL,
                          payload TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(market, listing_id));
    CREATE TABLE sales (market TEXT NOT NULL, sale_id TEXT NOT NULL, payload TEXT NOT NULL,
                        sold_at TEXT NOT NULL, PRIMARY KEY(market, sale_id));
    CREATE TABLE price_history (key TEXT NOT NULL, price TEXT NOT NULL, currency TEXT NOT NULL,
                               observed_at TEXT NOT NULL, PRIMARY KEY(key, observed_at));
    CREATE TABLE routes (user_id INTEGER NOT NULL REFERENCES users(user_id), buy_market TEXT NOT NULL,
                         sell_market TEXT NOT NULL, currency TEXT NOT NULL, payload TEXT NOT NULL,
                         PRIMARY KEY(user_id, buy_market, sell_market, currency));
    CREATE TABLE rates (base TEXT NOT NULL, quote TEXT NOT NULL, value TEXT NOT NULL, at TEXT NOT NULL,
                        source TEXT NOT NULL, PRIMARY KEY(base, quote));
    CREATE TABLE notifications (user_id INTEGER NOT NULL REFERENCES users(user_id), key TEXT NOT NULL,
                               state TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(user_id, key));
    CREATE TABLE favorites (user_id INTEGER NOT NULL REFERENCES users(user_id), key TEXT NOT NULL,
                           PRIMARY KEY(user_id, key));
    CREATE TABLE hidden (user_id INTEGER NOT NULL REFERENCES users(user_id), key TEXT NOT NULL,
                        PRIMARY KEY(user_id, key));
    CREATE TABLE sync_errors (id INTEGER PRIMARY KEY, market TEXT NOT NULL, category TEXT NOT NULL, at TEXT NOT NULL);
    CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE INDEX sales_time ON sales(sold_at);
    CREATE INDEX listings_time ON listings(updated_at);
    """,
    """
    ALTER TABLE rates RENAME TO rates_legacy;
    CREATE TABLE rates (user_id INTEGER NOT NULL DEFAULT 0, base TEXT NOT NULL, quote TEXT NOT NULL,
                        value TEXT NOT NULL, at TEXT NOT NULL, source TEXT NOT NULL,
                        PRIMARY KEY(user_id, base, quote));
    INSERT INTO rates SELECT 0, base, quote, value, at, source FROM rates_legacy;
    DROP TABLE rates_legacy;
    """,
    """
    CREATE TABLE market_connections (market TEXT PRIMARY KEY, key_env TEXT, docs_url TEXT,
                                     updated_at TEXT NOT NULL);
    """,
    """
    CREATE TABLE market_credentials (market TEXT PRIMARY KEY, ciphertext TEXT NOT NULL,
                                    updated_at TEXT NOT NULL);
    """,
]
