"""Integration tests for the HTTP contract and per-user progress."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

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
    assert meta["weekly_weekday"] == 1
    assert meta["weekly_hour_utc"] == 7


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
    moment = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)  # Tuesday

    monkeypatch.setenv("WEEKLY_RESET_HOUR_UTC", "12")
    monkeypatch.setattr("app.main.utc_now", lambda: moment)
    monkeypatch.setattr("app.repository.utc_now", lambda: moment)
    client.put(f"/api/items/{item_id}/progress?user_id={user_id}", json={"done": True})
    assert client.get(f"/api/items/{item_id}").json()["progress"][str(user_id)]["done"] is True

    # One minute before the next reset it is still done.
    just_before = datetime(2026, 11, 24, 11, 59, tzinfo=UTC)
    monkeypatch.setattr("app.main.utc_now", lambda: just_before)
    assert client.get(f"/api/items/{item_id}").json()["progress"][str(user_id)]["done"] is True

    # After Tuesday 12:00 UTC it becomes pending without any scheduled task.
    just_after = datetime(2026, 11, 24, 12, 1, tzinfo=UTC)
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
