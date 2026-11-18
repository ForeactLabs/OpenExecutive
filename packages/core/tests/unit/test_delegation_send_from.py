"""Who a reply card's email goes from: the person or the Executive
(delegation/send_from.py)."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from openexecutive.delegation import answers, card_drafts, inbox, replies, send_from, training
from openexecutive.delegation.reply_send import SendRefused, send_approved_reply
from openexecutive.integrations.workspace.google import GoogleMail
from openexecutive.integrations.workspace.microsoft import MicrosoftMail

from . import test_delegation_card_drafts as _drafts_tests
from .test_delegation_inbox import DANA, NOW, OWNER, FakeInbox

db = _drafts_tests.db
models = _drafts_tests.models
local_login = _drafts_tests.local_login
owner = _drafts_tests.owner
LOCAL = _drafts_tests.LOCAL
_card = _drafts_tests._card
_fresh = _drafts_tests._fresh


@pytest.fixture
def rewrites(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The model's rewrite as the Executive: what it was given, and a body."""
    seen: list[str] = []

    async def fake(model: str, system: str, turn: str) -> dict[str, Any]:
        seen.append(system)
        if system == send_from.AS_EXECUTIVE_PROMPT:
            return {"body": "Hi Dana,\n\nOlivia asked me to say she'll get back to you shortly."}
        return {"body": "unused"}

    monkeypatch.setattr(answers, "_call_model", fake)
    return seen


