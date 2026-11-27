"""A teammate's own morning brief (briefing.teammate_brief) and how the
scheduler sends it: theirs alone, only when something is new for them."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openexecutive.briefing import brief_items, brief_state, narrative_cache, teammate_brief
from openexecutive.people.models import Person
from openexecutive.scheduler import runner

NOW = datetime.now(UTC)


def _person(pid: int, **kw: Any) -> Person:
    base: dict[str, Any] = {"id": pid, "full_name": f"Person {pid}", "kind": "team"}
    return Person(**{**base, **kw})


def _card(alert_id: int, headline: str) -> dict[str, Any]:
    return {
        "alert_id": alert_id, "headline": headline, "suggested_action": "Approve",
        "created_at": (NOW - timedelta(days=3)).isoformat(), "category": "action",
    }


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Person 5's Home slice, the model and the grounding pass stubbed."""
    monkeypatch.setattr(narrative_cache, "DB_PATH", tmp_path / "cache.db")
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    state: dict[str, Any] = {
        "data": {"departments": [], "people": [], "proposals": [_card(301, "Approve the Lakeside PO")]},
        "contexts": [], "texts": [],
    }
    monkeypatch.setattr(teammate_brief, "_scoped_today", lambda pid: {**state["data"], "_departments": []})
    monkeypatch.setattr(teammate_brief, "_activity", lambda pid, since, depts: [])
    monkeypatch.setattr(brief_state, "handled_since", lambda since, limit=20, **kw: [])
    monkeypatch.setattr("openexecutive.briefing.reader.person_reader", lambda pid: None)
    monkeypatch.setattr("openexecutive.memory.facts.render_facts_for_prompt", lambda **kw: "")
    monkeypatch.setattr("openexecutive.briefing.brief_requests.render_parts", lambda pid, **kw: ("", ""))

    async def _synth(**kw: Any) -> str:
        state["contexts"].append(kw["rendered_context"])
        return state["texts"].pop(0) if state["texts"] else "**Do today**\n1. Approve the Lakeside PO."

    async def _ground(text: str, **kw: Any) -> tuple[str, None]:
        state["ground"] = kw
        return text, None

    monkeypatch.setattr("openexecutive.briefing.narrative.synthesize_briefing_narrative", _synth)
    monkeypatch.setattr("openexecutive.briefing.grounding.ground_brief", _ground)
    monkeypatch.setattr("openexecutive.alerts.card_refs.name_cards_for", lambda text, pid, **kw: text)
    return state


def test_a_teammate_with_nothing_on_their_plate_gets_nothing(board: dict[str, Any]) -> None:
    board["data"]["proposals"] = []
    assert asyncio.run(teammate_brief.write(_person(5), NOW)) is None
    assert board["contexts"] == []


def test_a_teammate_brief_is_theirs_and_not_resent_unchanged(board: dict[str, Any]) -> None:
    first = asyncio.run(teammate_brief.write(_person(5), NOW))
    assert first is not None and "Lakeside PO" in first.text
    assert set(first.items) == {"card:301"}
    assert board["ground"]["private_to_person"] == 5
    assert board["ground"]["surface"] == "teammate brief"
    teammate_brief.record_delivered(5, first)
    assert brief_items.told(teammate_brief.KIND, 5) == first.items
    # Nothing moved: no second message.
    assert asyncio.run(teammate_brief.write(_person(5), NOW + timedelta(days=1))) is None
    # Something new: the old card is already told, the new one is news.
    board["data"]["proposals"].append(_card(302, "Sign the Catoosa lease"))
    again = asyncio.run(teammate_brief.write(_person(5), NOW + timedelta(days=1)))
    assert again is not None
    context = board["contexts"][-1]
    assert "Sign the Catoosa lease" in context.split("STILL OPEN, ALREADY TOLD")[0]
    assert "Approve the Lakeside PO" in context.split("STILL OPEN, ALREADY TOLD")[1]


def test_a_quiet_answer_sends_nothing(board: dict[str, Any]) -> None:
    from openexecutive.briefing.narrative import QUIET_PRINCIPAL

    board["texts"] = [QUIET_PRINCIPAL]
    assert asyncio.run(teammate_brief.write(_person(5), NOW)) is None


