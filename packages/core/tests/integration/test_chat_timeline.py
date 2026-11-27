"""The chat route sends a turn's "What I did" timeline and saves it with the reply."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.api.routes import chat as chat_route
from openexecutive.memory import episodic, session_store
from openexecutive.memory.company_profile import CompanyProfile


@pytest.fixture(autouse=True)
def _reset_route_state() -> None:
    chat_route._sessions.clear()


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    db_path = Path("./episodic_memory.db").resolve()
    monkeypatch.setattr(episodic, "DB_PATH", db_path)
    monkeypatch.setattr(session_store, "DB_PATH", db_path)
    episodic.initialize_db(db_path)
    return db_path


@pytest.fixture()
def patched_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from openexecutive.knowledge import retriever
    from openexecutive.onboarding import profile_builder
    from openexecutive.utils import session_title

    monkeypatch.setattr(profile_builder, "load_or_create_profile", lambda: CompanyProfile())
    monkeypatch.setattr(retriever, "retrieve", lambda **_k: "")

    async def _no_title(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(session_title, "generate_session_title", _no_title)


def _events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _stub(monkeypatch: pytest.MonkeyPatch, items: list[Any], held: tuple[str, ...] = ()) -> None:
    from openexecutive.orchestrator import executive as exec_mod
    from openexecutive.orchestrator import outside_content

    class _Exec:
        _THINKING = exec_mod.Executive._THINKING

        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def stream_chat(self, **_kwargs: Any) -> AsyncIterator[Any]:
            for item in items:
                yield item

    monkeypatch.setattr(exec_mod, "Executive", _Exec)
    monkeypatch.setattr(outside_content, "pending_held", lambda _s: held)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_route.router)
    return TestClient(app)


def test_each_step_sends_the_timeline_and_the_reply_keeps_it(
    temp_db: Path, patched_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(
        monkeypatch,
        [
            # The executive's order: show_progress, then that round's activity.
            {"type": "progress", "task": "Checking the budget", "now": "Reading Dana's emails"},
            {"type": "activity", "label": "Searching your email…", "tool": "search_my_email", "reads": {"email": 2}},
            {"type": "progress", "now": "Writing to Dana", "found": ["Budget has room"]},
            {"type": "activity", "label": "Messaging Dana", "tool": "message_person"},
            {"type": "action_taken", "tool": "message_person", "summary": "Waiting for your approval", "target": "Dana", "decision_id": 9},
            "Yes, there is room.",
        ],
        held=("Forward the contract",),
    )
    resp = _client().post("/chat", json={"message": "can we afford it?"})
    assert resp.status_code == 200
    events = _events(resp.text)
    kinds = [e["type"] for e in events]

    # A timeline follows each event that changes it, while the turn runs.
    live = [e for e in events if e["type"] == "timeline" and not e["done"]]
    assert len(live) == 5
    assert kinds.index("timeline") == kinds.index("progress") + 1
    assert live[-1]["steps"][-1]["state"] == "current"

    # The finished one comes before `done`, with the held step waiting.
    final = [e for e in events if e["type"] == "timeline" and e["done"]]
    assert len(final) == 1
    assert kinds.index("timeline", kinds.index("chunk")) < kinds.index("done")
    steps = final[0]["steps"]
    assert final[0]["task"] == "Checking the budget"
    assert steps[0]["label"] == "Read Dana's emails"
    assert steps[0]["found"] == ["Budget has room"]
    assert steps[1]["state"] == "waits" and steps[1]["card_id"] == 9
    assert steps[2] == {**steps[2], "label": "Forward the contract", "state": "waits", "kind": "do"}
    assert all(s["state"] != "current" for s in steps)

    # Saved with the reply, and read back with it.
    session_id = next(e["session_id"] for e in events if e["type"] == "done")
    reply = session_store.load_messages(session_id, db_path=temp_db)[-1]
    assert reply["role"] == "assistant"
    assert reply["steps"]["steps"] == steps


def test_a_turn_with_no_steps_saves_none(
    temp_db: Path, patched_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, ["hello"])
    resp = _client().post("/chat", json={"message": "hi"})
    events = _events(resp.text)
    assert not [e for e in events if e["type"] == "timeline"]
    session_id = next(e["session_id"] for e in events if e["type"] == "done")
    assert "steps" not in session_store.load_messages(session_id, db_path=temp_db)[-1]


def test_a_step_cut_off_by_a_disconnect_is_saved_as_stopped(
    temp_db: Path, patched_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openexecutive.orchestrator import executive as exec_mod
    from openexecutive.orchestrator import outside_content

    gone = {"now": False}

    class _Exec:
        _THINKING = exec_mod.Executive._THINKING

        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def stream_chat(self, **_kwargs: Any) -> AsyncIterator[Any]:
            yield "Sending it now. "
            gone["now"] = True
            yield {"type": "activity", "label": "Sending the update…", "tool": "send_text"}
            yield "Sent."

    async def _is_disconnected(_self: Any) -> bool:
        return gone["now"]

    monkeypatch.setattr(exec_mod, "Executive", _Exec)
    monkeypatch.setattr(outside_content, "pending_held", lambda _s: ())
    monkeypatch.setattr("starlette.requests.Request.is_disconnected", _is_disconnected)
    _client().post("/chat", json={"message": "tell the team"})

    import sqlite3

    with sqlite3.connect(temp_db) as conn:
        row = conn.execute(
            "SELECT steps FROM chat_messages WHERE role = 'assistant' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    steps = json.loads(row[0])["steps"]
    assert steps[-1]["label"] == "Sending the update"
    assert steps[-1]["state"] == "stopped"
