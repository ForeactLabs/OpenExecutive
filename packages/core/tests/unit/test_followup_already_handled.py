"""A follow-up overtaken by events stays current: when it fires the run sees
what its setter wrote since (scheduler.runner._since_scheduled_block), and
its message says when it looks handled, but it is always sent: the run never
drops what someone asked for (11-25: Avery's own reminder to call Pat was
cancelled as "already handled" and never came). Every chat turn shows the
speaker's pending ones (correction_tools.pending_followups_block) so the
person can cancel one themselves."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openexecutive.audit import logger as audit_logger
from openexecutive.audit.logger import AuditLogger
from openexecutive.memory import episodic
from openexecutive.memory.session_store import create_session, own_messages_since, save_message
from openexecutive.orchestrator import correction_tools as ct
from openexecutive.orchestrator.session import Session
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store
from openexecutive.scheduler import runner


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    monkeypatch.setattr(people_store, "DB_PATH", path)
    episodic.initialize_db(path)
    people_store.initialize_db(path)
    for var in ("OE_LOCAL_LOGIN", "OE_PUBLIC_DEPLOYMENT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(audit_logger, "_default_logger", AuditLogger(db_path=path))
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    people_registry.invalidate()
    yield path
    people_registry.invalidate()


@pytest.fixture
def roster() -> SimpleNamespace:
    principal = people_store.upsert_person(
        full_name="Olivia Owner", is_principal=True, email="olivia@co.example", telegram_chat_id="111",
    )
    teammate = people_store.upsert_person(full_name="Ben Teammate", slack_user_id="UBEN")
    people_registry.invalidate()
    return SimpleNamespace(principal=people_store.get_person(principal),
                           teammate=people_store.get_person(teammate))


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def _message(db: Path, session_id: str, owner: int | None, role: str, text: str,
             at: timedelta, sender: int | None = None) -> None:
    create_session(session_id, "t", _iso(-timedelta(days=3)), caller_person_id=owner, db_path=db)
    save_message(session_id, role, text, db_path=db, sender_person_id=sender)
    with episodic._get_conn(db) as conn:
        conn.execute(
            "UPDATE chat_messages SET created_at = ? WHERE id = (SELECT MAX(id) FROM chat_messages)",
            (_iso(at),),
        )


def _followup(session_id: str | None, *, ref: str = "111", created_by: int | None = None) -> int:
    action_id = episodic.insert_scheduled_action(
        run_at=_iso(timedelta(days=1)), channel="telegram", channel_ref=ref,
        intent_text="Check whether Dana replied about the lease; if so, compare the terms.",
        originating_session_id=session_id, created_by_person_id=created_by,
    )
    with episodic._get_conn(db_path=episodic.DB_PATH) as conn:
        conn.execute("UPDATE scheduled_actions SET created_at = ? WHERE id = ?",
                     (_iso(-timedelta(days=1)), action_id))
    return action_id


def test_own_messages_since_keeps_only_their_words_after_the_time(
    db: Path, roster: SimpleNamespace
) -> None:
    me, ben = roster.principal.id, roster.teammate.id
    _message(db, "web-1", me, "user", "too early", -timedelta(days=2), sender=me)
    _message(db, "web-1", me, "user", "Dana   replied,\nlet's compare", -timedelta(hours=3), sender=me)
    _message(db, "web-1", me, "assistant", "Here is the comparison", -timedelta(hours=3))
    _message(db, "shared", ben, "user", "Ben's words", -timedelta(hours=2), sender=ben)
    _message(db, "old", me, "user", "before senders", -timedelta(hours=1))
    rows = own_messages_since(me, _iso(-timedelta(days=1)), db_path=db)
    assert [r["text"] for r in rows] == ["Dana replied, let's compare", "before senders"]
    assert own_messages_since(me, _iso(-timedelta(days=1)), limit=1, db_path=db)[0]["text"] == "before senders"
    assert own_messages_since(me, _iso(-timedelta(days=1)), max_chars=6, db_path=db)[0]["text"] == "Dana…"


def _fire(action_id: int, reply: object) -> tuple[AsyncMock, str]:
    [action] = [a for a in episodic.claim_due_actions(datetime.now(UTC) + timedelta(days=2))
                if a.id == action_id]
    with patch("openexecutive.orchestrator.executive.Executive.chat",
               new_callable=AsyncMock, return_value=reply) as chat, patch(
        "openexecutive.onboarding.profile_builder.load_or_create_profile",
        return_value=MagicMock(is_empty=lambda: True),
    ), patch("openexecutive.knowledge.retriever.retrieve", return_value=""), patch(
        "openexecutive.memory.episodic.format_for_prompt", return_value="",
    ), patch.object(runner, "_setter_decision", new_callable=AsyncMock, return_value=("", None)):
        asyncio.run(runner._execute_action(action, gateway=None))
    return chat, str(chat.call_args.kwargs["user_message"])


def test_a_followup_that_looks_handled_is_still_sent(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    _message(db, "web-2", me, "user", "Dana replied with new terms, compare them", -timedelta(hours=2), sender=me)
    # Even a reply that starts like the old stand-down marker is a sent run.
    _, prompt = _fire(action_id, "ALREADY_HANDLED: looks like you compared Dana's terms. Sent a note saying so.")
    assert "<since_scheduled>" in prompt and "Dana replied with new terms" in prompt
    assert "still send the one message" in prompt and "ALREADY_HANDLED" not in prompt
    assert "What they said last wins" in prompt and "already settled" in prompt
    assert "PAUSED" not in prompt and "DROPPED" not in prompt  # no marker to write
    row = episodic.get_scheduled_action(action_id)
    assert row is not None and row.status == "done"


def test_a_followup_still_due_runs_as_before(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1")  # owner from the conversation
    _message(db, "web-2", me, "user", "What's on my calendar?", -timedelta(hours=2), sender=me)
    _, prompt = _fire(action_id, "Sent.")
    assert "What's on my calendar?" in prompt
    assert episodic.get_scheduled_action(action_id).status == "done"  # type: ignore[union-attr]


def test_no_chat_since_shows_nothing(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    with patch.object(runner, "deliver_to_person", new_callable=AsyncMock) as tell:
        _, prompt = _fire(action_id, "Sent.")
    assert "<since_scheduled>" not in prompt and "send nothing to anyone else" not in prompt
    tell.assert_not_called()


def test_the_turn_shows_the_speakers_pending_followups(db: Path, roster: SimpleNamespace) -> None:
    mine = _followup("s-1")
    _followup("someone-else")
    session = Session(session_id="s-1", from_web_chat=True, caller_person_id=roster.principal.id,
                      web_caller_signed_in=True)
    block = ct.pending_followups_block(session)
    assert f"#{mine}," in block and "cancel_followup" in block
    assert block.count("\n- #") == 1
    session.unattended = True
    assert ct.pending_followups_block(session) == ""


def test_a_teammates_followup_that_looks_handled_is_still_sent(db: Path, roster: SimpleNamespace) -> None:
    ben = roster.teammate.id
    action_id = episodic.insert_scheduled_action(
        run_at=_iso(timedelta(days=1)), channel="slack_dm", channel_ref="UBEN",
        intent_text="Check whether the vendor answered.", originating_session_id="ben-1",
        created_by_person_id=ben,
    )
    with episodic._get_conn(db) as conn:
        conn.execute("UPDATE scheduled_actions SET created_at = ? WHERE id = ?",
                     (_iso(-timedelta(days=1)), action_id))
    _message(db, "ben-2", ben, "user", "The vendor answered, all sorted", -timedelta(hours=1), sender=ben)
    with patch.object(runner, "_send_on_plan", new_callable=AsyncMock) as send:
        send.return_value = SimpleNamespace(ok=True, detail="")
        _, prompt = _fire(action_id, "Looks sorted: the vendor answered.")
    send.assert_called_once()
    assert send.call_args.args[2] == "Looks sorted: the vendor answered."
    assert "The vendor answered, all sorted" in prompt
    assert episodic.get_scheduled_action(action_id).status == "done"  # type: ignore[union-attr]


def test_a_teammates_note_to_someone_else_with_no_chat_since_sends_no_notice(
    db: Path, roster: SimpleNamespace
) -> None:
    ben = roster.teammate.id
    action_id = episodic.insert_scheduled_action(
        run_at=_iso(timedelta(days=1)), channel="slack_dm", channel_ref="UVENDOR",
        intent_text="Email the vendor to confirm the order.", originating_session_id="ben-1",
        created_by_person_id=ben,
    )
    with patch.object(runner, "deliver_to_person", new_callable=AsyncMock) as tell:
        _, prompt = _fire(action_id, "Sent the vendor the confirmation.")
    assert "send nothing to anyone else" not in prompt
    tell.assert_not_called()


def _status_and_next(action_id: int) -> tuple[str, int]:
    with episodic._get_conn(episodic.DB_PATH) as conn:
        status = conn.execute("SELECT status FROM scheduled_actions WHERE id = ?", (action_id,)).fetchone()[0]
        later = conn.execute("SELECT COUNT(*) FROM scheduled_actions WHERE id > ? AND status = 'pending'",
                             (action_id,)).fetchone()[0]
    return status, later


def _repeating(action_id: int) -> None:
    with episodic._get_conn(episodic.DB_PATH) as conn:
        conn.execute("UPDATE scheduled_actions SET repeat = 'daily@09:00' WHERE id = ?", (action_id,))


def _fire_decided(
    action_id: int, decided: str, reply: object = "Sent.", when: datetime | None = None
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Fire a follow-up with the setter's since-chat read as ``decided``:
    (the run's chat, the decision call, the line to the setter)."""
    [action] = [a for a in episodic.claim_due_actions(datetime.now(UTC) + timedelta(days=2))
                if a.id == action_id]
    with patch("openexecutive.orchestrator.executive.Executive.chat",
               new_callable=AsyncMock, return_value=reply) as chat, patch(
        "openexecutive.onboarding.profile_builder.load_or_create_profile",
        return_value=MagicMock(is_empty=lambda: True),
    ), patch("openexecutive.knowledge.retriever.retrieve", return_value=""), patch(
        "openexecutive.memory.episodic.format_for_prompt", return_value="",
    ), patch.object(runner, "_setter_decision", new_callable=AsyncMock,
                         return_value=(decided, when)) as decide, \
            patch.object(runner, "deliver_to_person", new_callable=AsyncMock) as tell:
        tell.return_value = SimpleNamespace(ok=True, detail="")
        asyncio.run(runner._execute_action(action, gateway=None))
    return chat, decide, tell


