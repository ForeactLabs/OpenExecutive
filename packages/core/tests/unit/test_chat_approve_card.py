"""Approve on a card under a reply in the chat goes through the chat itself:
the card is approved as its own button does (decisions.approve_decision) and
the turn reads what happened, so the Executive carries on with the ask
instead of stopping at the card (decisions.approve_in_chat)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openexecutive.api.routes import chat as chat_route
from openexecutive.api.routes import decisions
from openexecutive.memory import episodic, session_store
from openexecutive.memory.company_profile import CompanyProfile
from openexecutive.people import store as people_store


@pytest.fixture(autouse=True)
def _reset_route_state() -> None:
    chat_route._sessions.clear()


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    db_path = Path("./episodic_memory.db").resolve()
    monkeypatch.setattr(episodic, "DB_PATH", db_path)
    monkeypatch.setattr(session_store, "DB_PATH", db_path)
    monkeypatch.setattr(people_store, "DB_PATH", tmp_path / "people.db")
    episodic.initialize_db(db_path)
    people_store.initialize_db()
    return db_path


@pytest.fixture()
def patched_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from openexecutive.knowledge import retriever as retriever_mod
    from openexecutive.memory import honcho_client as honcho_mod
    from openexecutive.onboarding import profile_builder
    from openexecutive.utils import session_title

    monkeypatch.setattr(profile_builder, "load_or_create_profile", lambda: CompanyProfile())
    monkeypatch.setattr(retriever_mod, "retrieve", lambda **_: "")

    async def fake_prefetch(*_a: Any, **_kw: Any) -> str:
        return ""

    async def fake_title(*_a: Any, **_kw: Any) -> str:
        return "A chat"

    monkeypatch.setattr(honcho_mod, "prefetch", fake_prefetch)
    monkeypatch.setattr(session_title, "generate_session_title", fake_title)


def _capture(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    from openexecutive.orchestrator import executive as exec_mod

    class _CapturingExecutive:
        _THINKING = exec_mod.Executive._THINKING

        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def stream_chat(self, **kwargs: Any) -> AsyncIterator[str]:
            captured["stream_chat"] = kwargs
            yield "ok"

    monkeypatch.setattr(exec_mod, "Executive", _CapturingExecutive)


def _instance(status: str = "approved_unchanged", result: str | None = "Opened the page.") -> Any:
    payload = {"tool": "browser_open_tab", "summary": "Open a page in your browser", "held": True}
    final = {**payload, **({"result": result} if result is not None else {})}
    return SimpleNamespace(
        id=43, status=status, proposed_payload_json=json.dumps(payload), final_payload_json=json.dumps(final),
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_route.router)
    return TestClient(app)


def test_approve_on_a_chat_card_approves_it_and_the_turn_carries_on(
    temp_db: Path, patched_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    people_store.upsert_person(full_name="Alex", is_principal=True)
    captured: dict[str, Any] = {}
    _capture(monkeypatch, captured)
    approved: list[tuple[int, Any]] = []

    async def fake_approve(instance_id: int, body: Any, request: Any) -> Any:
        approved.append((instance_id, body.edits))
        return _instance()

    monkeypatch.setattr(decisions, "approve_decision", fake_approve)
    resp = _client().post(
        "/chat", json={"message": "Approved", "approve_card": 43, "approve_input": {"url": "https://example.com"}},
    )
    assert resp.status_code == 200
    _ = resp.text
    assert approved == [(43, {"input": {"url": "https://example.com"}})]
    block = captured["stream_chat"]["page_context_block"]
    assert "THE CARD THEY JUST APPROVED" in block
    assert '"Open a page in your browser"' in block and "it was done" in block
    assert "Carry on with what they asked" in block
    # What the step gave back is labelled as data, never instructions.
    assert "<untrusted_content" in block and "Opened the page." in block
    assert captured["stream_chat"]["user_message"].startswith("Approved")


def test_a_plain_turn_approves_nothing(
    temp_db: Path, patched_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    people_store.upsert_person(full_name="Alex", is_principal=True)
    captured: dict[str, Any] = {}
    _capture(monkeypatch, captured)

    async def never(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("nothing to approve")

    monkeypatch.setattr(decisions, "approve_decision", never)
    resp = _client().post("/chat", json={"message": "hello"})
    assert resp.status_code == 200
    _ = resp.text
    assert "THE CARD THEY JUST APPROVED" not in captured["stream_chat"]["page_context_block"]


@pytest.mark.asyncio
async def test_a_refused_or_failed_approve_is_said_not_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(*_a: Any, **_kw: Any) -> Any:
        raise HTTPException(status_code=502, detail="It couldn't do that. The page didn't open.")

    monkeypatch.setattr(decisions, "approve_decision", refused)
    note = await decisions.approve_in_chat(43, SimpleNamespace())
    assert "didn't go through" in note and "The page didn't open." in note and "and it was done" not in note

    async def broken(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(decisions, "approve_decision", broken)
    note = await decisions.approve_in_chat(43, SimpleNamespace())
    assert "may not have gone through" in note and "and it was done" not in note


@pytest.mark.asyncio
async def test_a_step_that_gave_nothing_back_still_carries_on(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok(*_a: Any, **_kw: Any) -> Any:
        return _instance(result=None)

    monkeypatch.setattr(decisions, "approve_decision", ok)
    note = await decisions.approve_in_chat(43, SimpleNamespace())
    assert "it was done" in note and "<untrusted_content" not in note


def test_a_page_in_another_app_never_approves_a_card(
    temp_db: Path, patched_deps: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conversation a host origin's page wrote in only reads and advises:
    approve_card there approves nothing, and the turn says the card waits."""
    people_store.upsert_person(full_name="Alex", is_principal=True)
    captured: dict[str, Any] = {}
    _capture(monkeypatch, captured)

    async def never(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a host page must not approve")

    monkeypatch.setattr(decisions, "approve_decision", never)
    monkeypatch.setattr(chat_route, "_host_wrote", lambda _sid: True)
    resp = _client().post("/chat", json={"message": "hi", "session_id": "s-host", "approve_card": 43})
    assert resp.status_code == 200
    _ = resp.text
    block = captured["stream_chat"]["page_context_block"]
    assert decisions.HOST_APPROVE_NOTE in block
