"""What reaches a person's own inbox becomes the Executive's context
(delegation/mail_context.py, delegation/noticed.py): what is kept and for
how long, once only, the gist, and the one turn on what ties to something
known."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openexecutive.audit import logger as audit_logger
from openexecutive.audit.logger import AuditLogger
from openexecutive.delegation import inbox, mail_context, noticed
from openexecutive.delegation.gmail import MailMessage
from openexecutive.delegation.settings import set_enabled
from openexecutive.memory import episodic
from openexecutive.people import registry as people_registry
from openexecutive.people import store as people_store

OWNER = "olivia@co.example"
DANA = "dana@northpeak.example"
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    monkeypatch.setattr(people_store, "DB_PATH", path)
    episodic.initialize_db(path)
    people_store.initialize_db(path)
    people_registry.invalidate()
    monkeypatch.setattr(audit_logger, "_default_logger", AuditLogger(db_path=path))
    monkeypatch.setattr(inbox, "_client_slot_active", lambda: False)
    mail_context._SCANNING.clear()
    yield path
    people_registry.invalidate()


@pytest.fixture
def owner() -> Any:
    pid = people_store.upsert_person(full_name="Olivia Owner", is_principal=True, email=OWNER)
    people_store.upsert_person(full_name="Dana Park", email=DANA, kind="contact")
    people_registry.invalidate()
    set_enabled(pid, True, updated_by="test")
    return people_store.get_person(pid)


def _msg(
    mid: str, *, sender: str = "alerts@delta.example", name: str = "Delta", subject: str = "Flight DL123 update",
    text: str = "Your flight DL123 to Boston is delayed 90 minutes and now leaves at 4:10 PM.",
    labels: tuple[str, ...] = ("INBOX", "CATEGORY_UPDATES"), to: list[str] | None = None, minutes_ago: int = 20,
    **flags: Any,
) -> MailMessage:
    return MailMessage(
        id=mid, thread_id=f"t-{mid}", from_addr=sender, from_name=name, to=to if to is not None else [OWNER],
        subject=subject, labels=list(labels), text=text,
        received_at=(NOW - timedelta(minutes=minutes_ago)).isoformat(), **flags,
    )


class Mailbox:
    def __init__(self, *messages: MailMessage) -> None:
        self.messages = list(messages)
        self.asked_since: list[datetime] = []

    async def profile_email(self) -> str:
        return OWNER

    async def send_as_addresses(self) -> list[str]:
        return [OWNER]

    async def received_since(self, after: datetime, *, before: datetime | None = None, limit: int = 25) -> list[Any]:
        self.asked_since.append(after)
        return list(self.messages)[:limit]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The gist call, scripted: the flight email ties to a meeting and acts,
    anything else is information alone."""
    seen: dict[str, Any] = {"turns": [], "acts": []}

    async def gist_call(turn: str) -> dict[str, Any]:
        seen["turns"].append(turn)
        entries = []
        for n in range(1, turn.count('<email n="') + 1):
            block = turn.split(f'<email n="{n}">', 1)[1].split("</email>", 1)[0]
            if "DL123" in block:
                entries.append({"n": n, "gist": "Delta: DL123 delayed 90 minutes, now 4:10 PM",
                                "tie": "3:00 PM Board prep", "act": True})
            elif "receipt" in block.lower():
                entries.append({"n": n, "gist": "Receipt for $42 lunch", "tie": "", "act": True})
            else:
                entries.append({"n": n, "gist": "A note", "tie": "", "act": False})
        return {"emails": entries}

    async def prepare(person: Any, client: Any, *, off_team: bool) -> tuple[Any, Any]:
        return None, {"off_team": off_team}

    async def turn(person: Any, executive: Any, session: Any, prompt: str) -> None:
        seen["acts"].append((person.id, prompt, session["off_team"]))

    seen["prepare"] = prepare
    monkeypatch.setattr(mail_context, "_call_model", gist_call)
    monkeypatch.setattr(mail_context, "_prepare", prepare)
    monkeypatch.setattr(mail_context, "_turn", turn)
    return seen