def _to_vendor(db: Path, ben: int) -> int:
    action_id = episodic.insert_scheduled_action(
        run_at=_iso(timedelta(days=1)), channel="slack_dm", channel_ref="UVENDOR",
        intent_text="Message the vendor to confirm the order.", originating_session_id="ben-1",
        created_by_person_id=ben,
    )
    with episodic._get_conn(db) as conn:
        conn.execute("UPDATE scheduled_actions SET created_at = ? WHERE id = ?",
                     (_iso(-timedelta(days=1)), action_id))
    return action_id


@pytest.mark.parametrize("decided", ["paused", "dropped"])
def test_a_reminder_back_to_them_they_asked_to_hold_never_runs(
    db: Path, roster: SimpleNamespace, decided: str
) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    _repeating(action_id)
    _message(db, "web-2", me, "user", "Hold the Dana reminder until the board call", -timedelta(hours=2), sender=me)
    chat, decide, tell = _fire_decided(action_id, decided)
    chat.assert_not_called()  # settled before any run: no reply wording to misread
    decide.assert_called_once()
    assert tell.call_args.args[0].id == me
    assert tell.call_args.args[1].startswith("I held" if decided == "paused" else "I dropped")
    assert "It still repeats" in tell.call_args.args[1]
    row = episodic.get_scheduled_action(action_id)
    assert row is not None and row.status == "cancelled" and f"{decided} as Olivia Owner asked" in row.last_error
    assert _status_and_next(action_id) == ("cancelled", 1)  # only this time: the daily one goes on