def test_who_gets_a_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.people.notifications import NotificationSettings

    off = {9}
    monkeypatch.setattr(
        "openexecutive.people.notifications.get_notification_settings",
        lambda pid: NotificationSettings(morning_brief=pid not in off),
    )
    assert teammate_brief.gets_a_brief(_person(5))
    assert not teammate_brief.gets_a_brief(_person(1, is_principal=True))
    assert not teammate_brief.gets_a_brief(_person(6, kind="contact"))
    assert not teammate_brief.gets_a_brief(_person(7, archived=True))
    assert not teammate_brief.gets_a_brief(_person(9))
    # Email only for someone who has used the Executive or chose it.
    assert teammate_brief.chat_only(_person(5))
    assert not teammate_brief.chat_only(_person(5, last_channel="web"))
    assert not teammate_brief.chat_only(_person(5, preferred_channel="email"))


def test_the_scheduler_sends_each_teammate_their_own(monkeypatch: pytest.MonkeyPatch) -> None:
    people = [_person(1, is_principal=True), _person(5, slack_user_id="U5"), _person(6), _person(7)]
    monkeypatch.setattr("openexecutive.people.store.list_people", lambda: people)
    monkeypatch.setattr(teammate_brief, "gets_a_brief", lambda p: not p.is_principal)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)

    async def _write(person: Person, now: datetime) -> teammate_brief.TeammateBrief | None:
        if person.id == 6:
            return None  # nothing new for them
        if person.id == 7:
            raise RuntimeError("model down")
        return teammate_brief.TeammateBrief(text=f"brief for {person.id}", fingerprint="fp", items={"card:1": "s"})

    sent: list[tuple[int | None, str, bool]] = []

    async def _deliver(person: Person, text: str, **kw: Any) -> runner.PrincipalDelivery:
        sent.append((person.id, text, kw["chat_only"]))
        return runner.PrincipalDelivery(True, "slack_dm → U5", "delivered", "slack_dm")

    recorded: list[int] = []
    monkeypatch.setattr(teammate_brief, "write", _write)
    monkeypatch.setattr(runner, "deliver_to_person", _deliver)
    monkeypatch.setattr(teammate_brief, "record_delivered", lambda pid, brief: recorded.append(pid))
    asyncio.run(runner._send_teammate_briefs(NOW))
    assert sent == [(5, "brief for 5", True)]
    assert recorded == [5]


def test_a_teammate_in_quiet_hours_gets_it_when_they_end(monkeypatch: pytest.MonkeyPatch) -> None:
    people = [_person(5, slack_user_id="U5"), _person(6, slack_user_id="U6", on_leave_until=NOW.date())]
    monkeypatch.setattr("openexecutive.people.store.list_people", lambda: people)
    monkeypatch.setattr(teammate_brief, "gets_a_brief", lambda p: True)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    later = NOW + timedelta(hours=6)
    monkeypatch.setattr(runner, "_teammate_quiet_until", lambda person, now: later)
    written: list[int | None] = []

    async def _write(person: Person, now: datetime) -> teammate_brief.TeammateBrief:
        written.append(person.id)
        return teammate_brief.TeammateBrief(text="brief", fingerprint="fp", items={})

    async def _deliver(*a: Any, **kw: Any) -> runner.PrincipalDelivery:
        raise AssertionError("not sent in their quiet hours")

    held: list[tuple[int, datetime]] = []
    monkeypatch.setattr(
        "openexecutive.people.held_messages.hold",
        lambda pid, text, **kw: held.append((pid, kw["release_at"])) or 1,
    )
    kept: list[int] = []
    monkeypatch.setattr(teammate_brief, "write", _write)
    monkeypatch.setattr(runner, "deliver_to_person", _deliver)
    monkeypatch.setattr(teammate_brief, "record_kept", lambda pid, brief, held_id: kept.append(pid))
    asyncio.run(runner._send_teammate_briefs(NOW))
    assert written == [5]  # the one on leave is not written to
    assert held == [(5, later)] and kept == [5]  # told only once it is sent


def test_nobody_is_written_a_brief_nothing_can_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    # Imported teammates with no chat app who never used the Executive.
    monkeypatch.setattr("openexecutive.people.store.list_people", lambda: [_person(8), _person(9)])
    monkeypatch.setattr(teammate_brief, "gets_a_brief", lambda p: True)

    written: list[int | None] = []

    async def _write(person: Person, now: datetime) -> None:
        written.append(person.id)  # the loop logs a raise, so record instead

    monkeypatch.setattr(teammate_brief, "write", _write)
    asyncio.run(runner._send_teammate_briefs(NOW))
    assert written == []
    # Someone who chose email is written to.
    monkeypatch.setattr(
        "openexecutive.people.store.list_people",
        lambda: [_person(8, preferred_channel="email", email="p8@example.com")],
    )
    asyncio.run(runner._send_teammate_briefs(NOW))
    assert written == [8]