def _look(person: Any, client: Mailbox, now: datetime = NOW) -> mail_context.LookResult:
    async def go() -> mail_context.LookResult:
        result = await mail_context.look_person(person, gmail=client, now=now)
        await asyncio.gather(*list(mail_context._TURN_TASKS))
        return result

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


def test_an_item_is_kept_once_and_listed_for_its_person_alone(owner: Any) -> None:
    assert noticed.note(owner.id, source=noticed.EMAIL, item_key="m1", sender_name="UPS", subject="Delivered", now=NOW)
    assert not noticed.note(owner.id, source=noticed.EMAIL, item_key="m1", sender_name="UPS", subject="Delivered", now=NOW)
    assert [i.item_key for i in noticed.recent(owner.id, now=NOW)] == ["m1"]
    assert noticed.recent(owner.id + 1, now=NOW) == []


def test_the_same_item_from_another_source_is_dropped(owner: Any) -> None:
    noticed.note(owner.id, source=noticed.EMAIL, item_key="m1", sender_name="Delta",
                 subject="Re: Flight DL123 update", received_at=NOW, now=NOW)
    assert not noticed.note(owner.id, source="phone_alert", item_key="com.delta|9", sender_name="Delta",
                            subject="Flight DL123 update", received_at=NOW + timedelta(minutes=2), now=NOW)
    # Hours apart it is a new item.
    assert noticed.note(owner.id, source="phone_alert", item_key="com.delta|10", sender_name="Delta",
                        subject="Flight DL123 update", received_at=NOW + timedelta(hours=3), now=NOW)


def test_a_refreshed_item_takes_the_new_words_and_is_new_again(owner: Any) -> None:
    noticed.note(owner.id, source="phone_alert", item_key="k", sender_name="Delta", subject="Delayed",
                 gist="30 minutes late", now=NOW)
    assert noticed.claim_to_act(owner.id, ["k"], now=NOW) == ["k"]
    assert noticed.claim_to_act(owner.id, ["k"], now=NOW) == []
    assert not noticed.note(owner.id, source="phone_alert", item_key="k", sender_name="Delta", subject="Delayed",
                            gist="30 minutes late", refresh=True, now=NOW)
    assert noticed.note(owner.id, source="phone_alert", item_key="k", sender_name="Delta", subject="Delayed",
                        gist="90 minutes late", refresh=True, now=NOW)
    item = noticed.recent(owner.id, now=NOW)[0]
    assert (item.gist, item.updates, item.acted) == ("90 minutes late", 1, False)
    assert noticed.claim_to_act(owner.id, ["k"], now=NOW) == ["k"]


def test_items_go_after_two_days_and_with_forget(owner: Any) -> None:
    noticed.note(owner.id, source=noticed.EMAIL, item_key="old", subject="Old", received_at=NOW, now=NOW)
    later = NOW + noticed.KEEP + timedelta(hours=1)
    assert noticed.recent(owner.id, now=later) == []
    noticed.note(owner.id, source=noticed.EMAIL, item_key="new", subject="New", received_at=later, now=later)
    assert noticed.kept_keys(owner.id, ["old", "new"]) == {"new"}  # pruned on the write
    assert noticed.forget(owner.id) == 1
    assert noticed.recent(owner.id, now=later) == []


def test_words_narrow_the_list(owner: Any) -> None:
    noticed.note(owner.id, source=noticed.EMAIL, item_key="a", sender_name="UPS", subject="Delivered",
                 gist="Box left at the front door", now=NOW)
    noticed.note(owner.id, source=noticed.EMAIL, item_key="b", sender_name="Chase", subject="Card alert", now=NOW)
    assert [i.item_key for i in noticed.recent(owner.id, words="front door", now=NOW)] == ["a"]