def test_a_teammates_held_reminder_to_themself_gets_the_line(db: Path, roster: SimpleNamespace) -> None:
    ben = roster.teammate.id
    action_id = episodic.insert_scheduled_action(
        run_at=_iso(timedelta(days=1)), channel="slack_dm", channel_ref="UBEN",
        intent_text="Remind me to call the vendor.", originating_session_id="ben-1",
        created_by_person_id=ben,
    )
    with episodic._get_conn(db) as conn:
        conn.execute("UPDATE scheduled_actions SET created_at = ? WHERE id = ?",
                     (_iso(-timedelta(days=1)), action_id))
    _message(db, "ben-2", ben, "user", "Hold the vendor call reminder", -timedelta(hours=1), sender=ben)
    chat, _, tell = _fire_decided(action_id, "paused")
    chat.assert_not_called()
    assert tell.call_args.args[0].id == ben
    assert tell.call_args.args[1] == ("I held this follow-up as you asked: Remind me to call the vendor. "
                                      "Ask me for it again any time.")
    assert episodic.get_scheduled_action(action_id).status == "cancelled"  # type: ignore[union-attr]


@pytest.mark.parametrize("decided", ["paused", "dropped"])
def test_a_note_to_someone_else_they_asked_to_hold_never_runs(
    db: Path, roster: SimpleNamespace, decided: str
) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", ref="999", created_by=me)
    _message(db, "web-2", me, "user", "Hold the note to Dana, terms changed", -timedelta(hours=2), sender=me)
    chat, decide, tell = _fire_decided(action_id, decided)
    chat.assert_not_called()  # nothing is written to Dana, and their chat reaches no run
    decide.assert_called_once()
    tell.assert_called_once()
    assert tell.call_args.args[0].id == me
    line = tell.call_args.args[1]
    assert line.startswith("I held" if decided == "paused" else "I dropped")
    assert "Dana replied about the lease" in line and "Hold the note" not in line
    row = episodic.get_scheduled_action(action_id)
    assert row is not None and row.status == "cancelled" and f"{decided} as Olivia Owner asked" in row.last_error


