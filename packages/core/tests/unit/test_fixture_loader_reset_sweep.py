"""A factory reset empties every table in the company DB except the
bookkeeping in ``RESET_KEEPS`` and what the caller keeps, so a table added
later is wiped without anyone listing it."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openexecutive.cli import fixture_loader
from openexecutive.departments import registry as dept_registry
from openexecutive.departments import store as dept_store
from openexecutive.knowledge import review_store as rs_mod
from openexecutive.knowledge.store import ChromaDBStore
from openexecutive.memory import episodic, honcho_client
from openexecutive.memory import workspace_settings as ws
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "company.db"
    for mod in (episodic, dept_store, people_store, rs_mod):
        monkeypatch.setattr(mod, "DB_PATH", path)
    monkeypatch.setattr(ws, "_configured_timezone", lambda: ws.ZoneInfo("UTC"))
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)

    async def _noop(workspace_id: str | None = None) -> None:
        return None

    monkeypatch.setattr(honcho_client, "delete_workspace_and_reset_client", _noop)
    episodic.initialize_db(path)
    dept_store.initialize_db(path)
    people_store.initialize_db(path)
    dept_store.seed_default_departments(db_path=path)
    people_store.upsert_person(full_name="Pat Owner", is_principal=True)
    with sqlite3.connect(str(path)) as conn:
        # A table no reset code has ever heard of.
        conn.execute("CREATE TABLE brand_new_feature (note TEXT)")
        conn.execute("INSERT INTO brand_new_feature VALUES ('old company')")
        conn.execute("INSERT OR IGNORE INTO app_migrations (name, applied_at) VALUES ('kept-marker', 'now')")
    dept_registry.invalidate()
    people_registry.invalidate()
    yield path
    dept_registry.invalidate()
    people_registry.invalidate()


def _settings(tmp_path: Path) -> Any:
    profile_path = tmp_path / "company" / "profile.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("name: Old Co\n")
    return type("S", (), {
        "vector_store_path": tmp_path / "chroma",
        "company_profile_path": profile_path,
        "honcho_workspace_id": "openexec",
    })()


def _reset(tmp_path: Path, keep: tuple[str, ...] = ()) -> dict[str, Any]:
    with patch.object(ChromaDBStore, "delete_company_docs", lambda self: None), \
         patch.object(ChromaDBStore, "delete_documents", lambda self, **kw: None):
        return asyncio.run(fixture_loader.reset_all_state(_settings(tmp_path), keep=keep))


def _rows(db: Path, table: str) -> int:
    with sqlite3.connect(str(db)) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_reset_empties_every_table_but_the_bookkeeping(db: Path, tmp_path: Path) -> None:
    attached = tmp_path / "company" / "conversation_files" / "s1" / "notes.txt"
    attached.parent.mkdir(parents=True)
    attached.write_text("old company")
    result = _reset(tmp_path)
    assert not attached.exists()
    assert _rows(db, "brand_new_feature") == 0
    assert result["cleared"]["brand_new_feature"] == 1
    assert _rows(db, "people") == 0
    assert _rows(db, "departments") == 0
    assert _rows(db, "app_migrations") >= 1


def test_reset_keeps_the_tables_the_caller_names(db: Path, tmp_path: Path) -> None:
    ws.restore_workspace_settings(ws.WorkspaceSettings(mode="solo", timezone="Asia/Seoul"))
    _reset(tmp_path, keep=("people", "workspace_settings"))
    assert _rows(db, "people") == 1
    assert _rows(db, "brand_new_feature") == 0
    assert _rows(db, "departments") == 0
    # Kept as it was, not put back to the defaults.
    assert ws.get_workspace() == ws.WorkspaceSettings(mode="solo", timezone="Asia/Seoul")


def test_keeps_cover_the_one_time_markers() -> None:
    for table in ("app_migrations", "monitoring_migrations", "facts_meta", "review_meta",
                  "dynamic_workflows_meta"):
        assert table in fixture_loader.RESET_KEEPS


def test_reset_empties_the_queue_and_seeds_the_recurring_jobs_again(db: Path, tmp_path: Path) -> None:
    episodic.insert_scheduled_action(
        run_at="2099-01-01T00:00:00+00:00", channel="email", channel_ref="pat@example.com",
        intent_text="Call the bank", kind="reminder",
    )
    # An open loop sits on the internal channel but is the old company's.
    episodic.insert_scheduled_action(
        run_at="2099-01-01T00:00:00+00:00", channel="__internal__", channel_ref="",
        intent_text="Send Northwind the proposal", kind="open_loop",
    )
    _reset(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        kinds = {row[0] for row in conn.execute("SELECT kind FROM scheduled_actions")}
    assert "reminder" not in kinds
    assert "open_loop" not in kinds
    assert "principal_brief_morning" in kinds