# --------------------------------------------------------------------------- #
# Reading the inbox
# --------------------------------------------------------------------------- #


def test_updates_mail_is_kept_with_a_gist_and_noise_is_left_out(owner: Any, model: dict[str, Any]) -> None:
    client = Mailbox(
        _msg("m1"),
        _msg("m2", sender="deals@shop.example", name="Shop", labels=("INBOX", "CATEGORY_PROMOTIONS")),
        _msg("m3", sender=OWNER, name="Olivia"),
        _msg("m4", sender="spam@x.example", labels=("SPAM",)),
        _msg("m5", sender=DANA, name="Dana Park", subject="Notes", text="Here are the notes.",
             labels=("INBOX",), sender_authenticated=True),
    )
    result = _look(owner, client)
    assert result.status == "ok" and result.kept == 2
    kept = {i.item_key: i for i in noticed.recent(owner.id, now=NOW)}
    assert set(kept) == {"m1", "m5"}
    assert kept["m1"].gist == "Delta: DL123 delayed 90 minutes, now 4:10 PM"
    assert kept["m1"].tie == "3:00 PM Board prep"
    # The sender's relation is told to the model; the email is data in its own block.
    assert "Sender: contact" in model["turns"][0]
    assert '<email n="1">' in model["turns"][0]


def test_it_acts_once_only_on_what_ties_to_something_known(owner: Any, model: dict[str, Any]) -> None:
    client = Mailbox(_msg("m1"), _msg("m2", sender="receipts@cafe.example", name="Cafe", subject="Your receipt",
                                      text="Thanks for your receipt of $42."))
    result = _look(owner, client)
    assert result.acted == 1
    [(person_id, prompt, off_team)] = model["acts"]
    assert person_id == owner.id and off_team is True
    assert "DL123" in prompt and "Board prep" in prompt and "receipt" not in prompt.lower()
    assert "<untrusted_content" in prompt
    # The next look reads the same mail again (overlap) and does nothing twice.
    _look(owner, client, NOW + timedelta(minutes=10))
    assert len(model["acts"]) == 1
    assert len(model["turns"]) == 1


def test_mail_the_executive_also_got_is_left_to_its_own_turn(owner: Any, model: dict[str, Any]) -> None:
    from openexecutive.config import get_settings

    _look(owner, Mailbox(_msg("m1", to=[OWNER, get_settings().exec_email_address])))
    assert model["acts"] == []
    assert noticed.recent(owner.id, now=NOW)[0].item_key == "m1"  # still known


def test_a_failed_gist_still_keeps_who_and_what(owner: Any, monkeypatch: pytest.MonkeyPatch,
                                                model: dict[str, Any]) -> None:
    async def broken(turn: str) -> dict[str, Any]:
        raise RuntimeError("model busy")

    monkeypatch.setattr(mail_context, "_call_model", broken)
    result = _look(owner, Mailbox(_msg("m1")))
    assert result.failed == 1 and result.acted == 0
    item = noticed.recent(owner.id, now=NOW)[0]
    assert (item.subject, item.gist) == ("Flight DL123 update", "")


def test_nothing_is_read_with_act_as_me_off(owner: Any, model: dict[str, Any]) -> None:
    set_enabled(owner.id, False, updated_by="test")
    client = Mailbox(_msg("m1"))
    assert _look(owner, client).status == "act_as_me_off"
    assert client.asked_since == []


def test_the_first_look_goes_back_a_day_and_then_from_where_it_got_to(owner: Any, model: dict[str, Any]) -> None:
    client = Mailbox()
    _look(owner, client)
    _look(owner, client, NOW + timedelta(minutes=10))
    assert client.asked_since[0] == NOW - mail_context.FIRST_LOOK
    assert client.asked_since[1] == NOW - mail_context.OVERLAP