def test_a_note_to_someone_else_after_unrelated_chat_just_goes(
    db: Path, roster: SimpleNamespace
) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", ref="999", created_by=me)
    _message(db, "web-2", me, "user", "We're letting Dana go, keep it quiet", -timedelta(hours=2), sender=me)
    chat, _, tell = _fire_decided(action_id, "")
    prompt = str(chat.call_args.kwargs["user_message"])
    # The run writing to someone else never sees the setter's chat.
    assert "letting Dana go" not in prompt and "<since_scheduled>" not in prompt
    tell.assert_not_called()
    assert episodic.get_scheduled_action(action_id).status == "done"  # type: ignore[union-attr]


def test_a_teammates_note_to_someone_else_they_asked_to_hold_never_runs(
    db: Path, roster: SimpleNamespace
) -> None:
    ben = roster.teammate.id
    action_id = _to_vendor(db, ben)
    _message(db, "ben-2", ben, "user", "Hold the vendor message for now", -timedelta(hours=1), sender=ben)
    chat, _, tell = _fire_decided(action_id, "paused")
    chat.assert_not_called()
    assert tell.call_args.args[0].id == ben and "confirm the order" in tell.call_args.args[1]
    assert episodic.get_scheduled_action(action_id).status == "cancelled"  # type: ignore[union-attr]


def test_a_teammates_note_to_someone_else_never_sees_their_chat(
    db: Path, roster: SimpleNamespace
) -> None:
    ben = roster.teammate.id
    action_id = _to_vendor(db, ben)
    _message(db, "ben-2", ben, "user", "The vendor is on thin ice with us", -timedelta(hours=1), sender=ben)
    chat, _, tell = _fire_decided(action_id, "")
    assert "thin ice" not in str(chat.call_args.kwargs["user_message"])
    tell.assert_not_called()


def test_a_hold_is_kept_even_when_the_line_cant_reach_them(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", ref="999", created_by=me)
    [action] = [a for a in episodic.claim_due_actions(datetime.now(UTC) + timedelta(days=2))
                if a.id == action_id]
    with patch.object(runner, "_setter_decision", new_callable=AsyncMock, return_value=("dropped", None)), \
            patch.object(runner, "deliver_to_person", new_callable=AsyncMock, side_effect=RuntimeError("down")):
        asyncio.run(runner._execute_action(action, gateway=None))
    # Never asked again: a second reading could send what they asked to hold.
    assert episodic.get_scheduled_action(action_id).status == "cancelled"  # type: ignore[union-attr]


def _decision_response(decision: str, **more: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="record_followup_decision",
                                                    input={"decision": decision, **more})])


