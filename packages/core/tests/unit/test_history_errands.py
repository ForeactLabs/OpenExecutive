"""What chat keeps and what it doesn't (memory/history*.py): an errand handed
to the Executive is not a note, a note that reads two ways is dropped, a note
from the last day is not kept twice, and what someone says back to a call or
text it made for the person is kept as a ``heard`` note of theirs."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openexecutive.delegation import texts as tx
from openexecutive.delegation import texts_watch
from openexecutive.memory import episodic
from openexecutive.memory import history as h
from openexecutive.memory import history_chat as hc
from openexecutive.memory import history_heard as hh
from openexecutive.memory import history_notes as hn
from openexecutive.memory.history import NewNote
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store

WHEN = datetime(2026, 11, 28, 18, 55, tzinfo=UTC)
JR_ERRAND = (
    "Call JR and tell him to have a great time with Reece, and ask whether to expect him home for dinner."
)


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    monkeypatch.setattr(people_store, "DB_PATH", path)
    episodic.initialize_db(path)
    people_store.initialize_db(path)
    for var in ("OE_LOCAL_LOGIN", "OE_PUBLIC_DEPLOYMENT", tx.ENV_OFF, tx.BROWSER_OFF):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    people_registry.invalidate()
    yield path
    people_registry.invalidate()


@pytest.fixture
def roster() -> SimpleNamespace:
    principal = people_store.upsert_person(full_name="Olivia Owner", is_principal=True, email="olivia@co.example")
    contact = people_store.upsert_person(full_name="Carla Contact", email="carla@x.example", kind="contact")
    people_registry.invalidate()
    h.set_person_settings(principal, by="test", reply_notes=True)
    return SimpleNamespace(principal=principal, contact=contact)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Both passes' model call: what it returns, and what it was shown."""
    state: dict[str, Any] = {"notes": [], "turns": [], "systems": []}

    async def fake(model: str, turn: str, *, system: str, actor: str) -> dict[str, Any]:
        state["turns"].append(turn)
        state["systems"].append(system)
        if isinstance(state["notes"], Exception):
            raise state["notes"]
        return {"notes": state["notes"]}

    monkeypatch.setattr(hn, "record", fake)
    monkeypatch.setattr(hc, "_call_model", lambda m, t: fake(m, t, system=hc.CHAT_PROMPT, actor="history_chat"))
    return state


def _chat(person_id: int, text: str, ref: str = "web-1") -> list[int]:
    return asyncio.run(hc.note_chat_message(person_id, text=text, channel="web", conversation_ref=ref, said_at=WHEN))


# --------------------------------------------------------------------------- #
# Errands are not notes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kind", "summary", "quote"),
    [
        # The note Rufus saw: an errand, read as if they had asked JR themselves.
        ("asked", "Asked JR to confirm whether he should expect him home for dinner.",
         "ask whether to expect him home for dinner"),
        ("asked", "Asked JR to call and say to have a good time.", "Call JR and tell him to have a great time"),
        ("asked", "Asked Casey whether the test text arrived.", "Can you text Casey and ask if it arrived"),
        ("asked", "Asked for the company's DUNS and EIN numbers.", "please find the DUNS and EIN numbers"),
        ("shared", "Told the assistant not to run a web search.", "don't run a web search for this"),
        ("agreed", "Said he would tell him by Friday.", "he would tell him by Friday"),
        ("asked", "Said she should expect her home for dinner.", "she should expect her home for dinner"),
        ("shared", "Said she will call her.", "she will call her tonight"),
    ],
)
def test_an_errand_or_a_note_that_reads_two_ways_is_not_kept(kind: str, summary: str, quote: str) -> None:
    assert hc.errand_note(NewNote(kind=kind, summary=summary, quote=quote)) is True


@pytest.mark.parametrize(
    ("kind", "summary", "quote"),
    [
        ("asked", "Asked Dana Lee for the signed lease.", "I asked Dana Lee for the signed lease"),
        ("shared", "Said JR is picking them up in Marietta.", "JR is picking me up in Marietta"),
        ("promised", "Said he will send her the deck on Friday.", "he will send her the deck on Friday"),
        ("promised", "Promised she would send her slides to Dana by Friday.", "she'll send her slides to Dana by Friday"),
        ("shared", "Said she left her keys at the office.", "she left her keys at the office"),
        ("agreed", "Decided the promotion is 20% off.", "the promotion is 20% off"),
    ],
)
def test_what_they_said_themselves_is_kept(kind: str, summary: str, quote: str) -> None:
    assert hc.errand_note(NewNote(kind=kind, summary=summary, quote=quote)) is False


def test_the_jr_errand_leaves_no_note(roster: SimpleNamespace, model: dict[str, Any]) -> None:
    model["notes"] = [{
        "kind": "asked",
        "summary": "Asked JR to confirm whether he should expect him home for dinner.",
        "quote": "ask whether to expect him home for dinner",
    }]
    assert _chat(roster.principal, JR_ERRAND) == []
    assert h.list_notes(roster.principal) == []