def test_due_follows_the_poll_interval(owner: Any, model: dict[str, Any]) -> None:
    assert mail_context._due(NOW) == [owner.id]
    _look(owner, Mailbox())
    assert mail_context._due(NOW + timedelta(minutes=1)) == []


def test_forget_starts_over(owner: Any, model: dict[str, Any]) -> None:
    _look(owner, Mailbox(_msg("m1")))
    mail_context.forget(owner.id)
    assert noticed.recent(owner.id, now=NOW) == []
    assert mail_context.get_cursor(owner.id).read_through is None


def test_forget_by_source_keeps_the_other_sources(owner: Any) -> None:
    noticed.note(owner.id, source=noticed.EMAIL, item_key="m1", subject="Mail", now=NOW)
    noticed.note(owner.id, source="phone_alert", item_key="p1", subject="Alert", now=NOW)
    assert noticed.forget(owner.id, source=noticed.EMAIL) == 1
    assert [i.item_key for i in noticed.recent(owner.id, now=NOW)] == ["p1"]


def test_an_unverified_teammate_address_is_a_stranger_and_the_turn_is_outside_words(
    owner: Any, model: dict[str, Any],
) -> None:
    people_store.upsert_person(full_name="Cara Fo", email="cfo@co.example")
    people_registry.invalidate()
    forged = _msg("m1", sender="cfo@co.example", name="Cara Fo", sender_authenticated=False)
    _look(owner, Mailbox(forged))
    assert "Sender: stranger" in model["turns"][0]
    [(_, prompt, off_team)] = model["acts"]
    assert off_team is True
    # The turn's content check reads the email as quoted mail.
    assert 'source="email_quoted"' in prompt


def test_a_team_email_gisted_beside_an_outside_one_is_outside_words(owner: Any, model: dict[str, Any]) -> None:
    people_store.upsert_person(full_name="Cara Fo", email="cfo@co.example")
    people_registry.invalidate()
    team = _msg("m1", sender="cfo@co.example", name="Cara Fo", sender_authenticated=True)
    other = _msg("m2", sender="news@x.example", name="X", subject="Hello", text="Nothing here.")
    _look(owner, Mailbox(team, other))
    [(_, _prompt, off_team)] = model["acts"]
    assert off_team is True


def test_an_all_team_look_is_the_teams_words(owner: Any, model: dict[str, Any]) -> None:
    people_store.upsert_person(full_name="Cara Fo", email="cfo@co.example")
    people_registry.invalidate()
    _look(owner, Mailbox(_msg("m1", sender="cfo@co.example", name="Cara Fo", sender_authenticated=True)))
    [(_, _prompt, off_team)] = model["acts"]
    assert off_team is False


def test_a_busy_inbox_is_read_page_by_page(owner: Any, model: dict[str, Any]) -> None:
    from openexecutive.monitoring.sources import mailbox as watch

    promos = [
        _msg(f"p{i}", sender="deals@shop.example", name="Shop", labels=("INBOX", "CATEGORY_PROMOTIONS"),
             minutes_ago=i + 1)
        for i in range(watch._READ_LIMIT)
    ]
    older = _msg("m1", minutes_ago=600)

    class Paged(Mailbox):
        async def received_since(
            self, after: datetime, *, before: datetime | None = None, limit: int = 25,
        ) -> list[Any]:
            self.asked_since.append(after)
            page = [m for m in self.messages if before is None or datetime.fromisoformat(m.received_at) < before]
            return page[:limit]

    _look(owner, Paged(*promos, older))
    assert [i.item_key for i in noticed.recent(owner.id, now=NOW)] == ["m1"]


