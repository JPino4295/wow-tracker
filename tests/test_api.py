"""Integration tests for the HTTP contract and per-user progress."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

UTC = timezone.utc


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tracker.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app) as test_client:
        yield test_client


def mount_payload() -> dict[str, Any]:
    return {
        "name": "Riendas de la venganza",
        "wowhead_id": 137539,
        "type": "mount",
        "periodicity": "weekly",
        "description": "Montura de Sagrario de dominación",
        "notes": "Rare drop en Selva de Huesos",
    }


@pytest.fixture
def source_id(client: TestClient) -> int:
    response = client.post(
        "/api/sources",
        json={
            "name": "Sagrario de dominación",
            "kind": "raid",
            "zone": "Sagrario de dominación",
            "wowhead_url": "https://www.wowhead.com/es/zone=13561",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_health_and_meta_expose_two_users_and_reset_config(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    index = client.get("/")
    assert index.status_code == 200
    assert "WoW Farm Tracker" in index.text

    meta = client.get("/api/meta").json()
    assert [user["name"] for user in meta["users"]] == ["Jugador 1", "Jugador 2"]
    assert meta["daily_hour_utc"] == 7
    assert meta["weekly_weekday"] == 2
    assert meta["weekly_hour_utc"] == 7


def test_secret_source_can_be_created_and_listed(client: TestClient) -> None:
    created = client.post(
        "/api/sources",
        json={
            "name": "Cofre de secreto",
            "kind": "secret",
            "zone": "Isla de los Truenos",
            "wowhead_url": "https://www.wowhead.com/es/object=123456",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Cofre de secreto"
    assert body["kind"] == "secret"

    listed = client.get("/api/sources").json()
    assert [source["id"] for source in listed] == [body["id"]]
    assert listed[0]["kind"] == "secret"


def test_one_time_item_can_be_created_and_read_without_a_next_reset(
    client: TestClient,
) -> None:
    created = client.post("/api/items", json={**mount_payload(), "periodicity": "once"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["periodicity"] == "once"
    assert body["next_reset_at"] == ""

    detail = client.get(f"/api/items/{body['id']}").json()
    assert detail["periodicity"] == "once"
    assert detail["next_reset_at"] == ""


def test_secret_source_attachment_makes_item_one_time(client: TestClient) -> None:
    secret = client.post(
        "/api/sources", json={"name": "Cofre de secreto", "kind": "secret"}
    ).json()

    created = client.post(
        "/api/items",
        json={**mount_payload(), "sources": [{"source_id": secret["id"]}]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["periodicity"] == "once"

    weekly = client.post(
        "/api/items",
        json={**mount_payload(), "name": "Otro objeto", "wowhead_id": 137540},
    ).json()
    assert weekly["periodicity"] == "weekly"

    patched = client.patch(
        f"/api/items/{weekly['id']}",
        json={
            "sources": [
                {
                    "source_id": secret["id"],
                    "drop_rate": None,
                    "difficulty": "",
                    "conditions": "",
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["periodicity"] == "once"


def test_once_progress_survives_daily_and_weekly_resets_and_is_per_player(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = client.post(
        "/api/sources", json={"name": "Cofre de secreto", "kind": "secret"}
    ).json()
    once_id = client.post(
        "/api/items",
        json={
            **mount_payload(),
            "sources": [
                {
                    "source_id": secret["id"],
                    "drop_rate": None,
                    "difficulty": "",
                    "conditions": "",
                }
            ],
        },
    ).json()["id"]
    weekly_id = client.post(
        "/api/items",
        json={**mount_payload(), "name": "Objeto semanal", "wowhead_id": 137540},
    ).json()["id"]
    first, second = [user["id"] for user in client.get("/api/meta").json()["users"]]

    moment = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: moment)
    monkeypatch.setattr("app.repository.utc_now", lambda: moment)
    client.put(f"/api/items/{once_id}/progress?user_id={first}", json={"done": True})
    client.put(f"/api/items/{weekly_id}/progress?user_id={first}", json={"done": True})

    # Well past the next daily and weekly resets.
    later = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: later)
    monkeypatch.setattr("app.repository.utc_now", lambda: later)

    once = client.get(f"/api/items/{once_id}").json()
    assert once["progress"][str(first)]["done"] is True
    assert once["progress"][str(second)]["done"] is False
    weekly = client.get(f"/api/items/{weekly_id}").json()
    assert weekly["progress"][str(first)]["done"] is False


def test_removing_last_secret_source_keeps_once_until_periodicity_is_edited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = client.post(
        "/api/sources", json={"name": "Cofre de secreto", "kind": "secret"}
    ).json()
    item_id = client.post(
        "/api/items",
        json={
            **mount_payload(),
            "sources": [
                {
                    "source_id": secret["id"],
                    "drop_rate": None,
                    "difficulty": "",
                    "conditions": "",
                }
            ],
        },
    ).json()["id"]
    user_id = client.get("/api/meta").json()["users"][0]["id"]

    moment = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: moment)
    monkeypatch.setattr("app.repository.utc_now", lambda: moment)
    client.put(f"/api/items/{item_id}/progress?user_id={user_id}", json={"done": True})

    removed = client.patch(f"/api/items/{item_id}", json={"sources": []})
    assert removed.status_code == 200, removed.text
    assert removed.json()["periodicity"] == "once"
    assert removed.json()["next_reset_at"] == ""

    later = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: later)
    monkeypatch.setattr("app.repository.utc_now", lambda: later)
    assert client.get(f"/api/items/{item_id}").json()["progress"][str(user_id)]["done"] is True

    edited = client.patch(f"/api/items/{item_id}", json={"periodicity": "weekly"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["periodicity"] == "weekly"
    assert edited.json()["next_reset_at"] != ""


def test_item_with_source_is_created_and_url_is_generated(
    client: TestClient, source_id: int
) -> None:
    payload = mount_payload()
    payload["sources"] = [
        {
            "source_id": source_id,
            "drop_rate": 12.5,
            "difficulty": "Mítico",
            "conditions": "Solo en la última ala",
        }
    ]

    created = client.post("/api/items", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["url_wowhead"] == "https://www.wowhead.com/es/item=137539"
    assert body["sources"][0]["source_name"] == "Sagrario de dominación"
    assert body["sources"][0]["drop_rate"] == 12.5

    detail = client.get(f"/api/items/{body['id']}").json()
    assert detail["notes"] == "Rare drop en Selva de Huesos"
    assert detail["next_reset_at"].endswith("+00:00")


def test_progress_is_per_user_and_filters_respect_the_selected_user(
    client: TestClient, source_id: int
) -> None:
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]
    users = client.get("/api/meta").json()["users"]
    first, second = users[0]["id"], users[1]["id"]

    before = client.get("/api/items").json()[0]
    assert before["progress"][str(first)]["done"] is False
    assert before["progress"][str(second)]["done"] is False

    marked = client.put(
        f"/api/items/{item_id}/progress?user_id={first}", json={"done": True}
    )
    assert marked.status_code == 200, marked.text
    after = marked.json()
    assert after["progress"][str(first)]["done"] is True
    assert after["progress"][str(first)]["last_done_at"] is not None
    assert after["progress"][str(second)]["done"] is False

    done_for_first = client.get(f"/api/items?status=done&user_id={first}").json()
    assert [item["id"] for item in done_for_first] == [item_id]
    assert client.get(f"/api/items?status=pending&user_id={first}").json() == []
    pending_for_second = client.get(f"/api/items?status=pending&user_id={second}").json()
    assert [item["id"] for item in pending_for_second] == [item_id]

    undone = client.put(
        f"/api/items/{item_id}/progress?user_id={first}", json={"done": False}
    )
    assert undone.json()["progress"][str(first)]["last_done_at"] is None


def test_combinable_filters(client: TestClient, source_id: int) -> None:
    mount = mount_payload()
    mount["sources"] = [{"source_id": source_id, "difficulty": "", "conditions": ""}]
    client.post("/api/items", json=mount)
    client.post(
        "/api/items",
        json={
            "name": "Juguete cualquiera",
            "type": "toy",
            "periodicity": "daily",
        },
    )

    filtered = client.get(
        "/api/items?type=mount&periodicity=weekly&source_kind=raid&q=venganza"
    ).json()
    assert [item["name"] for item in filtered] == ["Riendas de la venganza"]
    assert client.get("/api/items?type=pet").json() == []
    assert client.get(f"/api/items?source_id={source_id}").json()[0]["type"] == "mount"


def test_notes_can_be_edited_and_invalid_input_is_rejected(
    client: TestClient, source_id: int
) -> None:
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]
    patched = client.patch(f"/api/items/{item_id}", json={"notes": "Nueva nota"})
    assert patched.status_code == 200
    assert patched.json()["notes"] == "Nueva nota"

    invalid_type = client.post("/api/items", json={**mount_payload(), "type": "wrong"})
    assert invalid_type.status_code == 422

    unsafe_url = client.post(
        "/api/items", json={**mount_payload(), "url_wowhead": "javascript:alert(1)"}
    )
    assert unsafe_url.status_code == 422

    unknown_source = client.post(
        "/api/items",
        json={**mount_payload(), "sources": [{"source_id": 9999}]},
    )
    assert unknown_source.status_code == 400

    duplicate_source = client.post(
        "/api/sources", json={"name": "Sagrario de dominación", "kind": "raid"}
    )
    assert duplicate_source.status_code == 409


def test_patch_source_updates_fields_and_reuses_contract_errors(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/sources",
        json={"name": "Sagrario", "kind": "raid", "zone": "Torgasta"},
    ).json()
    other = client.post(
        "/api/sources", json={"name": "Cofre", "kind": "secret"}
    ).json()

    patched = client.patch(
        f"/api/sources/{source['id']}",
        json={
            "zone": "Selva de Huesos",
            "wowhead_url": "https://www.wowhead.com/es/zone=1",
        },
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()) == {"id", "name", "kind", "zone", "wowhead_url"}
    assert patched.json() == {
        "id": source["id"],
        "name": "Sagrario",
        "kind": "raid",
        "zone": "Selva de Huesos",
        "wowhead_url": "https://www.wowhead.com/es/zone=1",
    }

    assert client.patch("/api/sources/9999", json={"name": "Nadie"}).status_code == 404
    assert (
        client.patch(f"/api/sources/{source['id']}", json={"kind": "wrong"}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/sources/{source['id']}",
            json={"wowhead_url": "ftp://inseguro"},
        ).status_code
        == 422
    )
    for field in ("name", "kind", "zone", "wowhead_url"):
        null_patch = client.patch(f"/api/sources/{source['id']}", json={field: None})
        assert null_patch.status_code == 422, null_patch.text

    conflict = client.patch(
        f"/api/sources/{other['id']}",
        json={"name": "Sagrario", "kind": "raid"},
    )
    assert conflict.status_code == 409

    assert client.get("/api/sources").json() == [other, patched.json()]


def test_delete_unused_source_returns_204_and_preserves_items(
    client: TestClient,
) -> None:
    used = client.post("/api/sources", json={"name": "Sagrario", "kind": "raid"}).json()
    unused = client.post(
        "/api/sources", json={"name": "Ruta vieja", "kind": "boss"}
    ).json()
    user_id = client.get("/api/meta").json()["users"][0]["id"]
    payload = mount_payload()
    payload["sources"] = [{"source_id": used["id"]}]
    item_id = client.post("/api/items", json=payload).json()["id"]
    client.put(f"/api/items/{item_id}/progress?user_id={user_id}", json={"done": True})
    before = client.get(f"/api/items/{item_id}").json()

    deleted = client.delete(f"/api/sources/{unused['id']}")
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""
    assert client.delete("/api/sources/9999").status_code == 404

    assert [source["id"] for source in client.get("/api/sources").json()] == [used["id"]]
    assert client.get(f"/api/items/{item_id}").json() == before
    assert before["sources"][0]["source_id"] == used["id"]
    assert before["progress"][str(user_id)]["done"] is True


def test_delete_used_source_returns_409_and_keeps_links(client: TestClient) -> None:
    source = client.post("/api/sources", json={"name": "Sagrario", "kind": "raid"}).json()
    payload = mount_payload()
    payload["sources"] = [{"source_id": source["id"]}]
    item_id = client.post("/api/items", json=payload).json()["id"]
    before = client.get(f"/api/items/{item_id}").json()

    deleted = client.delete(f"/api/sources/{source['id']}")
    assert deleted.status_code == 409, deleted.text
    assert set(deleted.json()) == {"detail"}
    assert deleted.json()["detail"]

    assert client.get("/api/sources").json() == [source]
    assert client.get(f"/api/items/{item_id}").json() == before
    assert before["sources"][0]["source_id"] == source["id"]
    assert before["sources"][0]["source_kind"] == "raid"


def test_existing_source_and_item_routes_are_unchanged_after_source_changes(
    client: TestClient,
) -> None:
    source = client.post(
        "/api/sources",
        json={
            "name": "Sagrario",
            "kind": "raid",
            "zone": "Torgasta",
            "wowhead_url": "https://www.wowhead.com/es/zone=1",
        },
    )
    assert source.status_code == 201, source.text
    unused = client.post("/api/sources", json={"name": "Vieja", "kind": "boss"}).json()
    assert client.get("/api/sources").json() == [source.json(), unused]

    item = client.post(
        "/api/items",
        json={**mount_payload(), "sources": [{"source_id": source.json()["id"]}]},
    )
    assert item.status_code == 201, item.text
    before = item.json()

    patched = client.patch(
        f"/api/sources/{source.json()['id']}", json={"name": "Sagrario renombrado"}
    )
    assert patched.status_code == 200, patched.text
    assert client.delete(f"/api/sources/{unused['id']}").status_code == 204

    listed = client.get("/api/sources").json()
    assert listed == [{**source.json(), "name": "Sagrario renombrado"}]
    assert set(listed[0]) == {"id", "name", "kind", "zone", "wowhead_url"}

    created = client.post("/api/sources", json={"name": "Nueva", "kind": "secret"})
    assert created.status_code == 201, created.text
    assert set(created.json()) == set(source.json())

    items = client.get("/api/items").json()
    assert len(items) == 1
    assert set(items[0]) == set(before)
    assert items[0]["id"] == before["id"]
    assert items[0]["periodicity"] == "weekly"
    assert items[0]["progress"] == before["progress"]
    link = items[0]["sources"][0]
    assert set(link) == set(before["sources"][0])
    assert link["source_id"] == source.json()["id"]
    assert link["source_kind"] == "raid"
    assert link["source_name"] == "Sagrario renombrado"
    assert client.get(f"/api/items/{before['id']}").json() == items[0]


def test_patch_source_kind_only(client: TestClient) -> None:
    source = client.post("/api/sources", json={"name": "Sagrario", "kind": "raid"}).json()

    patched = client.patch(f"/api/sources/{source['id']}", json={"kind": "boss"})
    assert patched.status_code == 200, patched.text
    assert patched.json() == {**source, "kind": "boss"}
    assert [
        item["kind"]
        for item in client.get("/api/sources").json()
        if item["id"] == source["id"]
    ] == ["boss"]


def test_farming_source_create_list_and_filter(client: TestClient) -> None:
    created = client.post(
        "/api/sources",
        json={
            "name": "Ruta de farmeo",
            "kind": "farming",
            "zone": "Isla de los Truenos",
            "wowhead_url": "https://www.wowhead.com/es/zone=1234",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "farming"
    assert set(body) == {"id", "name", "kind", "zone", "wowhead_url"}

    listed = client.get("/api/sources").json()
    assert listed == [body]

    item = client.post(
        "/api/items",
        json={**mount_payload(), "sources": [{"source_id": body["id"]}]},
    )
    assert item.status_code == 201, item.text
    client.post(
        "/api/items",
        json={**mount_payload(), "name": "Objeto sin fuente", "wowhead_id": 137541},
    )

    filtered = client.get("/api/items?source_kind=farming").json()
    assert [entry["name"] for entry in filtered] == ["Riendas de la venganza"]
    assert filtered[0]["sources"][0]["source_kind"] == "farming"

    duplicate = client.post(
        "/api/sources", json={"name": "Ruta de farmeo", "kind": "farming"}
    )
    assert duplicate.status_code == 409
    invalid = client.post("/api/sources", json={"name": "Mal", "kind": "wrong"})
    assert invalid.status_code == 422


def test_farming_source_does_not_force_once(client: TestClient) -> None:
    farming = client.post(
        "/api/sources", json={"name": "Ruta de farmeo", "kind": "farming"}
    ).json()
    secret = client.post(
        "/api/sources", json={"name": "Cofre de secreto", "kind": "secret"}
    ).json()

    created = client.post(
        "/api/items",
        json={**mount_payload(), "sources": [{"source_id": farming["id"]}]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["periodicity"] == "weekly"

    weekly = client.post(
        "/api/items",
        json={**mount_payload(), "name": "Otro objeto", "wowhead_id": 137542},
    ).json()
    patched = client.patch(
        f"/api/items/{weekly['id']}",
        json={
            "sources": [
                {
                    "source_id": farming["id"],
                    "drop_rate": None,
                    "difficulty": "",
                    "conditions": "",
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["periodicity"] == "weekly"

    secret_patch = client.patch(
        f"/api/items/{weekly['id']}",
        json={
            "sources": [
                {
                    "source_id": secret["id"],
                    "drop_rate": None,
                    "difficulty": "",
                    "conditions": "",
                }
            ]
        },
    )
    assert secret_patch.status_code == 200, secret_patch.text
    assert secret_patch.json()["periodicity"] == "once"


def test_owner_assignment_is_persisted_and_validation_rejects_unknown_users(
    client: TestClient,
) -> None:
    users = client.get("/api/meta").json()["users"]
    second = users[1]["id"]

    created = client.post("/api/items", json={**mount_payload(), "owner_user_id": second})
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    assert created.json()["owner_user_id"] == second

    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["owner_user_id"] == second
    # The other player still sees it, so the UI can render it as read-only.
    assert [item["id"] for item in client.get("/api/items").json()] == [item_id]

    shared = client.patch(f"/api/items/{item_id}", json={"owner_user_id": None})
    assert shared.status_code == 200, shared.text
    assert shared.json()["owner_user_id"] is None

    unknown = client.post("/api/items", json={**mount_payload(), "owner_user_id": 9999})
    assert unknown.status_code == 400, unknown.text
    unknown_patch = client.patch(f"/api/items/{item_id}", json={"owner_user_id": 9999})
    assert unknown_patch.status_code == 400, unknown_patch.text


def test_foreign_item_cannot_be_marked_but_shared_and_owned_can(
    client: TestClient,
) -> None:
    users = client.get("/api/meta").json()["users"]
    first, second = users[0]["id"], users[1]["id"]
    foreign_id = client.post(
        "/api/items", json={**mount_payload(), "owner_user_id": second}
    ).json()["id"]

    forbidden = client.put(
        f"/api/items/{foreign_id}/progress?user_id={first}", json={"done": True}
    )
    assert forbidden.status_code == 403, forbidden.text
    assert "different player" in forbidden.json()["detail"]

    allowed = client.put(
        f"/api/items/{foreign_id}/progress?user_id={second}", json={"done": True}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["progress"][str(second)]["done"] is True
    assert allowed.json()["progress"][str(first)]["done"] is False

    shared_payload = {**mount_payload(), "name": "Montura compartida", "wowhead_id": 137540}
    shared_id = client.post("/api/items", json=shared_payload).json()["id"]
    assert client.put(
        f"/api/items/{shared_id}/progress?user_id={first}", json={"done": True}
    ).status_code == 200


def test_delete_item_removes_it(client: TestClient) -> None:
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]
    assert client.delete(f"/api/items/{item_id}").status_code == 204
    assert client.get(f"/api/items/{item_id}").status_code == 404


def test_progress_resets_on_read_after_the_configured_weekly_reset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]
    user_id = client.get("/api/meta").json()["users"][0]["id"]
    moment = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)  # Wednesday

    monkeypatch.setenv("WEEKLY_RESET_HOUR_UTC", "12")
    monkeypatch.setattr("app.main.utc_now", lambda: moment)
    monkeypatch.setattr("app.repository.utc_now", lambda: moment)
    client.put(f"/api/items/{item_id}/progress?user_id={user_id}", json={"done": True})
    assert client.get(f"/api/items/{item_id}").json()["progress"][str(user_id)]["done"] is True

    # One minute before the next reset it is still done.
    just_before = datetime(2026, 11, 25, 11, 59, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: just_before)
    assert client.get(f"/api/items/{item_id}").json()["progress"][str(user_id)]["done"] is True

    # After Wednesday 12:00 UTC it becomes pending without any scheduled task.
    just_after = datetime(2026, 11, 25, 12, 1, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: just_after)
    assert client.get(f"/api/items/{item_id}").json()["progress"][str(user_id)]["done"] is False


def test_user_names_can_be_renamed(client: TestClient) -> None:
    users = client.get("/api/meta").json()["users"]
    first, second = users[0]["id"], users[1]["id"]
    response = client.patch(f"/api/users/{first}", json={"name": "Ana"})
    assert response.status_code == 200
    assert response.json()["users"][0]["name"] == "Ana"
    duplicate = client.patch(f"/api/users/{second}", json={"name": "Ana"})
    assert duplicate.status_code == 409

    assert client.patch("/api/users/9999", json={"name": "Nadie"}).status_code == 404
    assert client.put(
        "/api/items/9999/progress?user_id=1", json={"done": True}
    ).status_code == 404


def test_drop_rate_can_be_edited_after_creation(client: TestClient, source_id: int) -> None:
    item_id = client.post(
        "/api/items",
        json={
            **mount_payload(),
            "sources": [{"source_id": source_id, "difficulty": "Heroica"}],
        },
    ).json()["id"]

    patched = client.patch(
        f"/api/items/{item_id}",
        json={
            "sources": [
                {
                    "source_id": source_id,
                    "drop_rate": 7.5,
                    "difficulty": "Mítico",
                    "conditions": "Solo con la Rare activa",
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    link = patched.json()["sources"][0]
    assert link["drop_rate"] == 7.5
    assert link["difficulty"] == "Mítico"
    assert link["conditions"] == "Solo con la Rare activa"


def test_patch_with_partial_source_link_applies_link_defaults(
    client: TestClient, source_id: int
) -> None:
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]

    patched = client.patch(
        f"/api/items/{item_id}",
        json={"sources": [{"source_id": source_id}]},
    )
    assert patched.status_code == 200, patched.text
    link = patched.json()["sources"][0]
    assert link["source_id"] == source_id
    assert link["drop_rate"] is None
    assert link["difficulty"] == ""
    assert link["conditions"] == ""

    listed = client.get("/api/items").json()
    assert [item["id"] for item in listed] == [item_id]
    listed_link = listed[0]["sources"][0]
    assert listed_link["drop_rate"] is None
    assert listed_link["difficulty"] == ""
    assert listed_link["conditions"] == ""


def test_patch_rejects_source_link_without_source_id(
    client: TestClient, source_id: int
) -> None:
    item_id = client.post(
        "/api/items",
        json={**mount_payload(), "sources": [{"source_id": source_id}]},
    ).json()["id"]
    before = client.get(f"/api/items/{item_id}").json()

    rejected = client.patch(
        f"/api/items/{item_id}",
        json={"sources": [{"drop_rate": 5}]},
    )
    assert rejected.status_code == 422, rejected.text
    assert client.get(f"/api/items/{item_id}").json() == before


def test_patch_of_scalar_fields_preserves_existing_source_links(
    client: TestClient, source_id: int
) -> None:
    item_id = client.post(
        "/api/items",
        json={
            **mount_payload(),
            "sources": [
                {
                    "source_id": source_id,
                    "drop_rate": 12.5,
                    "difficulty": "Mítico",
                    "conditions": "Solo en la última ala",
                }
            ],
        },
    ).json()["id"]

    patched = client.patch(f"/api/items/{item_id}", json={"notes": "Nota nueva"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["notes"] == "Nota nueva"

    listed = client.get("/api/items").json()
    assert [item["id"] for item in listed] == [item_id]
    link = listed[0]["sources"][0]
    assert link["source_id"] == source_id
    assert link["drop_rate"] == 12.5
    assert link["difficulty"] == "Mítico"
    assert link["conditions"] == "Solo en la última ala"


def test_item_can_be_edited_completely(client: TestClient) -> None:
    second = client.get("/api/meta").json()["users"][1]["id"]
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]

    patched = client.patch(
        f"/api/items/{item_id}",
        json={
            "name": "Juguete editado",
            "wowhead_id": 137541,
            "url_wowhead": "https://www.wowhead.com/es/item=137541",
            "type": "toy",
            "periodicity": "daily",
            "owner_user_id": second,
            "icon_local": "juguete.png",
            "description": "Descripción nueva",
            "notes": "Notas nuevas",
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "Juguete editado"
    assert body["wowhead_id"] == 137541
    assert body["url_wowhead"] == "https://www.wowhead.com/es/item=137541"
    assert body["type"] == "toy"
    assert body["periodicity"] == "daily"
    assert body["owner_user_id"] == second
    assert body["icon_local"] == "juguete.png"
    assert body["description"] == "Descripción nueva"
    assert body["notes"] == "Notas nuevas"


def test_sources_can_be_added_later_and_survive_unrelated_edits(
    client: TestClient, source_id: int
) -> None:
    item_id = client.post("/api/items", json=mount_payload()).json()["id"]
    assert client.get(f"/api/items/{item_id}").json()["sources"] == []

    added = client.patch(
        f"/api/items/{item_id}",
        json={
            "sources": [
                {
                    "source_id": source_id,
                    "drop_rate": 15,
                    "difficulty": "Mítico",
                    "conditions": "Solo con la Rare activa",
                }
            ]
        },
    )
    assert added.status_code == 200, added.text
    assert added.json()["sources"][0]["drop_rate"] == 15

    renamed = client.patch(f"/api/items/{item_id}", json={"name": "Montura renombrada"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Montura renombrada"
    link = renamed.json()["sources"][0]
    assert link["source_id"] == source_id
    assert link["drop_rate"] == 15
    assert link["difficulty"] == "Mítico"


def test_user_without_data_can_be_deleted_and_is_not_reseeded(
    client: TestClient,
) -> None:
    users = client.get("/api/meta").json()["users"]
    first, second = users[0]["id"], users[1]["id"]

    deleted = client.delete(f"/api/users/{second}")
    assert deleted.status_code == 204, deleted.text
    assert [user["id"] for user in client.get("/api/meta").json()["users"]] == [first]

    # Restarting the app must not bring the deleted default player back.
    init_db("2026-11-17T08:00:00+00:00")
    assert [user["id"] for user in client.get("/api/meta").json()["users"]] == [first]


def test_user_can_be_created_with_a_unique_non_blank_name(
    client: TestClient,
) -> None:
    before = client.get("/api/meta").json()["users"]

    created = client.post("/api/users", json={"name": "  Nueva jugadora  "})
    assert created.status_code == 201, created.text
    after = created.json()["users"]
    assert len(after) == len(before) + 1
    assert after[-1]["name"] == "Nueva jugadora"

    duplicate = client.post("/api/users", json={"name": "Nueva jugadora"})
    assert duplicate.status_code == 409

    blank = client.post("/api/users", json={"name": "   "})
    assert blank.status_code == 422

    # The newly created player can own items just like the seeded ones.
    new_id = after[-1]["id"]
    item = client.post(
        "/api/items", json={**mount_payload(), "owner_user_id": new_id}
    )
    assert item.status_code == 201, item.text
    assert item.json()["owner_user_id"] == new_id


def test_existing_daily_weekly_contract_progress_and_auth_are_unchanged(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
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
        INSERT INTO users VALUES (1, 'Ana', '2026-01-01');
        INSERT INTO users VALUES (2, 'Beto', '2026-01-01');
        INSERT INTO sources VALUES (1, 'Sagrario', 'raid', 'Sagrario', 'https://example.com');
        INSERT INTO items (id, name, type, periodicity, owner_user_id, created_at, updated_at)
          VALUES (1, 'Montura semanal', 'mount', 'weekly', NULL, '2026-01-01', '2026-01-01');
        INSERT INTO items (id, name, type, periodicity, owner_user_id, created_at, updated_at)
          VALUES (2, 'Juguete diario', 'toy', 'daily', 2, '2026-01-01', '2026-01-01');
        INSERT INTO item_sources (item_id, source_id, drop_rate, difficulty, conditions)
          VALUES (1, 1, 12.5, 'Mítico', 'Rare');
        INSERT INTO progress VALUES (1, 1, '2026-11-17T08:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()

    moment = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: moment)
    monkeypatch.setattr("app.repository.utc_now", lambda: moment)

    with TestClient(app) as legacy_client:
        by_id = {item["id"]: item for item in legacy_client.get("/api/items").json()}
        assert set(by_id) == {1, 2}
        assert by_id[1]["name"] == "Montura semanal"
        assert by_id[1]["periodicity"] == "weekly"
        assert by_id[1]["sources"][0]["source_kind"] == "raid"
        assert by_id[2]["periodicity"] == "daily"
        assert by_id[2]["owner_user_id"] == 2
        assert by_id[1]["progress"]["1"]["done"] is True
        assert by_id[1]["progress"]["2"]["done"] is False

        assert [i["id"] for i in legacy_client.get("/api/items?periodicity=weekly").json()] == [1]
        assert [i["id"] for i in legacy_client.get("/api/items?source_kind=raid").json()] == [1]
        done = legacy_client.get("/api/items?status=done&user_id=1").json()
        assert [item["id"] for item in done] == [1]

        forbidden = legacy_client.put(
            "/api/items/2/progress?user_id=1", json={"done": True}
        )
        assert forbidden.status_code == 403

        patched = legacy_client.patch("/api/items/1", json={"notes": "Nota nueva"})
        assert patched.status_code == 200
        assert patched.json()["notes"] == "Nota nueva"

        after = datetime(2026, 11, 18, 7, 30, tzinfo=UTC)
        monkeypatch.setattr("app.main.utc_now", lambda: after)
        assert legacy_client.get("/api/items/1").json()["progress"]["1"]["done"] is False

        assert legacy_client.post(
            "/api/items", json={**mount_payload(), "type": "wrong"}
        ).status_code == 422
        assert legacy_client.post(
            "/api/items",
            json={**mount_payload(), "sources": [{"source_id": 9999}]},
        ).status_code == 400


def test_user_deletion_is_blocked_when_last_or_still_referenced(
    client: TestClient,
) -> None:
    users = client.get("/api/meta").json()["users"]
    first, second = users[0]["id"], users[1]["id"]
    item_id = client.post(
        "/api/items", json={**mount_payload(), "owner_user_id": second}
    ).json()["id"]

    owned = client.delete(f"/api/users/{second}")
    assert owned.status_code == 409, owned.text
    assert "assigned item" in owned.json()["detail"]

    client.patch(f"/api/items/{item_id}", json={"owner_user_id": None})
    client.put(f"/api/items/{item_id}/progress?user_id={second}", json={"done": True})
    with_progress = client.delete(f"/api/users/{second}")
    assert with_progress.status_code == 409, with_progress.text
    assert "marked as done" in with_progress.json()["detail"]

    client.put(f"/api/items/{item_id}/progress?user_id={second}", json={"done": False})
    assert client.delete(f"/api/users/{second}").status_code == 204

    last = client.delete(f"/api/users/{first}")
    assert last.status_code == 409, last.text
    assert "last player" in last.json()["detail"]
    assert client.delete("/api/users/9999").status_code == 404
