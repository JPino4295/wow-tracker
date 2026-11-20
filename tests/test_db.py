"""Schema migration tests for databases created before owner_user_id existed."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.db import init_db

UTC_NOW = "2026-11-17T08:00:00+00:00"


def test_owner_column_is_added_idempotently(tmp_path: Any, monkeypatch: Any) -> None:
    db_file = Path(tmp_path) / "legacy.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("DATA_DIR", str(Path(tmp_path) / "data"))

    connection = sqlite3.connect(db_file)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wowhead_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            icon_local TEXT NOT NULL DEFAULT '',
            periodicity TEXT NOT NULL,
            url_wowhead TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            last_scraped_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO items (id, name, type, periodicity, created_at, updated_at)
        VALUES (1, 'Montura antigua', 'mount', 'weekly', '2026-01-01', '2026-01-01');
        """
    )
    connection.commit()
    connection.close()

    init_db(UTC_NOW)
    init_db(UTC_NOW)  # Safe to call again on every start.

    connection = sqlite3.connect(db_file)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(items)")}
    assert "owner_user_id" in columns
    row = connection.execute(
        "SELECT name, owner_user_id FROM items WHERE id = 1"
    ).fetchone()
    assert row == ("Montura antigua", None)
    connection.close()


def test_populated_legacy_schema_migrates_to_new_checks_idempotently(
    tmp_path: Any, monkeypatch: Any
) -> None:
    db_file = Path(tmp_path) / "legacy.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("DATA_DIR", str(Path(tmp_path) / "data"))

    connection = sqlite3.connect(db_file)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        );
        CREATE TABLE items (
            id INTEGER PRIMARY KEY, wowhead_id INTEGER UNIQUE, name TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('mount','transmog','toy','pet','other')),
            icon_local TEXT NOT NULL DEFAULT '',
            periodicity TEXT NOT NULL CHECK (periodicity IN ('daily','weekly')),
            owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            url_wowhead TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '', last_scraped_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('raid','rare','boss')),
            zone TEXT NOT NULL DEFAULT '', wowhead_url TEXT NOT NULL DEFAULT '',
            UNIQUE (name, kind)
        );
        CREATE TABLE item_sources (
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            drop_rate REAL CHECK (drop_rate IS NULL OR (drop_rate >= 0 AND drop_rate <= 100)),
            difficulty TEXT NOT NULL DEFAULT '', conditions TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (item_id, source_id, difficulty)
        );
        CREATE TABLE progress (
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            last_done_at TEXT NOT NULL, PRIMARY KEY (item_id, user_id)
        );
        CREATE INDEX idx_items_type ON items(type);
        CREATE INDEX idx_items_periodicity ON items(periodicity);
        INSERT INTO users VALUES (1, 'Ana', '2026-01-01');
        INSERT INTO users VALUES (2, 'Beto', '2026-01-01');
        INSERT INTO sources VALUES (1, 'Sagrario', 'raid', 'Sagrario', 'https://example.com');
        INSERT INTO items (id, name, type, periodicity, created_at, updated_at)
          VALUES (1, 'Montura', 'mount', 'weekly', '2026-01-01', '2026-01-01');
        INSERT INTO items (id, name, type, periodicity, created_at, updated_at)
          VALUES (2, 'Juguete', 'toy', 'daily', '2026-01-01', '2026-01-01');
        INSERT INTO item_sources (item_id, source_id, drop_rate, difficulty, conditions)
          VALUES (1, 1, 12.5, 'Mítico', 'Rare');
        INSERT INTO progress VALUES (1, 1, '2026-11-17T07:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    init_db(UTC_NOW)
    init_db(UTC_NOW)  # Safe to call again on every start.

    connection = sqlite3.connect(db_file)
    assert [row[0] for row in connection.execute("SELECT id FROM users ORDER BY id")] == [1, 2]
    assert connection.execute(
        "SELECT name, periodicity, owner_user_id FROM items WHERE id = 1"
    ).fetchone() == ("Montura", "weekly", None)
    assert connection.execute(
        "SELECT name, periodicity FROM items WHERE id = 2"
    ).fetchone() == ("Juguete", "daily")
    assert connection.execute(
        "SELECT item_id, source_id, drop_rate, difficulty, conditions FROM item_sources"
    ).fetchone() == (1, 1, 12.5, "Mítico", "Rare")
    assert connection.execute(
        "SELECT item_id, user_id, last_done_at FROM progress"
    ).fetchone() == (1, 1, "2026-11-17T07:00:00+00:00")
    assert connection.execute(
        "SELECT name, kind FROM sources"
    ).fetchone() == ("Sagrario", "raid")

    indexes = {row[1] for row in connection.execute("PRAGMA index_list(items)")}
    assert {"idx_items_type", "idx_items_periodicity"} <= indexes

    connection.execute("INSERT INTO sources (name, kind) VALUES ('Cofre', 'secret')")
    connection.execute(
        "INSERT INTO items (name, type, periodicity, created_at, updated_at) "
        "VALUES ('Reliquia', 'other', 'once', ?, ?)",
        (UTC_NOW, UTC_NOW),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO sources (name, kind) VALUES ('Mal', 'wrong')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO items (name, type, periodicity, created_at, updated_at) "
            "VALUES ('Mal', 'other', 'hourly', ?, ?)",
            (UTC_NOW, UTC_NOW),
        )
    connection.close()