def test_a_hold_until_a_time_moves_it_and_never_loses_it(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    _message(db, "web-2", me, "user", "Hold that reminder until Friday", -timedelta(hours=2), sender=me)
    action = episodic.get_scheduled_action(action_id)
    assert action is not None
    friday = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    provider = MagicMock()
    provider.messages_create = AsyncMock(
        return_value=_decision_response("moved", run_at=friday.isoformat()))
    with patch("openexecutive.providers.get_provider", return_value=provider), \
            patch("openexecutive.audit.usage.log_model_usage"):
        assert asyncio.run(runner._setter_decision(action, roster.principal)) == ("moved", friday)
        # A time that has passed, or none, is no move: it goes as set.
        provider.messages_create = AsyncMock(
            return_value=_decision_response("moved", run_at=_iso(-timedelta(hours=1))))
        assert asyncio.run(runner._setter_decision(action, roster.principal)) == ("", None)
    chat, _, tell = _fire_decided(action_id, "moved", when=friday)
    chat.assert_not_called()
    assert tell.call_args.args[1].startswith("I moved this follow-up to ")
    row = episodic.get_scheduled_action(action_id)
    assert row is not None and row.status == "pending" and row.run_at == friday.isoformat()


def test_the_message_that_set_it_is_not_said_since(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    action = episodic.get_scheduled_action(action_id)
    assert action is not None
    # Saved when its turn ended, just after the row: the plan, not a later word.
    _message(db, "tg-1", me, "user", "Remind me Thursday to chase Bob. If he answers first, hold it.",
             -timedelta(days=1) + timedelta(seconds=5), sender=me)
    assert runner._since_lines(action, roster.principal) == ""
    _message(db, "tg-1", me, "user", "Bob answered, hold it", -timedelta(hours=1), sender=me)
    lines = runner._since_lines(action, roster.principal)
    assert "Bob answered, hold it" in lines and "Remind me Thursday" not in lines


@pytest.mark.parametrize(("decision", "expected"), [("paused", "paused"), ("dropped", "dropped"), ("send", "")])
def test_the_setters_words_are_read_apart_from_the_run(
    db: Path, roster: SimpleNamespace, decision: str, expected: str
) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", ref="999", created_by=me)
    action = episodic.get_scheduled_action(action_id)
    assert action is not None
    provider = MagicMock()
    provider.messages_create = AsyncMock(return_value=_decision_response(decision))
    with patch("openexecutive.providers.get_provider", return_value=provider), \
            patch("openexecutive.audit.usage.log_model_usage"):
        # Nothing said since: no call at all.
        assert asyncio.run(runner._setter_decision(action, roster.principal)) == ("", None)
        provider.messages_create.assert_not_called()
        _message(db, "web-2", me, "user", "Hold the note to Dana", -timedelta(hours=2), sender=me)
        assert asyncio.run(runner._setter_decision(action, roster.principal)) == (expected, None)
    kwargs = provider.messages_create.call_args.kwargs
    assert "tools" in kwargs and kwargs["tools"][0]["name"] == "record_followup_decision"
    assert "Hold the note to Dana" in kwargs["messages"][0]["content"]


def test_a_failed_read_sends_it_as_set(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", ref="999", created_by=me)
    _message(db, "web-2", me, "user", "Hold the note to Dana", -timedelta(hours=2), sender=me)
    action = episodic.get_scheduled_action(action_id)
    assert action is not None
    with patch("openexecutive.providers.get_provider", side_effect=RuntimeError("no model")):
        assert asyncio.run(runner._setter_decision(action, roster.principal)) == ("", None)


def test_a_drop_read_apart_never_ends_a_repeating_series(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", ref="999", created_by=me)
    _repeating(action_id)
    _message(db, "web-2", me, "user", "Cancel the report review", -timedelta(hours=2), sender=me)
    chat, _, tell = _fire_decided(action_id, "dropped")
    chat.assert_not_called()
    assert _status_and_next(action_id) == ("cancelled", 1)  # this time only
    assert "It still repeats" in tell.call_args.args[1]


_PLAIN_REPLIES = [
    "Dropped you a note on Slack about the Acme renewal.",
    "Your Meta ads are currently PAUSED.",
    "Heads up: Bob's ticket was DROPPED.",
    "DROPPED: looks done to me",
]


@pytest.mark.parametrize("reply", _PLAIN_REPLIES)
def test_a_teammates_reply_is_sent_as_written_whatever_its_words(
    db: Path, roster: SimpleNamespace, reply: str
) -> None:
    ben = roster.teammate.id
    action_id = episodic.insert_scheduled_action(
        run_at=_iso(timedelta(days=1)), channel="slack_dm", channel_ref="UBEN",
        intent_text="Remind me about the Acme renewal.", originating_session_id="ben-1",
        created_by_person_id=ben,
    )
    with episodic._get_conn(db) as conn:
        conn.execute("UPDATE scheduled_actions SET created_at = ? WHERE id = ?",
                     (_iso(-timedelta(days=1)), action_id))
    _message(db, "ben-2", ben, "user", "What's on today?", -timedelta(hours=1), sender=ben)
    with patch.object(runner, "_send_on_plan", new_callable=AsyncMock) as send:
        send.return_value = SimpleNamespace(ok=True, detail="")
        _fire(action_id, reply)
    assert send.call_args.args[2] == reply
    assert episodic.get_scheduled_action(action_id).status == "done"  # type: ignore[union-attr]


@pytest.mark.parametrize("reply", _PLAIN_REPLIES)
def test_an_owners_run_is_done_whatever_its_reply_says(
    db: Path, roster: SimpleNamespace, reply: str
) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    _repeating(action_id)
    _message(db, "web-2", me, "user", "What's on today?", -timedelta(hours=2), sender=me)
    with patch.object(runner, "deliver_to_person", new_callable=AsyncMock) as tell:
        _fire(action_id, reply)
    tell.assert_not_called()
    assert _status_and_next(action_id) == ("done", 1)


def test_a_repeats_next_occurrence_reads_the_first_word_after_it(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    first = _followup("tg-1", created_by=me)
    _repeating(first)
    [action] = [a for a in episodic.claim_due_actions(datetime.now(UTC) + timedelta(days=2)) if a.id == first]
    assert action.id is not None
    episodic.mark_action_done(action.id)  # queues tomorrow's, set by no turn
    [nxt] = [a for a in episodic.list_scheduled_actions() if a.id != first and a.status == "pending"]
    assert nxt.read_since is not None
    # The first thing they wrote in that chat since, a minute later, counts.
    _message(db, "tg-1", me, "user", "Skip tomorrow's reminder", timedelta(minutes=1), sender=me)
    assert "Skip tomorrow's reminder" in runner._since_lines(nxt, roster.principal)


def test_a_moved_followup_never_reads_the_same_hold_again(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    _message(db, "web-2", me, "user", "Hold it until tomorrow", -timedelta(hours=2), sender=me)
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    _fire_decided(action_id, "moved", when=tomorrow)
    row = episodic.get_scheduled_action(action_id)
    assert row is not None and row.status == "pending" and row.read_since is not None
    # When it comes due, "until tomorrow" was already acted on: nothing to read.
    assert runner._since_lines(row, roster.principal) == ""


def test_a_followup_moved_in_chat_never_reads_the_ask_that_moved_it(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    moved_at = datetime.now(UTC) - timedelta(hours=3)
    with episodic._get_conn(db) as conn:  # as move_followup leaves it, mid-turn in web-9
        conn.execute("UPDATE scheduled_actions SET read_since = ?, read_since_turn_in = 'web-9' WHERE id = ?",
                     (moved_at.isoformat(), action_id))
    # The moving turn saves its message when it ends, just after.
    _message(db, "web-9", me, "user", "Hold the Bob follow-up until Friday",
             -timedelta(hours=3) + timedelta(seconds=20), sender=me)
    row = episodic.get_scheduled_action(action_id)
    assert row is not None and runner._since_lines(row, roster.principal) == ""
    _message(db, "web-9", me, "user", "Actually drop the Bob one", -timedelta(hours=1), sender=me)
    assert "Actually drop the Bob one" in runner._since_lines(row, roster.principal)


def test_nobody_is_told_of_a_move_that_didnt_happen(db: Path, roster: SimpleNamespace) -> None:
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    friday = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    # The row left this run meanwhile (cancelled, or its claim reset).
    with patch("openexecutive.memory.episodic.reschedule_action", return_value=False):
        chat, _, tell = _fire_decided(action_id, "moved", when=friday)
    chat.assert_not_called()
    tell.assert_not_called()


def test_a_since_line_is_in_company_time_near_midnight(
    db: Path, roster: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6:30 PM in Los Angeles is already the next day in UTC: "tomorrow" in
    that line must read from the 24th there, not the 25th."""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr("openexecutive.memory.workspace_settings.get_user_timezone",
                        lambda: ZoneInfo("America/Los_Angeles"))
    me = roster.principal.id
    create_session("tg-1", "t", _iso(-timedelta(days=2)), caller_person_id=me, db_path=db)
    action_id = _followup("tg-1", created_by=me)
    _message(db, "web-2", me, "user", "Hold it until tomorrow at 9", -timedelta(hours=2), sender=me)
    with episodic._get_conn(db) as conn:
        conn.execute("UPDATE chat_messages SET created_at = '2026-11-25T02:30:00+00:00' "
                     "WHERE content = 'Hold it until tomorrow at 9'")
        conn.execute("UPDATE scheduled_actions SET created_at = '2026-11-24T00:00:00+00:00' WHERE id = ?",
                     (action_id,))
    action = episodic.get_scheduled_action(action_id)
    assert action is not None
    assert runner._since_lines(action, roster.principal) == (
        "- 2026-11-24 18:30 (Tuesday): Hold it until tomorrow at 9")