def test_the_prompt_says_an_errand_is_not_a_note() -> None:
    prompt = hc.CHAT_PROMPT
    assert "anything they ask the assistant to do, even when it reaches someone else" in prompt
    assert "name each one" in prompt and '"he" or "him"' in prompt
    assert "they themselves asked" in prompt


def test_their_own_words_never_make_a_heard_note(roster: SimpleNamespace, model: dict[str, Any]) -> None:
    model["notes"] = [{"kind": "heard", "summary": "Heard from JR he'll be home at 6.", "quote": "JR said he'll be home at 6"}]
    assert _chat(roster.principal, "Just so you know, JR said he'll be home at 6 tonight.") == []
    assert hn.check_note(
        {"kind": "heard", "summary": "Said JR is home at 6.", "quote": "JR said he'll be home at 6"},
        body="JR said he'll be home at 6", allowed="JR said he'll be home at 6",
    ) is None


# --------------------------------------------------------------------------- #
# The same note twice
# --------------------------------------------------------------------------- #


def test_a_note_from_the_last_day_is_not_kept_twice(roster: SimpleNamespace, model: dict[str, Any]) -> None:
    model["notes"] = [{
        "kind": "shared",
        "summary": "Said they generally order two pizzas: one specialty and one stuffed crust pepperoni.",
        "quote": "we generally order two pizzas, one specialty and one stuffed crust pepperoni",
    }]
    text = "FYI we generally order two pizzas, one specialty and one stuffed crust pepperoni."
    assert len(_chat(roster.principal, text, ref="web-1")) == 1
    # The same words again in another chat, and nearly the same summary.
    assert _chat(roster.principal, text, ref="web-2") == []
    model["notes"] = [{
        "kind": "shared",
        "summary": "Said they usually order two pizzas, one specialty and one stuffed crust pepperoni.",
        "quote": "usually order two pizzas, one specialty and one stuffed crust pepperoni",
    }]
    again = "Reminder: we usually order two pizzas, one specialty and one stuffed crust pepperoni."
    assert _chat(roster.principal, again, ref="web-3") == []
    assert len(h.list_notes(roster.principal)) == 1


def test_a_different_note_or_an_old_one_is_kept(roster: SimpleNamespace) -> None:
    note = NewNote(kind="agreed", summary="Agreed the launch moves to Tuesday.", quote="the launch moves to Tuesday")
    other = NewNote(kind="promised", summary="Said they will send the deck to Dana Lee.", quote="I'll send Dana Lee the deck")

    def add(n: NewNote, now: datetime) -> list[int]:
        return h.add_notes(
            roster.principal, [n], source=h.SOURCE_CHAT_MESSAGE, channel="web", conversation_ref="web-1",
            counterpart="", subject="", trust="high", occurred_at=now, now=now, skip_repeats=True,
        )

    assert add(note, WHEN)
    assert add(other, WHEN)
    assert add(note, WHEN + timedelta(hours=2)) == []
    # A day on, it is news again.
    assert add(note, WHEN + timedelta(days=2))
    # Another due date is another note.
    later = NewNote(kind="agreed", summary=note.summary, quote=note.quote, due_date="2026-12-01")
    assert add(later, WHEN + timedelta(days=2))


@pytest.mark.parametrize(
    ("kind", "first", "then"),
    [
        ("heard", "Heard from JR he will be home for dinner at 6.", "Heard from JR he will be home for dinner at 8."),
        ("heard", "Heard from JR he will be home for dinner.", "Heard from JR he will not be home for dinner."),
        ("heard", "Heard from JR he will be home for dinner.", "Heard from JR he won't be home for dinner."),
        ("agreed", "Agreed the launch moves to Tuesday.", "Agreed the launch moves to Thursday."),
        ("agreed", "Decided the promotion is 20% off.", "Decided the promotion is 30% off."),
        ("promised", "Said they will send the deck to Dana Lee.", "Said they will send the deck to Marcus Lee."),
    ],
)
def test_a_changed_answer_is_not_a_repeat(roster: SimpleNamespace, kind: str, first: str, then: str) -> None:
    def add(summary: str, quote: str) -> list[int]:
        return h.add_notes(
            roster.principal, [NewNote(kind=kind, summary=summary, quote=quote)], source=h.SOURCE_CHAT_MESSAGE,
            channel="web", conversation_ref="web-1", counterpart="", subject="", trust="high",
            occurred_at=WHEN, now=WHEN, skip_repeats=True,
        )

    assert add(first, "the first words they said")
    assert add(then, "the next words they said")
    assert len(h.list_notes(roster.principal)) == 2


