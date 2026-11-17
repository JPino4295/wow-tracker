"""Database queries. All rows crossing this boundary become Pydantic models."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import Enum
from typing import Any

from .config import ResetConfig
from .resets import is_done, next_reset, utc_now
from .schemas import (
    ItemCreate,
    ItemOut,
    ItemSourceOut,
    ItemUpdate,
    ProgressOut,
    SourceCreate,
    SourceOut,
    UserOut,
)

ITEM_COLUMNS = (
    "id",
    "wowhead_id",
    "name",
    "type",
    "icon_local",
    "periodicity",
    "owner_user_id",
    "url_wowhead",
    "description",
    "notes",
    "last_scraped_at",
    "created_at",
    "updated_at",
)
ITEM_WRITABLE_COLUMNS = ITEM_COLUMNS[1:-2]  # no id, created_at or updated_at


def timestamp() -> str:
    """UTC ISO-8601 string, always with an explicit offset."""
    return utc_now().isoformat(timespec="seconds")


def list_users(connection: sqlite3.Connection) -> list[UserOut]:
    rows = connection.execute("SELECT id, name FROM users ORDER BY id").fetchall()
    return [UserOut(id=row["id"], name=row["name"]) for row in rows]


def user_exists(connection: sqlite3.Connection, user_id: int) -> bool:
    return (
        connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        is not None
    )


def item_exists(connection: sqlite3.Connection, item_id: int) -> bool:
    return (
        connection.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
        is not None
    )


def rename_user(connection: sqlite3.Connection, user_id: int, name: str) -> bool:
    cursor = connection.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
    connection.commit()
    return cursor.rowcount > 0


def list_sources(connection: sqlite3.Connection) -> list[SourceOut]:
    rows = connection.execute(
        "SELECT id, name, kind, zone, wowhead_url FROM sources ORDER BY name"
    ).fetchall()
    return [SourceOut(**dict(row)) for row in rows]


def create_source(connection: sqlite3.Connection, payload: SourceCreate) -> SourceOut:
    cursor = connection.execute(
        "INSERT INTO sources (name, kind, zone, wowhead_url) VALUES (?, ?, ?, ?)",
        (payload.name, payload.kind.value, payload.zone, payload.wowhead_url),
    )
    connection.commit()
    return SourceOut(
        id=cursor.lastrowid,
        name=payload.name,
        kind=payload.kind,
        zone=payload.zone,
        wowhead_url=payload.wowhead_url,
    )


def _source_links(
    connection: sqlite3.Connection, item_id: int
) -> list[ItemSourceOut]:
    rows = connection.execute(
        """
        SELECT s.id AS source_id, s.name AS source_name, s.kind AS source_kind,
               s.zone, s.wowhead_url, i.drop_rate, i.difficulty, i.conditions
        FROM item_sources AS i
        JOIN sources AS s ON s.id = i.source_id
        WHERE i.item_id = ?
        ORDER BY s.name, i.difficulty
        """,
        (item_id,),
    ).fetchall()
    return [ItemSourceOut(**dict(row)) for row in rows]


def _item_out(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    now: datetime,
    config: ResetConfig,
    users: list[UserOut],
) -> ItemOut:
    progress_rows = connection.execute(
        "SELECT user_id, last_done_at FROM progress WHERE item_id = ?", (row["id"],)
    ).fetchall()
    progress = {
        str(user.id): ProgressOut(done=False, last_done_at=None) for user in users
    }
    for record in progress_rows:
        progress[str(record["user_id"])] = ProgressOut(
            done=is_done(
                record["last_done_at"], now, row["periodicity"], config
            ),
            last_done_at=record["last_done_at"],
        )

    return ItemOut(
        id=row["id"],
        wowhead_id=row["wowhead_id"],
        name=row["name"],
        type=row["type"],
        icon_local=row["icon_local"],
        periodicity=row["periodicity"],
        owner_user_id=row["owner_user_id"],
        url_wowhead=row["url_wowhead"],
        description=row["description"],
        notes=row["notes"],
        last_scraped_at=row["last_scraped_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        next_reset_at=next_reset(now, row["periodicity"], config).isoformat(),
        sources=_source_links(connection, row["id"]),
        progress=progress,
    )


def list_items(
    connection: sqlite3.Connection,
    now: datetime,
    config: ResetConfig,
) -> list[ItemOut]:
    rows = connection.execute(
        f"SELECT {', '.join(ITEM_COLUMNS)} FROM items ORDER BY name COLLATE NOCASE"
    ).fetchall()
    users = list_users(connection)
    return [_item_out(connection, row, now, config, users) for row in rows]


def get_item(
    connection: sqlite3.Connection,
    item_id: int,
    now: datetime,
    config: ResetConfig,
) -> ItemOut | None:
    row = connection.execute(
        f"SELECT {', '.join(ITEM_COLUMNS)} FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        return None
    return _item_out(connection, row, now, config, list_users(connection))


def _check_source_ids(
    connection: sqlite3.Connection, source_ids: list[int]
) -> None:
    for source_id in source_ids:
        exists = connection.execute(
            "SELECT 1 FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(f"Unknown source id: {source_id}")


def _check_owner(connection: sqlite3.Connection, owner_user_id: int | None) -> None:
    if owner_user_id is None:
        return
    if not user_exists(connection, owner_user_id):
        raise ValueError(f"Unknown owner user id: {owner_user_id}")


def create_item(connection: sqlite3.Connection, payload: ItemCreate) -> int:
    """Insert an item and its source relations in one transaction."""
    _check_source_ids(connection, [link.source_id for link in payload.sources])
    _check_owner(connection, payload.owner_user_id)
    data = payload.model_dump(exclude={"sources"})
    now = timestamp()
    columns = [*ITEM_WRITABLE_COLUMNS, "created_at", "updated_at"]
    values = [
        _plain(value) for value in (data[column] for column in ITEM_WRITABLE_COLUMNS)
    ] + [now, now]

    try:
        cursor = connection.execute(
            f"INSERT INTO items ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
        item_id = int(cursor.lastrowid)
        _insert_source_links(connection, item_id, payload.sources)
        connection.commit()
    except sqlite3.IntegrityError as err:
        connection.rollback()
        raise ValueError(f"Could not create item: {err}") from err
    return item_id


def update_item(
    connection: sqlite3.Connection, item_id: int, payload: ItemUpdate
) -> bool:
    if not connection.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone():
        return False

    data = payload.model_dump(exclude_unset=True)
    links = data.pop("sources", None)
    if links is not None:
        _check_source_ids(connection, [link["source_id"] for link in links])
    if "owner_user_id" in data:
        _check_owner(connection, data["owner_user_id"])

    data["updated_at"] = timestamp()
    assignments = ", ".join(f"{column} = ?" for column in data)
    try:
        connection.execute(
            f"UPDATE items SET {assignments} WHERE id = ?",
            [_plain(value) for value in data.values()] + [item_id],
        )
        if links is not None:
            connection.execute("DELETE FROM item_sources WHERE item_id = ?", (item_id,))
            _insert_source_links(connection, item_id, links)
        connection.commit()
    except sqlite3.IntegrityError as err:
        connection.rollback()
        raise ValueError(f"Could not update item: {err}") from err
    return True


def _insert_source_links(
    connection: sqlite3.Connection, item_id: int, links: list[Any]
) -> None:
    for link in links:
        values = (
            link if isinstance(link, dict) else link.model_dump()
        )
        connection.execute(
            """
            INSERT INTO item_sources (item_id, source_id, drop_rate, difficulty, conditions)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                item_id,
                values["source_id"],
                values["drop_rate"],
                values["difficulty"],
                values["conditions"],
            ),
        )


def delete_item(connection: sqlite3.Connection, item_id: int) -> bool:
    cursor = connection.execute("DELETE FROM items WHERE id = ?", (item_id,))
    connection.commit()
    return cursor.rowcount > 0


def set_progress(
    connection: sqlite3.Connection,
    item_id: int,
    user_id: int,
    done: bool,
    now: datetime | None = None,
) -> str | None:
    """Store last_done_at when done, remove the record when undone."""
    moment = (now or utc_now()).isoformat(timespec="seconds")
    if done:
        connection.execute(
            """
            INSERT INTO progress (item_id, user_id, last_done_at) VALUES (?, ?, ?)
            ON CONFLICT(item_id, user_id) DO UPDATE SET last_done_at = excluded.last_done_at
            """,
            (item_id, user_id, moment),
        )
    else:
        connection.execute(
            "DELETE FROM progress WHERE item_id = ? AND user_id = ?", (item_id, user_id)
        )
    connection.commit()
    return moment if done else None


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value
