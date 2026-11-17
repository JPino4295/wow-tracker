"""FastAPI application: JSON API plus the static single-page frontend."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status as http_status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import repository as repo
from .config import BASE_DIR, ResetConfig, data_dir, db_path, images_dir
from .db import get_connection, init_db
from .resets import utc_now
from .schemas import (
    ItemCreate,
    ItemOut,
    ItemType,
    ItemUpdate,
    MetaOut,
    Periodicity,
    ProgressOut,
    ProgressUpdate,
    SourceCreate,
    SourceKind,
    SourceOut,
    UserUpdate,
)

logger = logging.getLogger(__name__)
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        init_db(repo.timestamp())
    except (OSError, sqlite3.Error) as err:
        raise RuntimeError(
            f"Cannot write to the data directory ({data_dir()}). "
            "Check the UID/GID ownership of the mounted volume."
        ) from err
    logger.info("Database ready at %s", db_path())
    yield


app = FastAPI(title="WoW Farm Tracker", version="1.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount(
    "/media",
    StaticFiles(directory=images_dir(), check_dir=False),
    name="media",
)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta", response_model=MetaOut)
def meta(connection: sqlite3.Connection = Depends(get_connection)) -> MetaOut:
    config = ResetConfig.from_env()
    return MetaOut(
        users=repo.list_users(connection),
        daily_hour_utc=config.daily_hour_utc,
        weekly_weekday=config.weekly_weekday,
        weekly_hour_utc=config.weekly_hour_utc,
    )


@app.get("/api/sources", response_model=list[SourceOut])
def sources(connection: sqlite3.Connection = Depends(get_connection)) -> list[SourceOut]:
    return repo.list_sources(connection)


@app.post("/api/sources", response_model=SourceOut, status_code=http_status.HTTP_201_CREATED)
def add_source(
    payload: SourceCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> SourceOut:
    try:
        return repo.create_source(connection, payload)
    except sqlite3.IntegrityError as err:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, "That source already exists"
        ) from err


@app.get("/api/items", response_model=list[ItemOut])
def items(
    type: ItemType | None = None,
    periodicity: Periodicity | None = None,
    source_kind: SourceKind | None = None,
    source_id: int | None = None,
    status: Literal["done", "pending"] | None = None,
    user_id: int | None = None,
    q: str | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[ItemOut]:
    now = utc_now()
    config = ResetConfig.from_env()
    result = repo.list_items(connection, now, config)

    effective_user = user_id
    if status is not None and effective_user is None:
        effective_user = _first_user_id(connection)

    def matches(item: ItemOut) -> bool:
        if type is not None and item.type != type:
            return False
        if periodicity is not None and item.periodicity != periodicity:
            return False
        if source_id is not None and not any(
            link.source_id == source_id for link in item.sources
        ):
            return False
        if source_kind is not None and not any(
            link.source_kind == source_kind for link in item.sources
        ):
            return False
        if status is not None:
            key = str(effective_user)
            done = item.progress.get(key, ProgressOut(done=False, last_done_at=None)).done
            if done != (status == "done"):
                return False
        if q and q.strip().lower() not in f"{item.name} {item.description}".lower():
            return False
        return True

    return [item for item in result if matches(item)]


@app.post("/api/items", response_model=ItemOut, status_code=http_status.HTTP_201_CREATED)
def add_item(
    payload: ItemCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> ItemOut:
    try:
        item_id = repo.create_item(connection, payload)
    except ValueError as err:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(err)) from err
    return _must_get_item(connection, item_id)


@app.get("/api/items/{item_id}", response_model=ItemOut)
def item_detail(
    item_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> ItemOut:
    return _must_get_item(connection, item_id)


@app.patch("/api/items/{item_id}", response_model=ItemOut)
def edit_item(
    item_id: int,
    payload: ItemUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> ItemOut:
    try:
        updated = repo.update_item(connection, item_id, payload)
    except ValueError as err:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(err)) from err
    if not updated:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Item not found")
    return _must_get_item(connection, item_id)


@app.delete("/api/items/{item_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def remove_item(
    item_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> None:
    if not repo.delete_item(connection, item_id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Item not found")


@app.put("/api/items/{item_id}/progress", response_model=ItemOut)
def mark_progress(
    item_id: int,
    payload: ProgressUpdate,
    user_id: int = Query(ge=1),
    connection: sqlite3.Connection = Depends(get_connection),
) -> ItemOut:
    if not repo.user_exists(connection, user_id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "User not found")
    current = _must_get_item(connection, item_id)
    if current.owner_user_id is not None and current.owner_user_id != user_id:
        raise HTTPException(
            http_status.HTTP_403_FORBIDDEN,
            "This item is assigned to a different player",
        )
    repo.set_progress(connection, item_id, user_id, payload.done, utc_now())
    return _must_get_item(connection, item_id)


@app.patch("/api/users/{user_id}", response_model=MetaOut)
def edit_user(
    user_id: int,
    payload: UserUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> MetaOut:
    try:
        renamed = repo.rename_user(connection, user_id, payload.name)
    except sqlite3.IntegrityError as err:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, "That name is already in use"
        ) from err
    if not renamed:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "User not found")
    return meta(connection)


@app.delete("/api/users/{user_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def remove_user(
    user_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> None:
    if not repo.user_exists(connection, user_id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "User not found")
    if repo.user_count(connection) <= 1:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT, "Cannot delete the last player"
        )
    owned, progress = repo.user_usage(connection, user_id)
    if owned:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"Player still has {owned} assigned item(s). Reassign or delete them first.",
        )
    if progress:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"Player still has {progress} item(s) marked as done. Unmark them first.",
        )
    if not repo.delete_user(connection, user_id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "User not found")


def _must_get_item(connection: sqlite3.Connection, item_id: int) -> ItemOut:
    result = repo.get_item(connection, item_id, utc_now(), ResetConfig.from_env())
    if result is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Item not found")
    return result


def _first_user_id(connection: sqlite3.Connection) -> int:
    users = repo.list_users(connection)
    return users[0].id if users else 0