def test_a_turn_that_failed_to_start_is_tried_again(owner: Any, monkeypatch: pytest.MonkeyPatch,
                                                    model: dict[str, Any]) -> None:
    calls: list[int] = []

    async def failing(person: Any, client: Any, *, off_team: bool) -> tuple[Any, Any]:
        calls.append(person.id)
        raise RuntimeError("gateway down")

    monkeypatch.setattr(mail_context, "_prepare", failing)
    client = Mailbox(_msg("m1"))
    _look(owner, client)
    assert len(calls) == 1 and model["acts"] == []
    monkeypatch.setattr(mail_context, "_prepare", model["prepare"])
    _look(owner, client, NOW + timedelta(minutes=10))
    assert len(model["acts"]) == 1
    _look(owner, client, NOW + timedelta(minutes=20))
    assert len(model["acts"]) == 1


def test_a_turn_that_failed_after_starting_is_not_run_again(owner: Any, monkeypatch: pytest.MonkeyPatch,
                                                            model: dict[str, Any]) -> None:
    calls: list[str] = []

    async def failing(person: Any, executive: Any, session: Any, prompt: str) -> None:
        calls.append(prompt)  # it may already have told the attendees
        raise RuntimeError("model busy")

    monkeypatch.setattr(mail_context, "_turn", failing)
    client = Mailbox(_msg("m1"))
    _look(owner, client)
    _look(owner, client, NOW + timedelta(minutes=10))
    assert len(calls) == 1


def test_the_principals_turn_is_not_under_the_looks_private_scope(owner: Any, monkeypatch: pytest.MonkeyPatch,
                                                                  model: dict[str, Any]) -> None:
    from openexecutive.audit import context as audit_context

    scopes: list[tuple[bool, Any]] = []

    async def turn(person: Any, executive: Any, session: Any, prompt: str) -> None:
        scopes.append((audit_context._rows_private.get(), audit_context._rows_owner.get()))

    monkeypatch.setattr(mail_context, "_turn", turn)
    _look(owner, Mailbox(_msg("m1")))
    assert scopes == [(False, None)]


def test_waits_for_the_reply_watcher_and_leaves_its_cards_alone(owner: Any, model: dict[str, Any]) -> None:
    inbox.set_watch(owner.id, True, updated_by="test", now=NOW - timedelta(days=1))
    fresh = _msg("m1", minutes_ago=1)
    _look(owner, Mailbox(fresh))
    assert model["acts"] == []  # the reply watcher hasn't looked yet
    inbox._claim(owner.id, fresh, relation="", outcome=inbox.DRAFTED, now=NOW)
    _look(owner, Mailbox(fresh), NOW + timedelta(minutes=10))
    assert model["acts"] == []  # its reply card handles it
    assert noticed.waiting_to_act(owner.id, source=noticed.EMAIL, now=NOW + timedelta(minutes=10)) == []


def test_acts_once_the_reply_watcher_needs_no_reply(owner: Any, model: dict[str, Any]) -> None:
    inbox.set_watch(owner.id, True, updated_by="test", now=NOW - timedelta(days=1))
    fresh = _msg("m1", minutes_ago=1)
    _look(owner, Mailbox(fresh))
    inbox._claim(owner.id, fresh, relation="", outcome=inbox.NOT_NEEDED, now=NOW)
    _look(owner, Mailbox(fresh), NOW + timedelta(minutes=10))
    assert len(model["acts"]) == 1


def test_turned_off_mid_look_keeps_nothing(owner: Any, monkeypatch: pytest.MonkeyPatch,
                                           model: dict[str, Any]) -> None:
    real = mail_context._call_model

    async def off_then_gist(turn: str) -> dict[str, Any]:
        set_enabled(owner.id, False, updated_by="test")
        mail_context.forget(owner.id)
        return await real(turn)

    monkeypatch.setattr(mail_context, "_call_model", off_then_gist)
    assert _look(owner, Mailbox(_msg("m1"))).status == "act_as_me_off"
    assert noticed.recent(owner.id, now=NOW) == []
    assert model["acts"] == []
    assert mail_context.get_cursor(owner.id).read_through is None
