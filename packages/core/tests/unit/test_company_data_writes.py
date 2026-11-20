"""The company simulator and client-slot routes replace or wipe the whole
company, so their writes are the principal's, as the workspace settings and
the People list are. Reads stay open to the team."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import clients as clients_route
from openexecutive.api.routes import fixtures as fixtures_route
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store

OWNER = {"x-caller-email": "pat@example.com"}
TEAMMATE = {"x-caller-email": "sam@example.com"}

WRITES = [
    ("post", "/fixtures/reset"),
    ("post", "/fixtures/snapshot"),
    ("post", "/fixtures/unload"),
    ("post", "/fixtures/acme/load"),
    ("post", "/fixtures/generate"),
    ("post", "/fixtures"),
    ("delete", "/fixtures/acme"),
    ("post", "/clients"),
    ("post", "/clients/save"),
    ("post", "/clients/acme/activate"),
    ("patch", "/clients/acme"),
    ("delete", "/clients/acme"),
]


@pytest.fixture()
def people_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "people.db"
    monkeypatch.setattr(people_store, "DB_PATH", path)
    people_store.initialize_db(path)
    people_registry.invalidate()
    yield path
    people_registry.invalidate()


@pytest.fixture()
def client(people_db: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    calls: list[str] = []

    async def _reset(_settings: Any, **_kw: Any) -> dict[str, Any]:
        calls.append("reset")
        return {"reset": True}

    monkeypatch.setattr("openexecutive.cli.fixture_loader.reset_all_state", _reset)
    app = FastAPI()
    app.include_router(fixtures_route.router)
    app.include_router(clients_route.router)
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.calls = calls  # type: ignore[attr-defined]
    return test_client


def _team() -> None:
    people_store.upsert_person(full_name="Pat Owner", is_principal=True, email="pat@example.com")
    people_store.upsert_person(full_name="Sam Teammate", email="sam@example.com")
    people_registry.invalidate()


@pytest.mark.parametrize(("method", "path"), WRITES)
def test_a_teammate_cannot_reset_load_or_switch_the_company(
    client: TestClient, method: str, path: str
) -> None:
    _team()
    response = client.request(method.upper(), path, headers=TEAMMATE, json={})
    assert response.status_code == 403
    assert client.calls == []  # type: ignore[attr-defined]


def test_the_principal_can_reset(client: TestClient) -> None:
    _team()
    response = client.post("/fixtures/reset", headers=OWNER)
    assert response.status_code == 200
    assert client.calls == ["reset"]  # type: ignore[attr-defined]


def test_a_new_install_with_no_principal_can_reset(client: TestClient) -> None:
    response = client.post("/fixtures/reset", headers=TEAMMATE)
    assert response.status_code == 200


def test_reads_stay_open_to_the_team(client: TestClient) -> None:
    _team()
    assert client.get("/fixtures/status", headers=TEAMMATE).status_code != 403
    assert client.get("/clients", headers=TEAMMATE).status_code != 403