def test_the_same_words_from_someone_else_or_another_source_are_kept(roster: SimpleNamespace) -> None:
    def add(kind: str, source: str, who: str, summary: str) -> list[int]:
        return h.add_notes(
            roster.principal, [NewNote(kind=kind, summary=summary, quote="Yes I'll be home for dinner")],
            source=source, channel="texts", conversation_ref=f"conv-{who}", counterpart=who, subject="",
            trust="medium", occurred_at=WHEN, now=WHEN, skip_repeats=True,
        )

    assert add("heard", h.SOURCE_ERRAND_REPLY, "JR", "Heard from JR he'll be home for dinner.")
    assert add("heard", h.SOURCE_ERRAND_REPLY, "Casey", "Heard from Casey she'll be home for dinner.")
    assert add("answered", h.SOURCE_CHAT_MESSAGE, "", "Said they'll be home for dinner.")
    # From JR again: the same answer.
    assert add("heard", h.SOURCE_ERRAND_REPLY, "JR", "Heard from JR he'll be home for dinner.") == []
    assert len(h.list_notes(roster.principal)) == 3


# --------------------------------------------------------------------------- #
# What came back from an errand
# --------------------------------------------------------------------------- #


def _heard(person_id: int, words: str = "Yes! Home by 6, save me some pasta", **kw: Any) -> list[int]:
    args: dict[str, Any] = {"who": "JR", "channel": "texts", "conversation_ref": "conv-1", "said_at": WHEN}
    args.update(kw)
    return asyncio.run(hh.note_heard(person_id, words=words, **args))


def test_the_answer_is_kept_as_a_heard_note(roster: SimpleNamespace, model: dict[str, Any]) -> None:
    model["notes"] = [{"kind": "heard", "summary": "Heard from JR he'll be home by 6.", "quote": "Home by 6, save me"}]
    [nid] = _heard(roster.principal)
    [note] = h.list_notes(roster.principal)
    assert note.id == nid and note.kind == "heard" and note.source == h.SOURCE_ERRAND_REPLY
    assert note.trust == "medium" and note.counterpart == "JR" and note.visibility == "private"
    assert note.conversation_key == h.conversation_key("texts", "conv-1")
    assert h.shown_summary(note.summary) == "You heard from JR he'll be home by 6."
    [turn] = model["turns"]
    assert turn.startswith("<their_reply>\nFrom: JR\nBy: texts") and turn.endswith("</their_reply>")
    assert model["systems"] == [hh.HEARD_PROMPT]


@pytest.mark.parametrize(
    "raw",
    [
        # A figure they never said.
        {"kind": "heard", "summary": "Heard from JR he'll be home by 7.", "quote": "Home by 6, save me"},
        # Words that aren't theirs.
        {"kind": "heard", "summary": "Heard from JR he'll be home by 6.", "quote": "I will be home by six"},
        # A name that isn't in what they said.
        {"kind": "heard", "summary": "Heard from JR that Marcus is home by 6.", "quote": "Home by 6, save me"},
        # Another kind: a reply is never the person's own word.
        {"kind": "promised", "summary": "Said they'd be home by 6.", "quote": "Home by 6, save me"},
    ],
)
def test_a_heard_note_that_fails_a_check_is_dropped(
    roster: SimpleNamespace, model: dict[str, Any], raw: dict[str, Any]
) -> None:
    model["notes"] = [raw]
    assert _heard(roster.principal) == []
    assert h.list_notes(roster.principal) == []


def test_heard_notes_follow_the_persons_switches(roster: SimpleNamespace, model: dict[str, Any]) -> None:
    model["notes"] = [{"kind": "heard", "summary": "Heard from JR he'll be home by 6.", "quote": "Home by 6, save me"}]
    h.set_person_settings(roster.principal, by="test", reply_notes=False)
    assert _heard(roster.principal) == []
    h.set_person_settings(roster.principal, by="test", reply_notes=True)
    h.forget_conversation(roster.principal, h.conversation_key("texts", "conv-1"))
    assert _heard(roster.principal) == []
    # A contact keeps no notes.
    assert _heard(roster.contact) == []
    assert model["turns"] == []
    model["notes"] = RuntimeError("down")
    assert _heard(roster.principal, conversation_ref="conv-2") == []


# --------------------------------------------------------------------------- #
# A text that answers one a chat asked for
# --------------------------------------------------------------------------- #


def _send(
    person_id: int, conversation_id: str, *, asked_in: str | None, status: str, at: datetime,
    queued: timedelta | None = None,
) -> None:
    send = tx.queue_send(person_id, "Home for dinner?", conversation_id=conversation_id, asked_in=asked_in)
    with tx._connect() as conn:
        conn.execute(
            f"UPDATE {tx.TEXT_SENDS_TABLE} SET status=?, created_at=?, done_at=? WHERE id=?",  # noqa: S608
            (status, tx._iso(at - (queued or timedelta(0))), tx._iso(at), send.id),
        )