def test_what_was_done_for_them_with_no_department_reaches_them(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from openexecutive.api.routes import today as today_route

    rows = [{"title": "Nudged Dana's vendor", "department": None},
            {"title": "Ops check-in", "department": "ops"},
            {"title": "Sales check-in", "department": "sales"}]
    items = [SimpleNamespace(model_dump=lambda r=r: r) for r in rows]
    monkeypatch.setattr(today_route, "_build_activity", lambda *a, **kw: SimpleNamespace(items=items))
    got = teammate_brief._activity(5, NOW, ["ops"])
    assert [a["title"] for a in got] == ["Nudged Dana's vendor", "Ops check-in"]


def test_a_kept_brief_is_told_only_once_it_was_sent(
    board: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.people import held_messages

    original = held_messages._connect
    monkeypatch.setattr(held_messages, "_connect", lambda db_path=None: original(tmp_path / "held.db"))
    brief = asyncio.run(teammate_brief.write(_person(5), NOW))
    assert brief is not None
    held_id = held_messages.hold(5, brief.text, source=teammate_brief.KIND, release_at=NOW)
    teammate_brief.record_kept(5, brief, held_id)
    # Still waiting: nothing told yet, and no second brief that would
    # repeat it and arrive right behind it.
    assert teammate_brief.settle_kept(5) is True
    assert brief_items.told(teammate_brief.KIND, 5) == {}
    assert asyncio.run(teammate_brief.write(_person(5), NOW)) is None
    written_at = teammate_brief._read_kept(5)[0]["written_at"]
    held_messages.mark_sent([held_id])
    teammate_brief.settle_kept(5)
    assert brief_items.told(teammate_brief.KIND, 5) == brief.items
    # The next window starts when it was written, not when it went out, so
    # what happened while it waited is still news.
    from openexecutive.briefing import brief_state

    last = brief_state.last_delivered(teammate_brief.kind_for(5))
    assert last is not None and last.generated_at == written_at


def test_a_kept_brief_sent_after_a_newer_one_does_not_replace_it(
    board: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from openexecutive.briefing import brief_state
    from openexecutive.people import held_messages

    original = held_messages._connect
    monkeypatch.setattr(held_messages, "_connect", lambda db_path=None: original(tmp_path / "held.db"))
    old = asyncio.run(teammate_brief.write(_person(5), NOW))
    assert old is not None
    first = held_messages.hold(5, old.text, source=teammate_brief.KIND, release_at=NOW)
    teammate_brief.record_kept(5, old, first)
    # A second one kept while the first still waits keeps both.
    second = held_messages.hold(5, old.text, source=teammate_brief.KIND, release_at=NOW)
    teammate_brief.record_kept(5, old, second)
    assert [e["held_id"] for e in teammate_brief._read_kept(5)] == [first, second]
    # The next day's brief went straight out, then the first kept one was sent.
    newer = replace(old, text="Newer brief", fingerprint="newer", items={"newer:item": "s"})
    teammate_brief.record_delivered(5, newer, at="2099-01-01T00:00:00+00:00")
    held_messages.mark_sent([first])
    teammate_brief.settle_kept(5)
    last = brief_state.last_delivered(teammate_brief.kind_for(5))
    assert last is not None and last.narrative_text == "Newer brief"
    assert brief_items.told(teammate_brief.KIND, 5) == {**old.items, "newer:item": "s"}
    # The one still waiting stays kept.
    assert [e["held_id"] for e in teammate_brief._read_kept(5)] == [second]


def test_a_kept_brief_that_was_dropped_tells_nothing(
    board: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.briefing import brief_state
    from openexecutive.people import held_messages

    original = held_messages._connect
    monkeypatch.setattr(held_messages, "_connect", lambda db_path=None: original(tmp_path / "held.db"))
    brief = asyncio.run(teammate_brief.write(_person(5), NOW))
    assert brief is not None
    before = brief_state.last_delivered(teammate_brief.kind_for(5))
    held_id = held_messages.hold(5, brief.text, source=teammate_brief.KIND, release_at=NOW)
    teammate_brief.record_kept(5, brief, held_id)
    held_messages._drop([held_id], NOW, None)
    assert teammate_brief.settle_kept(5) is False
    assert brief_items.told(teammate_brief.KIND, 5) == {}
    assert teammate_brief._read_kept(5) == []
    assert brief_state.last_delivered(teammate_brief.kind_for(5)) == before
