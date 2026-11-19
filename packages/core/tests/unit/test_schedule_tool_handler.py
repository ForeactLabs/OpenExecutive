"""Unit tests for the schedule_followup tool handler.

Focuses on input validation, anti-spam guards, and timezone handling. The send-
tool handlers are exercised separately (they hit real network APIs in prod).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openexecutive.memory.episodic import initialize_db
from openexecutive.orchestrator.schedule_tools import (
    current_session,
    handle_schedule_followup,
)
from openexecutive.orchestrator.session import Session


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "episodic.db"
    initialize_db(db_path)
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db_path)
    return db_path


@pytest.fixture()
def session_with_telegram_ref() -> Iterator[Session]:
    s = Session()
    token = current_session.set(s)
    yield s
    current_session.reset(token)


def _future_iso(seconds: int = 600) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _call(tool_input: dict) -> dict:
    return json.loads(asyncio.run(handle_schedule_followup(tool_input)))


def test_happy_path_returns_scheduled_id(session_with_telegram_ref: Session) -> None:
    payload = _call(
        {
            "run_at": _future_iso(),
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "Remind user about the board prep.",
        }
    )
    assert payload["status"] == "scheduled"
    assert isinstance(payload["id"], int)
    assert payload["channel"] == "telegram"


def test_rejects_past_run_at(session_with_telegram_ref: Session) -> None:
    payload = _call(
        {
            "run_at": (datetime.now(UTC) - timedelta(seconds=60)).isoformat(),
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "x",
        }
    )
    assert "must be in the future" in payload["error"]


def test_rejects_unparseable_run_at(session_with_telegram_ref: Session) -> None:
    payload = _call(
        {
            "run_at": "next tuesday-ish",
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "x",
        }
    )
    assert "company's time zone" in payload["error"]


def test_rejects_unknown_channel(session_with_telegram_ref: Session) -> None:
    payload = _call(
        {
            "run_at": _future_iso(),
            "channel": "carrier_pigeon",
            "channel_ref": "42",
            "intent": "x",
        }
    )
    assert "unknown channel" in payload["error"]


def test_any_address_is_scheduled_its_send_meets_the_gate_when_it_fires(
    session_with_telegram_ref: Session,
) -> None:
    payload = _call(
        {
            "run_at": _future_iso(),
            "channel": "telegram",
            "channel_ref": "99",  # never came up in this chat
            "intent": "x",
        }
    )
    assert payload["status"] == "scheduled"


def test_rejects_beyond_horizon(session_with_telegram_ref: Session) -> None:
    far = (datetime.now(UTC) + timedelta(days=400)).isoformat()
    payload = _call(
        {
            "run_at": far,
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "x",
        }
    )
    assert "days out" in payload["error"]


def test_accepts_z_suffix_utc(session_with_telegram_ref: Session) -> None:
    iso_z = (datetime.now(UTC) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = _call(
        {
            "run_at": iso_z,
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "x",
        }
    )
    assert payload["status"] == "scheduled"


def test_rejects_when_pending_cap_exceeded(
    session_with_telegram_ref: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_PENDING_PER_CHANNEL_REF", "1")
    first = _call(
        {
            "run_at": _future_iso(60),
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "first",
        }
    )
    assert first["status"] == "scheduled"
    second = _call(
        {
            "run_at": _future_iso(120),
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "second",
        }
    )
    assert "too many pending" in second["error"]


def test_a_follow_up_goes_to_the_speakers_own_address_or_anyone_elses(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A chat app that seeds no addresses (Google Chat): "check my inbox at 5
    # and tell me" goes to the speaker's own email.
    from openexecutive.people import registry as people_registry
    from openexecutive.people import store as people_store

    monkeypatch.setattr(people_store, "DB_PATH", isolated_db)
    people_store.initialize_db(isolated_db)
    people_registry.invalidate()
    me = people_store.upsert_person(full_name="Olivia Owner", is_principal=True, email="olivia@co.example")
    s = Session(caller_person_id=me)
    token = current_session.set(s)
    try:
        own = _call({"run_at": _future_iso(), "channel": "email", "channel_ref": "Olivia@co.example", "intent": "x"})
        assert own["status"] == "scheduled"
        other = _call({"run_at": _future_iso(), "channel": "email", "channel_ref": "eve@x.example", "intent": "x"})
        assert other["status"] == "scheduled"
    finally:
        current_session.reset(token)
        people_registry.invalidate()


def test_a_time_with_no_offset_is_the_companys_time(
    session_with_telegram_ref: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Tomorrow at noon" in New York is noon there, not noon UTC."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    monkeypatch.setattr("openexecutive.memory.workspace_settings.get_user_timezone", lambda: zone)
    noon = (datetime.now(zone) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    payload = _call(
        {
            "run_at": noon.replace(tzinfo=None).isoformat(),
            "channel": "telegram",
            "channel_ref": "42",
            "intent": "Follow up on the board prep.",
        }
    )
    assert payload["status"] == "scheduled"
    assert datetime.fromisoformat(payload["run_at"]) == noon