def test_errand_sent_is_a_chat_asked_text_from_the_last_day(roster: SimpleNamespace) -> None:
    reply_at = tx._iso(WHEN)
    assert tx.errand_sent("conv-1", reply_at) is False
    # A reply a texts turn or an approved card sent is not an errand.
    _send(roster.principal, "conv-1", asked_in=None, status=tx.SENT, at=WHEN - timedelta(hours=1))
    assert tx.errand_sent("conv-1", reply_at) is False
    # One a chat asked for that never went is not either.
    _send(roster.principal, "conv-1", asked_in="web-1", status=tx.FAILED, at=WHEN - timedelta(hours=1))
    assert tx.errand_sent("conv-1", reply_at) is False
    # Two days ago is too long ago.
    _send(roster.principal, "conv-1", asked_in="web-1", status=tx.SENT, at=WHEN - timedelta(days=2))
    assert tx.errand_sent("conv-1", reply_at) is False
    _send(roster.principal, "conv-1", asked_in="web-1", status=tx.SENT, at=WHEN - timedelta(hours=1))
    assert tx.errand_sent("conv-1", reply_at) is True
    assert tx.errand_sent("conv-2", reply_at) is False
    # Once they answered, a later text in the conversation is just a text.
    with tx._connect() as conn:
        conn.execute(
            f"INSERT INTO {tx.TEXTS_TABLE} (person_id, conversation_id, ref, sender, text, outgoing, at, handled) "  # noqa: S608
            "VALUES (?,?,?,?,?,?,?,?)",
            (roster.principal, "conv-1", "r1", "JR", "Home by 6", 0, reply_at, 1),
        )
    assert tx.errand_sent("conv-1", reply_at) is True
    assert tx.errand_sent("conv-1", tx._iso(WHEN + timedelta(hours=15))) is False


def test_a_text_from_before_the_errand_went_is_no_answer(roster: SimpleNamespace) -> None:
    # Queued at 10:00 with Chrome closed; it went at 10:40.
    went = WHEN
    _send(roster.principal, "conv-9", asked_in="web-1", status=tx.SENT, at=went, queued=timedelta(minutes=40))
    # A text JR sent at 10:20, synced in the same pass, isn't his answer...
    assert tx.errand_sent("conv-9", tx._iso(went - timedelta(minutes=20))) is False
    # ...but one after it went is.
    assert tx.errand_sent("conv-9", tx._iso(went + timedelta(minutes=5))) is True


def test_a_reply_to_an_errand_text_is_noted(roster: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake(person_id: int, **kw: Any) -> list[int]:
        seen.append({"person_id": person_id, **kw})
        return [1]

    monkeypatch.setattr(hh, "note_heard", fake)
    conv = tx.Conversation(id="conv-1", person_id=roster.principal, key="k", title="JR", number="",
                           is_group=False, updated_at="")
    new = [
        tx.Text(id=1, conversation_id="conv-1", sender="JR", text="Yes!", outgoing=False, at=tx._iso(WHEN)),
        tx.Text(id=2, conversation_id="conv-1", sender="JR", text="Home by 6", outgoing=False,
                at=tx._iso(WHEN + timedelta(minutes=1))),
    ]
    asyncio.run(texts_watch._note_answer(roster.principal, conv, new))
    assert seen == []
    _send(roster.principal, "conv-1", asked_in="web-1", status=tx.SENT, at=WHEN - timedelta(minutes=30))
    asyncio.run(texts_watch._note_answer(roster.principal, conv, new))
    [call] = seen
    assert call["person_id"] == roster.principal and call["words"] == "Yes!\nHome by 6"
    assert call["who"] == "JR" and call["channel"] == "texts" and call["conversation_ref"] == "conv-1"
    # A group's texts are never someone's answer.
    group = tx.Conversation(id="conv-1", person_id=roster.principal, key="k", title="Family", number="",
                            is_group=True, updated_at="")
    asyncio.run(texts_watch._note_answer(roster.principal, group, new))
    assert len(seen) == 1


# --------------------------------------------------------------------------- #
# Who someone is to them: a fact of their own
# --------------------------------------------------------------------------- #


def test_remember_fact_keeps_personal_facts_for_them_alone() -> None:
    from openexecutive.memory import chat_review
    from openexecutive.orchestrator.fact_tools import REMEMBER_FACT_TOOL

    text = REMEMBER_FACT_TOOL["description"]
    assert "who someone is to them ('JR is my son')" in text
    assert "never an errand" in text
    assert "personal matters" not in text
    assert "JR is my son" in REMEMBER_FACT_TOOL["input_schema"]["properties"]["about_me"]["description"]
    assert "remember_fact with about_me true" in chat_review._SYSTEM