@pytest.fixture
def sends(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What the Executive's mailbox was asked to send, through the one path
    an approved step takes."""
    from openexecutive.orchestrator import take_the_lead

    out: list[dict[str, Any]] = []

    async def fake(payload: dict[str, Any], *, by_principal: bool, own_card: bool = False) -> str:
        out.append({**payload, "by_principal": by_principal, "own_card": own_card})
        return json.dumps({"status": "sent"})

    monkeypatch.setattr(take_the_lead, "carry_out", fake)
    return out


def _seen(card: Any) -> dict[str, Any]:
    """What a card showing it as it is now sends back with Send."""
    return {"seen_caught_up_at": card_drafts.version(inbox.card_payload(_fresh(card)))}


def _switch(card: Any, mailbox: FakeInbox, owner: Any, executive: bool, **kw: Any) -> dict[str, Any]:
    return asyncio.run(send_from.switch(
        _fresh(card), caller=LOCAL, resolver=owner.id, executive=executive, gmail=mailbox, **kw,
    ))


def test_switch_to_the_executive_and_back(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str],
) -> None:
    mailbox, card = _card(owner)
    before = inbox.card_payload(_fresh(card))["draft_body"]
    saved = _switch(card, mailbox, owner, True)
    assert saved[send_from.FROM_EXECUTIVE] is True
    assert saved["draft_body"].startswith("Hi Dana,\n\nOlivia asked me")
    assert saved["draft_body"].endswith(send_from.sign_off(owner))
    assert saved[send_from.WORDS_AS_YOU] == before
    assert saved["conversation"][-1]["text"] == send_from.NOTE_TO_EXECUTIVE
    shown = replies.cards(owner)[0]
    assert shown.from_executive is True and shown.executive_name
    # Back to them: their own words, as they were, with no model call.
    calls = len(rewrites)
    back = _switch(card, mailbox, owner, False)
    assert back["draft_body"] == before and back[send_from.FROM_EXECUTIVE] is False
    assert len(rewrites) == calls


def test_only_its_person_switches_it(db: Path, owner: Any, models: dict[str, Any], rewrites: list[str]) -> None:
    mailbox, card = _card(owner)
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_from.switch(_fresh(card), caller=LOCAL, resolver=owner.id + 99, executive=True, gmail=mailbox))
    assert err.value.code == "not_yours"


def test_send_goes_from_the_executive_with_them_on_cc(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
) -> None:
    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)
    # A card shown before the switch looks again first; nothing goes.
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_approved_reply(_fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox))
    assert err.value.code == "confirm" and sends == []
    assert asyncio.run(send_approved_reply(
        _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, now=NOW + timedelta(minutes=30),
        confirm=_seen(card),
    )) == ""
    # Nothing went from their own mailbox.
    assert mailbox.sent == [] and mailbox.specs == []
    assert len(sends) == 1
    step = sends[0]
    assert step["held"] is True and step["mcp"] is True and step["own_card"] is True
    assert step["requested_by_person_id"] == owner.id and step["by_principal"] is True
    args = step["input"]
    assert args["to"] == DANA and args["cc"] == OWNER
    assert args["body"].startswith("Hi Dana,\n\nOlivia asked me")
    # The card closed as sent, once.
    assert _fresh(card).status == "approved_unchanged"
    assert inbox.open_cards(owner.id) == []
    with pytest.raises(SendRefused):
        asyncio.run(send_approved_reply(_fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox))
    assert len(sends) == 1


def test_a_failed_send_leaves_the_card_waiting(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.orchestrator import take_the_lead

    async def refused(payload: dict[str, Any], *, by_principal: bool, own_card: bool = False) -> str:
        return "Error: HttpError 429 rateLimitExceeded"

    monkeypatch.setattr(take_the_lead, "carry_out", refused)
    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_approved_reply(_fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm=_seen(card)))
    assert err.value.code == "send_failed"
    # Plain words, never the app's own error text.
    assert err.value.message == "It wasn't sent. Email was busy, so nothing changed. Try again in a few minutes."
    assert _fresh(card).status == "proposed"


def test_saying_it_on_the_card_switches_the_same_card(
    db: Path, owner: Any, models: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(model: str, system: str, turn: str) -> dict[str, Any]:
        if system == send_from.AS_EXECUTIVE_PROMPT:
            return {"body": "Hi Dana,\n\nOlivia asked me to reply."}
        # The card's change: only who it goes from, the words as they were.
        main = turn.split("<draft>\n", 1)[1].split("\n</draft>", 1)[0]
        return {"body": main, "send_from": "executive", "note": "ok"}

    monkeypatch.setattr(answers, "_call_model", fake)
    mailbox, card = _card(owner)
    saved, changed, _ = asyncio.run(answers.change(
        _fresh(card), caller=LOCAL, resolver=owner.id, text="send it from you and cc me", gmail=mailbox,
    ))
    assert changed == []
    assert saved[send_from.FROM_EXECUTIVE] is True
    assert saved["draft_body"].startswith("Hi Dana,\n\nOlivia asked me to reply.")
    turns = saved["conversation"]
    assert turns[-2] == {"kind": "asked", "text": "send it from you and cc me"}
    assert turns[-1]["text"] == send_from.NOTE_TO_EXECUTIVE
    # Still one card.
    assert len(inbox.open_cards(owner.id)) == 1


@pytest.mark.parametrize("go", [True, False])
def test_send_it_from_you_switches_the_sender_and_waits(
    db: Path, owner: Any, models: dict[str, Any], monkeypatch: pytest.MonkeyPatch, go: bool,
) -> None:
    from openexecutive.alerts import replan

    async def fake(model: str, system: str, turn: str) -> dict[str, Any]:
        if system == send_from.AS_EXECUTIVE_PROMPT:
            return {"body": "Hi Dana,\n\nOlivia asked me to reply."}
        main = turn.split("<draft>\n", 1)[1].split("\n</draft>", 1)[0]
        return {"body": main, "send_now": True, "send_from": "executive"}

    async def judged(system: str, tool: dict[str, Any], turn: str, **kw: Any) -> dict[str, Any]:
        return {"go": go}

    monkeypatch.setattr(answers, "_call_model", fake)
    monkeypatch.setattr(replan, "_call_tool", judged)
    replan._CALLS.clear()
    replan._CALLER_CALLS.clear()
    mailbox, card = _card(owner)
    saved, changed, send_now = asyncio.run(answers.change(
        _fresh(card), caller=LOCAL, resolver=owner.id, text="send it from you", gmail=mailbox,
    ))
    # Who it goes from switches either way; the send waits, since the new
    # wording is theirs to read first.
    assert changed == [] and send_now is False
    assert saved[send_from.FROM_EXECUTIVE] is True
    assert saved["conversation"][-1]["text"] == send_from.NOTE_TO_EXECUTIVE


def test_a_rewrite_on_a_switched_card_keeps_it_from_the_executive(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str],
) -> None:
    mailbox, card = _card(owner)
    theirs = inbox.card_payload(_fresh(card))["draft_body"]
    _switch(card, mailbox, owner, True)
    given: list[str] = []

    async def write(person: Any, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        given.append(payload["draft_body"])
        return "Hi Dana,\n\nFriday works.\n\nOlivia", {}

    saved = asyncio.run(card_drafts.edit(_fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, rewrite=write))
    # Rewritten from their own words, never the Executive's.
    assert given == [theirs]
    # Still from the Executive: their new words kept, the card's written again.
    assert saved[send_from.FROM_EXECUTIVE] is True
    assert saved[send_from.WORDS_AS_YOU] == "Hi Dana,\n\nFriday works.\n\nOlivia"
    assert saved["draft_body"].endswith(send_from.sign_off(owner))
    # Back to them: the new words, as they wrote them.
    back = _switch(card, mailbox, owner, False)
    assert back["draft_body"] == "Hi Dana,\n\nFriday works.\n\nOlivia"
    # A change by hand on the card is the Executive's words, kept as typed.
    _switch(card, mailbox, owner, True)
    kept = asyncio.run(card_drafts.edit(
        _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, text="Hi Dana,\n\nShort and sweet.",
    ))
    assert kept[send_from.FROM_EXECUTIVE] is True and kept["draft_body"] == "Hi Dana,\n\nShort and sweet."
    assert kept[send_from.WORDS_AS_YOU] == "Hi Dana,\n\nFriday works.\n\nOlivia"
    # Switching back puts their own words back and says the change isn't in them.
    again = _switch(card, mailbox, owner, False)
    assert again["draft_body"] == "Hi Dana,\n\nFriday works.\n\nOlivia"
    assert again["conversation"][-1]["text"] == send_from.NOTE_TO_YOU_WITHOUT_CHANGE


def test_a_rewrite_keeps_their_words_in_their_mailbox(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str],
) -> None:
    inbox.set_mailbox_drafts(owner.id, True, updated_by="test")
    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)

    async def write(person: Any, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return "Hi Dana,\n\nFriday works.\n\nOlivia", {}

    saved = asyncio.run(card_drafts.edit(_fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, rewrite=write))
    there = mailbox.drafts[saved["draft_id"]].message.text
    assert "Friday works." in there and "asked me" not in there
    # A change by hand on the card is the Executive's words: their draft
    # keeps their own, and switching back finds the two the same.
    edited = asyncio.run(card_drafts.edit(
        _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, text="Hi Dana,\n\nOlivia asked me: Friday.",
    ))
    assert edited["draft_body"] == "Hi Dana,\n\nOlivia asked me: Friday."
    assert "asked me" not in mailbox.drafts[saved["draft_id"]].message.text
    back = _switch(card, mailbox, owner, False)
    assert back["draft_body"] == mailbox.drafts[saved["draft_id"]].message.text


def test_a_chat_app_card_shows_who_it_goes_from_when_posted() -> None:
    t1, t2, t3, t4 = (f"2026-11-17T1{h}:00:00+00:00" for h in range(4))
    switched = {send_from.FROM_EXECUTIVE: True, send_from.SWITCHED_AT: t2}
    back = {send_from.FROM_EXECUTIVE: False, send_from.SWITCHED_AT: t2, send_from.SWITCHED_BACK_AT: t3}
    # Posted after it switched to the Executive: it showed what goes.
    assert card_drafts.asks_again(switched, {}, moved_on=False, shown_at=t3) == []
    # Posted before: it showed them as the sender.
    assert card_drafts.asks_again(switched, {}, moved_on=False, shown_at=t1) == ["changed_in_chat"]
    # Posted after it switched back: it shows them, as now.
    assert card_drafts.asks_again(back, {}, moved_on=False, shown_at=t4) == []
    # Any switch since it was posted: look again, however it ended up.
    assert card_drafts.asks_again(back, {}, moved_on=False, shown_at=t1) == ["changed_in_chat"]
    assert card_drafts.asks_again(back, {}, moved_on=False, shown_at=t2) == ["changed_in_chat"]
    # Not known when it was posted: look again.
    assert card_drafts.asks_again(back, {}, moved_on=False) == ["changed_in_chat"]


def test_a_chat_app_tap_sends_from_the_executive(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
) -> None:
    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)
    posted = (NOW + timedelta(days=3650)).isoformat()
    asyncio.run(send_approved_reply(
        _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm={}, shown_at=posted,
        now=NOW + timedelta(minutes=30),
    ))
    assert len(sends) == 1 and sends[0]["input"]["cc"] == OWNER


def test_newer_mail_asks_again_before_the_executive_sends(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
) -> None:
    from .test_delegation_inbox import _msg

    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)
    mailbox.add(_msg("m9", "t1", sender=DANA, minutes_ago=1, text="Never mind, sorted."))
    later = NOW + timedelta(minutes=30)
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_approved_reply(
            _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm=_seen(card), now=later,
        ))
    assert err.value.code == "confirm" and err.value.extra["reasons"] == ["thread_moved_on"] and sends == []
    asyncio.run(send_approved_reply(
        _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, now=later,
        confirm={**_seen(card), "thread_moved_on": True},
    ))
    assert len(sends) == 1


def test_no_on_its_own_next_time_from_the_executive(db: Path, owner: Any) -> None:
    trained = training.Training(person_id=owner.id, training=True, drafts=False)
    payload = {"draft_to": [DANA], "from_email": DANA}
    assert training.can_allow_reply(owner.id, payload, trained) is True
    assert training.can_allow_reply(owner.id, {**payload, "from_executive": True}, trained) is False


def test_send_arguments_carry_cc_and_files() -> None:
    files = [("letter.pdf", "application/pdf", b"%PDF")]
    google = GoogleMail().build_send_arguments(
        mailbox="exec@x.example", to=DANA, subject="Re: hi", body="b", cc=[OWNER], attachments=files,
    )
    assert google["cc"] == OWNER
    assert google["attachments"] == [{"content": "JVBERg==", "filename": "letter.pdf", "mime_type": "application/pdf"}]
    graph = MicrosoftMail().build_send_arguments(
        mailbox="exec@x.example", to=DANA, subject="Re: hi", body="b", cc=[OWNER], attachments=files,
    )["body"]["Message"]
    assert graph["ccRecipients"] == [{"emailAddress": {"address": OWNER}}]
    assert graph["attachments"][0]["contentBytes"] == "JVBERg==" and graph["attachments"][0]["name"] == "letter.pdf"
    # Without them, as before.
    assert "cc" not in GoogleMail().build_send_arguments(mailbox="e", to=DANA, subject="s", body="b")


def test_chat_app_card_names_the_executive(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str],
) -> None:
    from openexecutive.integrations import channel_cards

    def labelled() -> tuple[tuple[str, str], ...]:
        options = channel_cards.card_options(card.id, "")
        return tuple((label, channel_cards.choice_of(data)) for label, data in options)

    mailbox, card = _card(owner)
    assert labelled() == (
        ("Send from me", "a"), ("Send from Open Executive, cc me", "x"), ("Skip", "s"),
    )
    _switch(card, mailbox, owner, True)
    assert labelled() == (
        ("Send from Open Executive, cc me", "a"), ("Send from me", "x"), ("Skip", "s"),
    )


def test_a_chat_tap_switches_the_sender_and_shows_the_new_card(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.integrations import channel_cards

    mailbox, card = _card(owner)
    _in_this_mailbox(monkeypatch, mailbox)
    shown = channel_cards.card_text(card.id) or ""
    tapped = asyncio.run(channel_cards.tap(
        channel_cards.Choice(card.id, channel_cards.SWITCH, channel_cards.version(card.id, shown)), owner,
    ))
    assert tapped.line == "Changed who it goes from. Check it, then send it."
    assert "this reply from Open Executive, with you on cc." in tapped.text
    assert tapped.options[0] == (
        "Send from Open Executive, cc me", channel_cards.callback_data(card.id, "a", tapped.text),
    )
    # The old card's Send from me now only shows the card as it is.
    stale = channel_cards.Choice(card.id, channel_cards.APPROVE, channel_cards.version(card.id, shown))
    assert asyncio.run(channel_cards.tap(stale, owner)).line == channel_cards.CHANGED
    # A switch sends nothing.
    assert sends == [] and _fresh(card).status == "proposed"


def test_a_chat_tap_switches_only_while_as_you_is_on(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.delegation.settings import set_enabled
    from openexecutive.integrations import channel_cards

    mailbox, card = _card(owner)
    _in_this_mailbox(monkeypatch, mailbox)
    set_enabled(owner.id, False, updated_by="test")
    shown = channel_cards.card_text(card.id) or ""
    tapped = asyncio.run(channel_cards.tap(
        channel_cards.Choice(card.id, channel_cards.SWITCH, channel_cards.version(card.id, shown)), owner,
    ))
    # The same answer as the web card's: nothing rewritten.
    assert tapped.line == "Not done: Turn on As you to change this reply here."
    assert rewrites == [] and inbox.card_payload(_fresh(card)).get(send_from.FROM_EXECUTIVE) is not True


def _in_this_mailbox(monkeypatch: pytest.MonkeyPatch, mailbox: Any) -> None:
    switch = send_from.switch

    async def in_this_mailbox(instance: Any, **kwargs: Any) -> dict[str, Any]:
        return await switch(instance, **kwargs, gmail=mailbox)

    monkeypatch.setattr(send_from, "switch", in_this_mailbox)


def test_not_on_a_card_going_on_its_own(db: Path, owner: Any, models: dict[str, Any], rewrites: list[str]) -> None:
    mailbox, card = _card(owner)
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE decision_instances SET gate_mode = 'auto_execute' WHERE id = ?", (card.id,))
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_from.switch_for_work(card.id, owner, executive=True, gmail=mailbox))
    assert err.value.code == "already_handled"
    assert inbox.card_payload(_fresh(card)).get(send_from.FROM_EXECUTIVE) is not True


def test_a_reply_in_their_mailbox_keeps_their_words_there(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
) -> None:
    inbox.set_mailbox_drafts(owner.id, True, updated_by="test")
    mailbox, card = _card(owner)
    payload = inbox.card_payload(_fresh(card))
    assert payload["draft_id"]
    calls = list(mailbox.calls)
    _switch(card, mailbox, owner, True)
    assert mailbox.calls == calls  # the draft in their mailbox wasn't touched
    asyncio.run(send_approved_reply(_fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm=_seen(card)))
    args = sends[0]["input"]
    assert args["to"] == DANA and args["cc"] == OWNER
    # Their own draft of it is taken out of their mailbox.
    assert mailbox.deleted == [payload["draft_id"]]


def test_not_after_they_replied_themselves(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
) -> None:
    from .test_delegation_inbox import _msg

    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)
    mailbox.add(_msg("m9", "t1", sender=OWNER, minutes_ago=1, labels=("SENT",), text="Done, thanks."))
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_approved_reply(
            _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm=_seen(card),
            now=NOW + timedelta(minutes=30),
        ))
    assert err.value.code == "you_replied" and sends == []


def test_an_unreadable_mailbox_stops_it(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.delegation.gmail import GmailError

    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)

    async def broken(thread_id: str) -> Any:
        raise GmailError("expired")

    monkeypatch.setattr(mailbox, "get_thread", broken)
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_approved_reply(
            _fresh(card), caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm=_seen(card),
            now=NOW + timedelta(minutes=30),
        ))
    assert err.value.code == "gmail_error" and sends == []
    assert _fresh(card).status == "proposed"


def test_new_words_stay_when_the_switch_asked_with_them_fails(
    db: Path, owner: Any, models: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(model: str, system: str, turn: str) -> dict[str, Any]:
        if system == send_from.AS_EXECUTIVE_PROMPT:
            return {}  # the rewrite as the Executive fails
        return {"body": "Hi Dana,\n\nShorter.", "send_from": "executive", "note": "Shorter."}

    monkeypatch.setattr(answers, "_call_model", fake)
    mailbox, card = _card(owner)
    saved, _, _ = asyncio.run(answers.change(
        _fresh(card), caller=LOCAL, resolver=owner.id, text="shorten it and send it from you", gmail=mailbox,
    ))
    assert "Shorter." in saved["draft_body"] and saved.get(send_from.FROM_EXECUTIVE) is not True
    assert saved["conversation"][-1]["text"] == send_from.NOTE_NOT_SWITCHED


def test_a_change_saved_while_it_checks_is_not_sent(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], sends: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.memory.decision_ledger import update_open_payload

    mailbox, card = _card(owner)
    _switch(card, mailbox, owner, True)
    tapped = _fresh(card)
    read = mailbox.get_thread

    async def changed_meanwhile(thread_id: str) -> Any:
        # Another tab saves new words while the mailbox is being read.
        now = _fresh(card)
        update_open_payload(
            now.id, {**inbox.card_payload(now), "draft_body": "Newer words."},
            expected_json=now.proposed_payload_json,
        )
        return await read(thread_id)

    monkeypatch.setattr(mailbox, "get_thread", changed_meanwhile)
    with pytest.raises(SendRefused) as err:
        asyncio.run(send_approved_reply(
            tapped, caller=LOCAL, resolver=owner.id, gmail=mailbox, confirm=_seen(card),
            now=NOW + timedelta(minutes=30),
        ))
    assert err.value.code == "already_handled" and sends == []
    assert _fresh(card).status == "proposed"


def test_the_cards_work_switches_it_before_it_hands_back(
    db: Path, owner: Any, models: dict[str, Any], rewrites: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.delegation import card_work
    from openexecutive.orchestrator import reply_card_tools
    from openexecutive.orchestrator.schedule_tools import current_session
    from openexecutive.orchestrator.session import Session

    mailbox, card = _card(owner)
    order: list[str] = []
    real_apply, real_switch = card_work.apply, send_from.switch_for_work

    async def apply(*a: Any, **k: Any) -> Any:
        order.append("apply")
        return await real_apply(*a, **k)

    async def switch(*a: Any, **k: Any) -> Any:
        order.append("switch")
        return await real_switch(*a, **k)

    monkeypatch.setattr(card_work, "still_working", lambda c: True)
    monkeypatch.setattr(card_work, "keep", lambda coro: coro.close())
    monkeypatch.setattr(card_work, "apply", apply)
    monkeypatch.setattr(send_from, "switch_for_work", switch)
    writer = type("W", (), {"person": owner, "mailbox": mailbox})()
    monkeypatch.setattr("openexecutive.orchestrator.delegation_tools._writer", lambda name: writer)
    token = current_session.set(Session(reply_card_work=card.id))
    try:
        out = json.loads(asyncio.run(reply_card_tools.handle_update_reply_card(
            {"note": "Sending it from me.", "send_from": "executive"},
        )))
        assert out["status"] == "ok" and order == ["switch", "apply"]
        assert inbox.card_payload(_fresh(card)).get(send_from.FROM_EXECUTIVE) is True

        async def refused(*a: Any, **k: Any) -> None:
            raise SendRefused(409, "gone", "It was sent.")

        monkeypatch.setattr(card_work, "apply", refused)
        out = json.loads(asyncio.run(reply_card_tools.handle_update_reply_card({"send_from": "me"})))
        assert out["error"].startswith("Who it goes from was changed, but nothing else was")
        out = json.loads(asyncio.run(reply_card_tools.handle_update_reply_card({"send_from": "someone"})))
        assert out["error"].startswith("Nothing was changed")
    finally:
        current_session.reset(token)
