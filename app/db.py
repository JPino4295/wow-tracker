"""SQLite connection handling and idempotent schema creation."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

from .config import backups_dir, db_path, images_dir, user_names

_ITEMS_COLUMNS = """
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    wowhead_id      INTEGER UNIQUE,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('mount','transmog','toy','pet','other')),
    icon_local      TEXT NOT NULL DEFAULT '',
    periodicity     TEXT NOT NULL CHECK (periodicity IN ('daily','weekly','once')),
    owner_user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    url_wowhead     TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    last_scraped_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
"""

_SOURCES_COLUMNS = """
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('raid','rare','boss','secret','farming','dungeon')),
    zone        TEXT NOT NULL DEFAULT '',
    wowhead_url TEXT NOT NULL DEFAULT '',
    UNIQUE (name, kind)
"""

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items ({_ITEMS_COLUMNS});

CREATE TABLE IF NOT EXISTS sources ({_SOURCES_COLUMNS});

CREATE TABLE IF NOT EXISTS item_sources (
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source_id  INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    drop_rate  REAL CHECK (drop_rate IS NULL OR (drop_rate >= 0 AND drop_rate <= 100)),
    difficulty TEXT NOT NULL DEFAULT '',
    conditions TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, source_id, difficulty)
);

CREATE TABLE IF NOT EXISTS progress (
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_done_at TEXT NOT NULL,
    PRIMARY KEY (item_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
CREATE INDEX IF NOT EXISTS idx_items_periodicity ON items(periodicity);
CREATE INDEX IF NOT EXISTS idx_item_sources_source ON item_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_progress_user ON progress(user_id);
"""


def connect() -> sqlite3.Connection:
    """Open a connection with foreign keys enabled and row access by name."""
    connection = sqlite3.connect(db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_connection() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request."""
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def _rebuild_checks(connection: sqlite3.Connection) -> None:
    """Widen legacy CHECK clauses; SQLite cannot change one with ALTER TABLE."""
    plans = (
        (
            "items",
            "once",
            _ITEMS_COLUMNS,
            (
                "id", "wowhead_id", "name", "type", "icon_local", "periodicity",
                "owner_user_id", "url_wowhead", "description", "notes",
                "last_scraped_at", "created_at", "updated_at",
            ),
            (
                "CREATE INDEX idx_items_type ON items(type)",
                "CREATE INDEX idx_items_periodicity ON items(periodicity)",
            ),
        ),
        (
            "sources",
            "dungeon",
            _SOURCES_COLUMNS,
            ("id", "name", "kind", "zone", "wowhead_url"),
            (),
        ),
    )
    for table, value, columns_sql, columns, indexes in plans:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if row is None or f"'{value}'" in row["sql"]:
            continue

        # Cascades must stay off while the parent table is dropped, or child rows die.
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            names = ", ".join(columns)
            connection.execute(f"CREATE TABLE {table}_rebuilt ({columns_sql})")
            connection.execute(
                f"INSERT INTO {table}_rebuilt ({names}) SELECT {names} FROM {table}"
            )
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {table}_rebuilt RENAME TO {table}")
            for index in indexes:
                connection.execute(index)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")


def init_db(now: str) -> None:
    """Create tables and the two default users. Safe to call on every start."""
    db_path().parent.mkdir(parents=True, exist_ok=True)
    images_dir().mkdir(parents=True, exist_ok=True)
    backups_dir().mkdir(parents=True, exist_ok=True)

    connection = connect()
    try:
        connection.executescript(SCHEMA)

        # Idempotent migration for databases created before owner_user_id existed.
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
        if "owner_user_id" not in existing:
            connection.execute(
                "ALTER TABLE items ADD COLUMN owner_user_id "
                "INTEGER REFERENCES users(id) ON DELETE SET NULL"
            )

        _rebuild_checks(connection)

        # Seed defaults only for a brand-new database. Otherwise deleting a player
        # would be undone on the next container restart.
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
            for name in user_names():
                connection.execute(
                    "INSERT OR IGNORE INTO users (name, created_at) VALUES (?, ?)",
                    (name, now),
                )
        connection.commit()
    finally:
        connection.close()


def rows_to_dicts(rows: Any) -> list[dict[str, Any]]:
    """Small helper for read-only listings."""
    return [dict(row) for row in rows]
