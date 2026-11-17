"""Schema migration tests for databases created before owner_user_id existed."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

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
