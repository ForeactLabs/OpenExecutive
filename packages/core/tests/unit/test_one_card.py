"""One card per topic, whatever wrote the card (alerts.one_card)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from openexecutive import audit
from openexecutive.alerts import one_card
from openexecutive.alerts import store as alerts_store
from openexecutive.alerts.models import PRIVATE_ALERT_TAG
from openexecutive.audit.context import set_turn
from openexecutive.memory import decision_ledger as ledger
from openexecutive.memory import episodic
from openexecutive.orchestrator.schedule_tools import current_session
from openexecutive.orchestrator.session import Session

OWNER = 1
SAM = 2


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    monkeypatch.setattr(alerts_store, "DB_PATH", path)
    monkeypatch.setattr(audit, "log_event", lambda *_a, **_k: None)
    episodic.initialize_db(path)
    alerts_store.initialize_db(path)
    yield path


@pytest.fixture
def mail_turn() -> Iterator[Session]:
    session = Session(session_id="email:t1", origin_channel="email")
    session.email_from = "dana@northpeak.example"
    session.email_thread_id = "thread-1"
    token = current_session.set(session)
    try:
        yield session
    finally:
        current_session.reset(token)


def _note(
    headline: str, *, reader: int | None = OWNER, private: bool = False, source: str = "email",
    raised: bool = True, joins: int | None = None, severity: str = "medium", **kw: Any,
) -> int:
    with one_card.about(raised=raised, joins=joins):
        alert_id = alerts_store.insert_alert(
            source=source, external_id=f"{headline}-{id(kw)}", severity=severity, headline=headline,
            body=kw.pop("body", headline), routed_to_person_id=reader,
            topic_tags=[PRIVATE_ALERT_TAG] if private else [], **kw,
        )
    assert alert_id is not None
    return alert_id


def _approve(tool: str = "google_workspace__send_gmail_message", tool_input: dict[str, Any] | None = None,
             *, reader: int | None = OWNER) -> tuple[int, int]:
    decision_id = ledger.create_decision_instance(
        decision_class="take_the_lead_action", department="executive", originating_session_id=None,
        proposed_payload={"tool": tool, "input": tool_input or {}}, idempotency_key=None,
        gate_mode="propose", approver_person_id=reader, confidence=None,
    )
    thread, step = one_card.held_topic(tool, [tool_input or {}], mcp=True)
    with one_card.about(thread=thread, step=step):
        alert_id = alerts_store.insert_alert(
            source=ledger.DECISION_ALERT_SOURCE, external_id=ledger.decision_alert_external_id(decision_id),
            severity="medium", headline=f"Waiting for your OK: {tool}", body="Send it.",
            topic_tags=[ledger.decision_instance_tag(decision_id)], routed_to_person_id=reader,
        )
    assert alert_id is not None
    return alert_id, decision_id


def _get(alert_id: int) -> Any:
    alert = alerts_store.get_alert(alert_id)
    assert alert is not None
    return alert


def test_a_note_and_an_approve_card_from_one_turn_are_one_card_either_way_round() -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        note = _note("Northpeak wants Thursday")
        card, _ = _approve()
    with set_turn(session_id="s", turn_id="turn-2"):
        card_first, _ = _approve()
        late = _note("Leo asked for the order")
    for joined, into in ((note, card), (late, card_first)):
        assert _get(joined).status == "dismissed" and _get(joined).superseded_by_alert_id == into
        assert _get(into).status == "unread"
        assert "Then: " in _get(into).body


def test_two_notes_from_one_turn_are_one_card_asking_the_question() -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        first = _note("Plan updated")
        second = _note("Pick a slot", needs="choice", question="Which slot?", choices=["Mon", "Tue"])
    kept = _get(first)
    assert _get(second).superseded_by_alert_id == first
    assert kept.question == "Which slot?" and kept.choices == ["Mon", "Tue"] and "Pick a slot" in kept.body


def test_cards_for_different_readers_or_privacy_stay_apart() -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        mine = _note("For the owner")
        theirs = _note("For Sam", reader=SAM)
        private = _note("Private to the owner", private=True)
        team = _note("For the team", reader=None)
    assert all(_get(a).status == "unread" for a in (mine, theirs, private, team))


def test_the_same_person_on_another_matter_keeps_its_own_card(mail_turn: Session) -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        first = _note("Dana asked for Thursday")
    mail_turn.email_thread_id = "thread-2"
    with set_turn(session_id="s", turn_id="turn-2"):
        other = _note("Dana sent a new invoice question")
    assert _get(other).status == "unread"
    # The model said it is the same matter: it joins.
    with set_turn(session_id="s", turn_id="turn-3"):
        same = _note("Dana asked again about Thursday", joins=first)
    assert _get(same).superseded_by_alert_id == first


def test_a_later_mail_on_the_same_thread_from_the_same_person_joins(mail_turn: Session) -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        first = _note("Dana asked for Thursday")
    with set_turn(session_id="s", turn_id="turn-2"):
        bump = _note("Dana is still waiting")
    assert _get(bump).superseded_by_alert_id == first
    # Someone else writing on that thread is their own card.
    mail_turn.email_from = "leo@northpeak.example"
    with set_turn(session_id="s", turn_id="turn-3"):
        leo = _note("Leo chimed in")
    assert _get(leo).status == "unread"


def test_a_newer_reply_on_the_same_thread_replaces_the_one_waiting() -> None:
    reply = {"to": "dana@northpeak.example", "subject": "Re: Thursday", "body": "x", "thread_id": "thread-1"}
    old_card, old = _approve(tool_input=reply)
    thread, step = one_card.held_topic("google_workspace__send_gmail_message", [reply], mcp=True)
    assert one_card.approve_waiting(thread, reader=one_card.reader_for(OWNER, private=False), step=step)
    new_card, new = _approve(tool_input={**reply, "body": "y"})
    assert ledger.get_decision_instance(old).status == ledger.STATUS_CLOSED_EXTERNALLY  # type: ignore[union-attr]
    assert ledger.get_decision_instance(new).status == ledger.STATUS_PROPOSED  # type: ignore[union-attr]
    assert _get(old_card).status == "dismissed" and _get(new_card).status == "unread"
    # A send to other people on that thread is its own yes.
    other_card, other = _approve(tool_input={**reply, "to": "leo@northpeak.example"})
    assert ledger.get_decision_instance(new).status == ledger.STATUS_PROPOSED  # type: ignore[union-attr]
    assert _get(other_card).status == "unread"


def test_two_different_steps_from_one_turn_are_two_approve_cards() -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        mail, _ = _approve(tool_input={"to": "dana@northpeak.example", "subject": "Hi", "body": "x"})
        meeting, _ = _approve("create_calendar_event", {"title": "Sync"})
    assert _get(mail).status == "unread" and _get(meeting).status == "unread"


def test_cards_a_feature_writes_keep_to_themselves() -> None:
    """A passed-on message, a meeting's card or a document has its own
    reader and buttons: it never joins or takes in a card."""
    with set_turn(session_id="s", turn_id="turn-1"):
        message = _note("From Sam: the dock is closed Friday", source="message", raised=False)
        meeting = _note("Notes from the 3pm call", source="meetings", raised=False)
        card, _ = _approve()
        note = _note("Northpeak wants Thursday")
    assert _get(message).status == _get(meeting).status == "unread"
    assert _get(message).body == "From Sam: the dock is closed Friday"
    assert _get(note).superseded_by_alert_id == card


def test_a_card_whose_approval_does_its_own_text_keeps_to_itself() -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        ok = _note("Send the renewal deck to Northpeak?", needs="ok")
        note = _note("Northpeak wants Thursday")
        card, _ = _approve()
    assert _get(ok).status == "unread" and _get(ok).body == "Send the renewal deck to Northpeak?"
    assert _get(note).superseded_by_alert_id == card


def test_another_matter_on_the_same_thread_keeps_its_own_card(mail_turn: Session) -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        first = _note("Dana asked for Thursday")
    with set_turn(session_id="s", turn_id="turn-2"):
        other = _note("Dana asked for a new price list", joins=0)
    assert _get(first).status == _get(other).status == "unread"


def test_a_grave_card_makes_the_card_it_joins_as_grave() -> None:
    with set_turn(session_id="s", turn_id="turn-1"):
        card, _ = _approve()
        _note("These bank details look changed: possible invoice fraud", severity="high")
    kept = _get(card)
    assert kept.severity == "high" and "possible invoice fraud" in kept.body


def test_a_card_from_no_turn_is_left_alone() -> None:
    first = _note("Weekly digest")
    second = _note("Weekly digest again")
    assert _get(first).status == _get(second).status == "unread"


def test_a_card_that_joined_is_never_sent() -> None:
    import asyncio

    from openexecutive.alerts import dispatcher
    from openexecutive.alerts.models import AlertChannel

    with set_turn(session_id="s", turn_id="turn-1"):
        card, _ = _approve()
        joined = _note("Then this")
    attempted, delivered = asyncio.run(dispatcher.dispatch_all(_get(joined), [AlertChannel.WEB, AlertChannel.EMAIL]))
    assert (attempted, delivered) == ([], [])
    assert _get(card).status == "unread"


def test_the_people_a_turn_is_with() -> None:
    assert one_card.person_key(Session(origin_channel="email", email_from="Dana@X.example")) == "mail:dana@x.example"
    assert one_card.person_key(Session(caller_person_id=4)) == "person:4"
    assert one_card.person_key(Session(unattended=True, run_for_person_id=5)) == "person:5"
    assert one_card.person_key(Session(unattended=True)) == ""


def test_open_cards_from_lists_earlier_turns_only() -> None:
    session = Session(caller_person_id=SAM)
    token = current_session.set(session)
    try:
        with set_turn(session_id="s", turn_id="turn-1"):
            earlier = _note("Sam's first ask")
        with set_turn(session_id="s", turn_id="turn-2"):
            _note("Sam's second ask", reader=SAM)
        owners = one_card.reader_for(OWNER, private=False)
        assert [cid for cid, _, _ in one_card.open_cards_from("person:2", turn="turn-2", reader=owners)] == [earlier]
        # Another reader's cards are never listed.
        assert one_card.open_cards_from("person:2", turn="turn-3", reader=one_card.reader_for(SAM, private=True)) == []
    finally:
        current_session.reset(token)
    assert json.dumps(one_card.open_cards_from("", turn="x", reader=owners)) == "[]"
