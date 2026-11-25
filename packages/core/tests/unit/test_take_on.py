"""Home's "I can also take on": the one list (briefing.take_on), the daily
pass's checks (briefing.take_on_maker), and Do it / Or tell me / Hide
(api.routes.take_on)."""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openexecutive.briefing import take_on
from openexecutive.briefing import take_on_maker as maker
from openexecutive.config import background_turn_limit
from openexecutive.memory import episodic

ME = "olivia@co.example"
# The real suggested-watch reader: the fixture below stubs it for every test.
WATCH = take_on._watch
WATCH_REAL = take_on._watch
# The real reader of what runs on its own: the fixture stubs it too.
ON_ITS_OWN = maker.on_its_own


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "take_on.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    # Only the made offers: the other places are read from their own stores.
    monkeypatch.setattr(take_on, "_repeat", lambda _pid: [])
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: [])
    monkeypatch.setattr(maker, "on_its_own", lambda _person: maker.OnItsOwn())
    # The engine on its own: no other source and the default reads, whatever
    # an app built earlier in the run plugged in.
    monkeypatch.setattr(take_on, "_sources", {})
    monkeypatch.setattr(take_on, "hooks", take_on.Hooks())
    yield path


def _offer(title: str = "I can send Dana the signed proposal", kind: str = "email") -> take_on.Offer:
    return take_on.Offer(
        key=take_on.made_key(title), origin="made", kind=kind, title=title,  # type: ignore[arg-type]
        source="Your inbox", source_detail="Dana, Nov 20",
        preview=[("To", "dana@client.example"), ("Attach", "Proposal.pdf")],
    )


# Three emails about three different things.
_APART = [("Dana", "the signed proposal", "Proposal.pdf"), ("Priya", "the March invoice", "Invoice.pdf"),
          ("Marco", "the hiring plan", "Hiring.docx")]


def _apart(i: int) -> take_on.Offer:
    name, what, attach = _APART[i]
    return take_on.Offer(
        key=take_on.made_key(f"I can send {name} {what}"), origin="made", kind="email",
        title=f"I can send {name} {what}", source="Your inbox",
        preview=[("To", f"{name.lower()}@client.example"), ("Attach", attach)],
    )


def _list(email: str = ME, **kw: Any) -> list[take_on.Offer]:
    return take_on.offers_for(email, 1, principal=True, **kw)


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #


def test_a_made_offer_is_kept_once_and_shows_for_its_person_only() -> None:
    o = _offer()
    assert take_on.put_made(ME, [o]) == 1
    assert take_on.put_made(ME, [o]) == 0
    mine = _list()
    assert [x.title for x in mine] == [o.title]
    assert mine[0].preview == [("To", "dana@client.example"), ("Attach", "Proposal.pdf")]
    assert _list("sam@co.example") == []


def test_an_offer_nobody_used_leaves_after_three_days() -> None:
    take_on.put_made(ME, [_offer()])
    assert _list()
    later = datetime.now(UTC) + timedelta(days=take_on.KEEP_DAYS, hours=1)
    assert _list(now=later) == []


def test_a_suggested_watch_stays_while_it_is_still_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    watch = take_on.Offer(key="watch:eia", origin="watch", kind="watch", title="I can watch diesel prices",
                          source="News I watch")
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: [watch])
    assert [o.key for o in _list()] == ["watch:eia"]
    later = datetime.now(UTC) + timedelta(days=take_on.KEEP_DAYS + 5)
    assert [o.key for o in _list(now=later)] == ["watch:eia"]
    # Answered on the Watch list: it goes.
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: [])
    assert _list(now=later) == []


def test_hide_already_done_keeps_it_away_and_later_brings_it_back_in_a_week() -> None:
    a, b = _offer("I can book the review"), _offer("I can chase the invoice", "followup")
    take_on.put_made(ME, [a, b])
    take_on.hide(ME, _list()[0], "already_done")
    take_on.hide(ME, _list()[0], "later")
    assert _list() == []
    back = datetime.now(UTC) + timedelta(days=take_on.LATER_DAYS, hours=1)
    assert [o.title for o in _list(now=back)] == ["I can chase the invoice"]
    assert _list(now=back + timedelta(days=take_on.KEEP_DAYS - 1)) != []
    assert _list(now=back + timedelta(days=take_on.KEEP_DAYS, hours=1)) == []


def test_one_put_off_late_with_later_gets_its_days_back(monkeypatch: pytest.MonkeyPatch) -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    start = datetime.now(UTC)
    _list(now=start)
    late = start + timedelta(days=take_on.KEEP_DAYS - 0.5)
    monkeypatch.setattr(take_on, "_now", lambda: late)
    (shown,) = _list(now=late)
    take_on.hide(ME, shown, "later")
    back = late + timedelta(days=take_on.LATER_DAYS, hours=1)
    assert [x.key for x in _list(now=back)] == [o.key]
    assert [x.key for x in _list(now=back + timedelta(days=take_on.KEEP_DAYS - 1))] == [o.key]


def test_one_that_failed_and_was_put_off_with_later_comes_back() -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, _list()[0])
    take_on.finish(ME, o.key, "failed", outcome="It stopped before it finished.")
    take_on.hide(ME, _list()[0], "later")
    assert _list() == []
    back = datetime.now(UTC) + timedelta(days=take_on.LATER_DAYS, hours=1)
    (got,) = _list(now=back)
    assert (got.key, got.status, got.outcome) == (o.key, "", "")


def test_one_back_from_later_and_done_is_not_offered_again(monkeypatch: pytest.MonkeyPatch) -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.hide(ME, _list()[0], "later")
    back = datetime.now(UTC) + timedelta(days=take_on.LATER_DAYS, hours=1)
    monkeypatch.setattr(take_on, "_now", lambda: back)
    (again,) = _list(now=back)
    assert take_on.start(ME, again)
    take_on.finish(ME, again.key, "done", outcome="Sent it.")
    # What it did counts as done and is kept, so the same step isn't offered
    # again in other words.
    assert take_on.about(o.title, o.preview) in take_on.about_already(ME)
    assert [f.key for f in take_on.finished(ME, back - timedelta(hours=1))] == [o.key]


def test_the_places_offers_come_from_take_turns_at_the_top(monkeypatch: pytest.MonkeyPatch) -> None:
    take_on.put_made(ME, [_apart(i) for i in range(3)])
    watch = take_on.Offer(key="watch:eia", origin="watch", kind="watch", title="I can watch diesel prices",
                          source="News I watch")
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: [watch])
    assert [o.origin for o in _list()][:3] == ["made", "watch", "made"]


def test_not_useful_twice_on_one_kind_stops_that_kind() -> None:
    offers = [_apart(i) for i in range(3)]
    take_on.put_made(ME, offers)
    for o in _list()[:2]:
        take_on.hide(ME, o, "not_useful")
    assert take_on.turned_down_kinds(ME) == {"email"}
    assert _list() == []
    take_on.put_made(ME, [_offer("I can write the launch plan", "doc")])
    assert [o.kind for o in _list()] == ["doc"]


def test_watch_suggestions_still_show_after_two_watches_were_not_useful(monkeypatch: pytest.MonkeyPatch) -> None:
    watches = [take_on.Offer(key=f"watch:w{i}", origin="watch", kind="watch", title=f"I can watch {name}",
                             source="News I watch") for i, name in enumerate(("diesel prices", "Acme hiring", "rates"))]
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: list(watches))
    for o in _list()[:2]:
        take_on.hide(ME, o, "not_useful")
    assert take_on.turned_down_kinds(ME) == {"watch"}
    # Each was declined on its own; the one still waiting on the Watch list shows.
    assert [o.key for o in _list()] == ["watch:w2"]


def test_do_it_runs_once_and_records_what_came_of_it() -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    assert take_on.start(ME, o)
    assert not take_on.start(ME, o)
    take_on.finish(ME, o.key, "done", outcome="Sent it.", done=[("Emailed Dana", "/sent/1"), ("x", "javascript:x")])
    got = _list()[0]
    assert got.status == "done"
    assert got.done == [("Emailed Dana", "/sent/1"), ("x", "")]


def test_a_run_left_working_reads_as_failed_after_a_while() -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    later = datetime.now(UTC) + background_turn_limit() + timedelta(minutes=1)
    got = _list(now=later)[0]
    assert got.status == "failed" and got.outcome == take_on.STALE_OUTCOME
    # It never finished, so it ages out from when it began.
    assert _list(now=datetime.now(UTC) + timedelta(days=take_on.DONE_DAYS, hours=1)) == []


def test_or_tell_me_changes_the_offer_and_undo_puts_it_back() -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.change(ME, o, "I can send Dana the proposal and cc Sam", [("To", "dana@client.example"), ("Cc", "Sam")],
                   "cc Sam")
    got = _list()[0]
    assert got.changed and got.said == "cc Sam" and got.title.endswith("cc Sam")
    assert "Cc: Sam" in take_on.ask_text(got)
    take_on.undo_change(ME, o.key)
    back = _list()[0]
    assert not back.changed and back.title == o.title


def test_the_working_one_comes_first() -> None:
    a, b = _offer("I can book the review", "meeting"), _offer("I can chase the invoice", "followup")
    take_on.put_made(ME, [a, b])
    second = _list()[1]
    take_on.start(ME, second)
    assert _list()[0].key == second.key


def test_cadence_in_words() -> None:
    assert take_on.cadence_words("nonsense") == "on a schedule"
    assert take_on.cadence_words("weekly@mon@13:00").startswith("every ")
    assert take_on.cadence_words("daily@09:30").startswith("every day at ")


# --------------------------------------------------------------------------- #
# The daily pass's checks
# --------------------------------------------------------------------------- #


def _inputs(text: str = "INBOUND\n- Dana <dana@client.example> sent the proposal", *, calendar: bool = False,
            apps: list[str] | None = None) -> maker.Inputs:
    return maker.Inputs(text, calendar=calendar, apps=apps or [], team=[("Sam Lee", "sam@co.example")])


def test_check_keeps_only_steps_that_can_be_done() -> None:
    items = [
        {"kind": "email", "title": "I can send Dana the proposal", "source": "Your inbox",
         "preview": [{"label": "To", "value": "dana@client.example"}]},
        {"kind": "email", "title": "I can email Zed", "source": "x",
         "preview": [{"label": "To", "value": "zed@nowhere.example"}]},  # an address it never saw
        {"kind": "meeting", "title": "I can book a call with Dana", "source": "x",
         "preview": [{"label": "With", "value": "Dana"}]},  # no calendar
        {"kind": "app", "title": "I can refund the order", "source": "Stripe",
         "preview": [{"label": "App", "value": "Stripe"}]},  # not connected
        {"kind": "message", "title": "I can tell Pat", "source": "x",
         "preview": [{"label": "To", "value": "Pat"}]},  # not on the team
        {"kind": "message", "title": "I can tell Sam the deal closed", "source": "x",
         "preview": [{"label": "To", "value": "Sam"}]},
        {"kind": "launch_rockets", "title": "I can do anything", "preview": [{"label": "a", "value": "b"}]},
        {"kind": "doc", "title": "", "preview": [{"label": "Doc", "value": "Plan"}]},
    ]
    kept = maker.check(items, _inputs(), set())
    assert [o.title for o in kept] == ["I can send Dana the proposal", "I can tell Sam the deal closed"]
    assert all(o.origin == "made" and o.key.startswith("made:") for o in kept)


def test_check_lets_a_meeting_or_app_through_when_connected_and_skips_quiet_kinds() -> None:
    items = [
        {"kind": "meeting", "title": "I can book a call with Dana", "preview": [{"label": "With", "value": "Dana"}]},
        {"kind": "app", "title": "I can refund the order", "preview": [{"label": "App", "value": "stripe"}]},
        {"kind": "doc", "title": "I can write the plan", "preview": [{"label": "Doc", "value": "Plan"}]},
    ]
    kept = maker.check(items, _inputs(calendar=True, apps=["Stripe"]), {"doc"})
    assert [o.kind for o in kept] == ["meeting", "app"]


def test_no_pass_over_nothing_or_with_background_off(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(take_on.hooks, "background_on", lambda: True)
    assert not maker.due(ME, _inputs("COMPANY: Acme\n\nCONNECTED APPS\n- none"))
    assert maker.due(ME, _inputs())
    take_on.record_pass(ME, _inputs().hash)
    assert not maker.due(ME, _inputs("INBOUND\n- something new"))  # once a day
    monkeypatch.setattr(take_on.hooks, "background_on", lambda: False)
    assert not maker.due("new@co.example", _inputs())


async def test_the_pass_keeps_checked_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive import providers
    from openexecutive.audit import usage

    reply = json.dumps([
        {"kind": "email", "title": "I can send Dana the proposal", "source": "Your inbox",
         "preview": [{"label": "To", "value": "dana@client.example"}]},
        {"kind": "email", "title": "I can email a stranger", "preview": [{"label": "To", "value": "x@y.example"}]},
    ])

    class Provider:
        async def messages_create(self, **_kw: Any) -> Any:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)])

    monkeypatch.setattr(providers, "get_provider", lambda _m: Provider())
    monkeypatch.setattr(usage, "log_model_usage", lambda *a, **k: None)
    monkeypatch.setattr(take_on.hooks, "background_model", lambda: "claude-haiku-5-5")
    assert await maker.run_pass(ME, _inputs()) == 1
    assert [o.title for o in _list()] == ["I can send Dana the proposal"]
    assert take_on.last_pass(ME)[0] is not None


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from openexecutive.api.routes import take_on as route
    from openexecutive.briefing import take_on_maker

    monkeypatch.setattr(route, "_me", lambda _r: route.Me(ME, 1, True))
    monkeypatch.setattr(take_on_maker, "schedule", lambda *a, **k: False)
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app)


def test_hide_never_lands_on_a_step_do_it_just_started() -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    seen = _list()[0]  # read before Do it, as a second screen would
    take_on.start(ME, o)
    assert not take_on.hide(ME, seen, "not_useful")
    (got,) = _list()
    assert got.status == "working"


def test_two_readers_settling_one_approved_card_tell_its_source_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.memory import decision_ledger

    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {9: SimpleNamespace(status="approved_unchanged")})
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [])
    told: list[str] = []
    monkeypatch.setattr(route, "_taken_up", lambda _me, o: told.append(o.key))
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=9)
    me = route.Me(ME, 1, True)
    first, second = _list(), _list()
    route._settle_cards(me, first)
    route._settle_cards(me, second)
    assert told == [o.key]


def test_hiding_a_step_already_under_way_leaves_it_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.memory import decision_ledger

    monkeypatch.setattr(decision_ledger, "get_decision_instances", lambda ids: {8: SimpleNamespace(status="proposed")})
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=8)
    assert client.post(f"/take-on/{o.key}/hide", json={"reason": "not_useful"}).status_code == 204
    states = take_on._states(take_on.who(ME), None)
    assert states[o.key]["status"] == "card" and not states[o.key]["hidden_reason"]


def test_a_principal_on_people_without_an_email_still_has_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api import caller as api_caller
    from openexecutive.api.routes import take_on as route
    from openexecutive.people import store as people_store

    monkeypatch.setattr(api_caller, "caller", lambda _r: SimpleNamespace(email=""))
    monkeypatch.setattr(api_caller, "person_id_for", lambda _w: 3)
    monkeypatch.setattr(people_store, "get_person", lambda _pid: SimpleNamespace(is_principal=True, email=None))
    me = route._me(object())  # type: ignore[arg-type]
    assert me.email and me.person_id == 3 and me.principal
    o = _offer()
    take_on.put_made(me.email, [o])
    assert [x.key for x in take_on.offers_for(me.email, 3, principal=True)] == [o.key]


def test_someone_not_on_people_gets_no_list_and_no_pass(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.briefing import take_on_maker

    passes: list[str] = []
    monkeypatch.setattr(route, "_me", lambda _r: route.Me("guest@else.example", None, False))
    monkeypatch.setattr(take_on_maker, "schedule", lambda email, *a, **k: passes.append(email) or True)
    assert client.get("/take-on").json()["offers"] == []
    assert passes == []


def test_an_unsigned_principal_reads_their_own_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a local install the caller names no email: the list is keyed by
    the principal's People email."""
    from openexecutive.api import caller as api_caller
    from openexecutive.api.routes import take_on as route
    from openexecutive.people import store as people_store

    monkeypatch.setattr(api_caller, "caller", lambda _r: SimpleNamespace(email=""))
    monkeypatch.setattr(api_caller, "person_id_for", lambda _w: 1)
    monkeypatch.setattr(people_store, "get_person", lambda _pid: SimpleNamespace(is_principal=True, email=ME))
    me = route.reader(SimpleNamespace())
    assert (me.email, me.person_id, me.principal) == (ME, 1, True)


def test_done_for_you_reads_the_same_list_as_take_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.api.routes import today

    monkeypatch.setattr(route, "reader", lambda _r: route.Me(ME, None, True))
    monkeypatch.setattr(today, "_build_activity", lambda *a, **k: today.ActivityResponse(items=[]))
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "done", outcome="Sent it.")
    rows = today._done_for_you(SimpleNamespace(), None, datetime.now(UTC))  # type: ignore[arg-type]
    assert f"take_on:{o.key}" in [r.key for r in rows]


def test_get_lists_the_callers_offers(client: TestClient) -> None:
    take_on.put_made(ME, [_offer()])
    body = client.get("/take-on").json()
    assert body["home_max"] == 3
    assert body["offers"][0]["kind_label"] == "Email"
    assert body["offers"][0]["preview"][0] == {"label": "To", "value": "dana@client.example"}
    assert body["making"] is False


def test_the_list_says_when_new_offers_are_being_made(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.briefing import take_on_maker

    monkeypatch.setattr(take_on_maker, "schedule", lambda *a, **k: True)
    assert client.get("/take-on").json()["making"] is True
    monkeypatch.setattr(take_on_maker, "schedule", lambda *a, **k: False)
    take_on_maker._in_flight.add(take_on.who(ME))
    try:
        assert client.get("/take-on").json()["making"] is True
    finally:
        take_on_maker._in_flight.discard(take_on.who(ME))
    assert client.get("/take-on").json()["making"] is False


def test_do_it_runs_one_turn_as_the_caller_and_reads_what_it_did(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.memory import decision_ledger, session_store

    o = _offer()
    take_on.put_made(ME, [o])
    seen: dict[str, Any] = {}

    async def fake_turn(_request: object, **kw: Any) -> str:
        seen.update(kw)
        return "sess-1"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    r = client.post(f"/take-on/{o.key}/do")
    assert r.status_code == 202 and r.json()["status"] == "working"
    assert "Do exactly this one step" in seen["message"] and "To: dana@client.example" in seen["message"]
    assert client.post(f"/take-on/{o.key}/do").status_code == 409

    monkeypatch.setattr(session_store, "load_messages", lambda _s: [
        {"role": "assistant", "content": "I sent Dana the proposal.",
         "actions": [{"summary": "Emailed Dana", "link": "/sent/1"}]},
    ])
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [])
    seen["on_finished"](True)
    # Done, it leaves the list and shows in Done for you.
    assert client.get("/take-on").json()["offers"] == []
    [got] = take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))
    assert got.status == "done" and got.done == [("Emailed Dana", "/sent/1")]
    assert got.session_id == "sess-1" and got.title == o.title


def test_a_run_that_only_asks_back_keeps_the_offer_with_the_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing done and one question back is not done: the offer stays on
    the list with the question, and the place it came from keeps it."""
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.api.routes import take_on as route
    from openexecutive.memory import decision_ledger, session_store

    o = _offer("I can book a meeting with Dana on Thursday", "meeting")
    take_on.put_made(ME, [o])
    seen: dict[str, Any] = {}
    taken: list[str] = []

    async def fake_turn(_request: object, **kw: Any) -> str:
        seen.update(kw)
        return "sess-ask"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    monkeypatch.setattr(route, "_taken_up", lambda _me, offer: taken.append(offer.key))
    client.post(f"/take-on/{o.key}/do")
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [
        {"role": "assistant", "content": "Dana is free Thursday at 10 and at 2.\n\nWhich time works, 10 or 2?"},
    ])
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [])
    seen["on_finished"](True)
    (got,) = client.get("/take-on").json()["offers"]
    assert got["status"] == "asked" and got["outcome"] == "Which time works, 10 or 2?"
    assert got["session_id"] == "sess-ask"
    assert take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1)) == []
    assert taken == []
    # They can start it again, or hide it.
    assert client.post(f"/take-on/{o.key}/do").status_code == 202


def test_a_doc_written_in_the_chat_is_done() -> None:
    assert route_asked([{"role": "assistant", "content": "Here is your 30-day plan.\n\n1. Week one..."}]) == ""
    assert route_asked([{"role": "assistant", "content": "Want me to send it to **Dana?**"}]) == "Want me to send it to Dana?"


def route_asked(replies: list[dict[str, Any]]) -> str:
    from openexecutive.api.routes import take_on as route

    return route._asked_back(replies)


def test_a_turn_cut_off_after_it_acted_shows_what_it_did(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.memory import decision_ledger, session_store

    o = _offer()
    take_on.put_made(ME, [o])
    seen: dict[str, Any] = {}

    async def fake_turn(_request: object, **kw: Any) -> str:
        seen.update(kw)
        return "sess-cut"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    client.post(f"/take-on/{o.key}/do")
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [
        {"role": "assistant", "content": "I set up the weekly check.",
         "actions": [{"summary": "Saved a workflow", "link": "/jobs/weekly"}]},
    ])
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [])
    seen["on_finished"](False)
    assert client.get("/take-on").json()["offers"] == []
    [got] = take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))
    assert got.done == [("Saved a workflow", "/jobs/weekly")]


def test_a_step_that_went_to_a_card_follows_the_card(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.memory import decision_ledger, session_store

    o = _offer()
    take_on.put_made(ME, [o])
    seen: dict[str, Any] = {}

    async def fake_turn(_request: object, **kw: Any) -> str:
        seen.update(kw)
        return "sess-2"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    client.post(f"/take-on/{o.key}/do")
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [{"role": "assistant", "content": "It waits for you."}])
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [42])
    seen["on_finished"](True)
    statuses = {"s": decision_ledger.STATUS_PROPOSED}
    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {42: SimpleNamespace(status=statuses["s"])})
    got = client.get("/take-on").json()["offers"][0]
    assert got["status"] == "card" and got["decision_id"] == 42
    statuses["s"] = decision_ledger.STATUS_EXECUTED
    assert client.get("/take-on").json()["offers"] == []
    assert [f.key for f in take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))] == [o.key]


@pytest.mark.parametrize("status", ["approved_unchanged", "approved_with_edit"])
def test_an_approved_card_counts_as_done(client: TestClient, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=9)
    monkeypatch.setattr(decision_ledger, "get_decision_instances", lambda ids: {9: SimpleNamespace(status=status)})
    assert client.get("/take-on").json()["offers"] == []
    assert [f.key for f in take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))] == [o.key]


def test_an_offer_that_put_up_two_cards_waits_for_both(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.set_session(ME, o.key, "sess-9")
    take_on.finish(ME, o.key, "card", decision_id=7)
    status = {7: decision_ledger.STATUS_EXECUTED, 8: decision_ledger.STATUS_PROPOSED}
    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {i: SimpleNamespace(status=status[i]) for i in ids})
    monkeypatch.setattr(decision_ledger, "cards_for_session", lambda s: dict(status) if s == "sess-9" else {})
    (got,) = client.get("/take-on").json()["offers"]
    assert got["status"] == "card" and got["decision_id"] == 8
    status[8] = decision_ledger.STATUS_EXECUTED
    assert client.get("/take-on").json()["offers"] == []
    assert [f.key for f in take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))] == [o.key]


def test_skipping_the_last_card_after_one_went_through_is_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.set_session(ME, o.key, "sess-9")
    take_on.finish(ME, o.key, "card", decision_id=8)
    status = {7: decision_ledger.STATUS_EXECUTED, 8: decision_ledger.STATUS_REJECTED}
    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {i: SimpleNamespace(status=status[i]) for i in ids})
    monkeypatch.setattr(decision_ledger, "cards_for_session", lambda s: dict(status) if s == "sess-9" else {})
    told: list[str] = []
    monkeypatch.setattr(route, "_taken_up", lambda _me, off: told.append(off.key))
    assert client.get("/take-on").json()["offers"] == []
    (done,) = take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))
    assert (done.key, done.outcome) == (o.key, "You approved part of it, and that's done.") and told == [o.key]


def test_skipping_a_card_while_another_is_being_sent_follows_that_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.set_session(ME, o.key, "sess-9")
    take_on.finish(ME, o.key, "card", decision_id=10)
    status = {10: decision_ledger.STATUS_REJECTED, 11: decision_ledger.STATUS_EXECUTING}
    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {i: SimpleNamespace(status=status[i]) for i in ids})
    monkeypatch.setattr(decision_ledger, "cards_for_session", lambda s: dict(status) if s == "sess-9" else {})
    told: list[str] = []
    monkeypatch.setattr(route, "_taken_up", lambda _me, off: told.append(off.key))
    (still,) = client.get("/take-on").json()["offers"]
    assert still["status"] == "card"
    status[11] = decision_ledger.STATUS_EXECUTED
    assert client.get("/take-on").json()["offers"] == []
    (done,) = take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))
    assert done.key == o.key and told == [o.key]


def test_a_watch_taken_up_leaves_the_suggestions_as_a_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.monitoring import store as ms
    from openexecutive.monitoring.research import watch_policy

    gone: list[int] = []
    outcomes: list[str] = []
    monkeypatch.setattr(ms, "get_watchlist_item_by_slug", lambda slug: SimpleNamespace(id=5) if slug == "eia" else None)
    monkeypatch.setattr(ms, "delete_pending_suggestion", lambda i: gone.append(i) or True)
    monkeypatch.setattr(watch_policy, "record_outcome_for", lambda _item, outcome, **_k: outcomes.append(outcome))
    offer = take_on.Offer(key="watch:eia", origin="watch", kind="watch", title="I can watch diesel prices",
                          source="News I watch")
    route._taken_up(route.Me(ME, 1, True), offer)
    assert gone == [5] and outcomes == [watch_policy.OUTCOME_APPROVED]
    # Changed by Or tell me into an email: the email went, no watch was made,
    # so the suggestion stays.
    offer.kind = "email"
    route._taken_up(route.Me(ME, 1, True), offer)
    assert gone == [5] and outcomes == [watch_policy.OUTCOME_APPROVED]
    # Changed by Or tell me to watch somewhere else: the one suggested
    # wasn't watched, so it stays too.
    offer.kind, offer.changed, offer.title = "watch", True, "I can watch Adyen's changelog"
    route._taken_up(route.Me(ME, 1, True), offer)
    assert gone == [5] and outcomes == [watch_policy.OUTCOME_APPROVED]


def test_a_repeat_ask_changed_into_a_one_off_stays_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.workflows import repeat_asks

    answered: list[int] = []
    monkeypatch.setattr(repeat_asks, "answer_offer", lambda oid, _pid, accept: answered.append(oid))
    offer = take_on.Offer(key="repeat:5", origin="repeat", kind="doc", title="I can do the summary once",
                          source="Your chats")
    route._taken_up(route.Me(ME, 1, True), offer)
    assert answered == []


def test_the_pass_stays_off_while_the_executive_is_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.scheduler import pause

    monkeypatch.setattr(pause, "is_paused", lambda: True)
    assert take_on.Hooks().background_on() is False
    monkeypatch.setattr(pause, "is_paused", lambda: False)
    assert take_on.Hooks().background_on() is True


async def test_do_it_cancelled_before_the_run_starts_can_start_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.api.routes import take_on as route

    o = _offer()
    take_on.put_made(ME, [o])

    async def cancelled(_request: object, **_kw: Any) -> str:
        raise asyncio.CancelledError

    monkeypatch.setattr(chat_route, "start_background_turn", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await route.do_it(o.key, object())  # type: ignore[arg-type]
    (got,) = _list()
    assert got.status == "failed"
    assert take_on.start(ME, got)


async def test_a_finished_run_settles_off_the_event_loop(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.api.routes import take_on as route
    from openexecutive.memory import session_store

    o = _offer()
    take_on.put_made(ME, [o])
    seen: dict[str, Any] = {}

    async def fake_turn(_request: object, **kw: Any) -> str:
        seen.update(kw)
        return "sess-3"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    client.post(f"/take-on/{o.key}/do")
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [
        {"role": "assistant", "content": "Sent it.", "actions": [{"summary": "Emailed Dana", "link": ""}]},
    ])
    seen["on_finished"](True)
    assert route._settling
    await asyncio.gather(*list(route._settling))
    assert [f.key for f in take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))] == [o.key]


@pytest.mark.parametrize(("status", "heard_it"), [("executed", True), ("rejected", False)])
def test_the_source_hears_only_once_the_card_is_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, status: str, heard_it: bool,
) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=11)
    heard: list[str] = []
    monkeypatch.setattr(route, "_taken_up", lambda _me, offer: heard.append(offer.key))
    monkeypatch.setattr(decision_ledger, "get_decision_instances", lambda ids: {11: SimpleNamespace(status=status)})
    assert client.get("/take-on").json()["offers"] == []
    assert heard == ([o.key] if heard_it else [])


@pytest.mark.parametrize(("reason", "why"), [("not_useful", "not_relevant"), ("already_done", "wrong_source")])
def test_hiding_a_watch_declines_the_suggestion_as_the_watch_list_does(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, reason: str, why: str,
) -> None:
    from openexecutive.monitoring import store as ms
    from openexecutive.monitoring.research import watch_policy

    item = SimpleNamespace(id=5, slug="eia", target="https://www.eia.gov/petroleum/", notes="Diesel prices",
                           route_to_person_id=None)
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: WATCH_REAL(_pid, _p))
    monkeypatch.setattr(ms, "list_pending_suggestions", lambda: [item])
    monkeypatch.setattr(ms, "get_watchlist_item_by_slug", lambda slug: item if slug == "eia" else None)
    declined: list[tuple[int, str, str]] = []
    monkeypatch.setattr(watch_policy, "decline_suggestion",
                        lambda it, r, *, actor: declined.append((it.id, r, actor)) or True)
    assert client.post("/take-on/watch:eia/hide", json={"reason": "later"}).status_code == 204
    assert declined == []
    assert client.post("/take-on/watch:eia/hide", json={"reason": reason}).status_code == 204
    assert declined == [(5, why, ME)]


def test_a_skipped_card_takes_the_offer_away(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=7)
    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {7: SimpleNamespace(status=decision_ledger.STATUS_REJECTED)})
    assert client.get("/take-on").json()["offers"] == []
    assert take_on.turned_down_kinds(ME) == set()


def test_or_tell_me_rewrites_and_checks_it(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.briefing import take_on_maker

    o = _offer()
    take_on.put_made(ME, [o])
    monkeypatch.setattr(take_on_maker, "gather", lambda *a, **k: _inputs())
    replies = [
        {"kind": "email", "title": "I can send Dana the proposal and ask for a start date",
         "preview": [{"label": "To", "value": "dana@client.example"}, {"label": "Asks", "value": "a start date"}]},
        {"kind": "email", "title": "I can send it to Eve", "preview": [{"label": "To", "value": "eve@else.example"}]},
    ]

    async def fake_rewrite(_offer: object, _said: str) -> Any:
        return replies.pop(0)

    monkeypatch.setattr(route, "_rewrite", fake_rewrite)
    r = client.post(f"/take-on/{o.key}/say", json={"text": "also ask for a start date"})
    assert r.status_code == 200
    assert r.json()["changed"] and r.json()["said"] == "also ask for a start date"
    assert client.post(f"/take-on/{o.key}/say", json={"text": "send it to eve"}).status_code == 422
    assert not client.post(f"/take-on/{o.key}/undo").json()["changed"]


def test_or_tell_me_started_elsewhere_while_rewriting_leaves_the_step_as_it_was(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.briefing import take_on_maker

    o = _offer()
    take_on.put_made(ME, [o])
    monkeypatch.setattr(take_on_maker, "gather", lambda *a, **k: _inputs())

    async def rewrite_while_do_it_starts(_offer: object, _said: str) -> Any:
        take_on.start(ME, o)  # Do it, pressed on another screen meanwhile
        return {"kind": "email", "title": "I can send Dana the proposal next week",
                "preview": [{"label": "To", "value": "dana@client.example"}]}

    monkeypatch.setattr(route, "_rewrite", rewrite_while_do_it_starts)
    assert client.post(f"/take-on/{o.key}/say", json={"text": "make it next week"}).status_code == 409
    (got,) = client.get("/take-on").json()["offers"]
    assert got["status"] == "working" and got["title"] == o.title and not got["changed"]


def test_or_tell_me_is_never_a_repeat_of_the_offer_it_changes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The list's own record of what is offered holds the offer being changed:
    # "make it shorter" is about the same thing, and still goes through. One
    # close to another offer on the list is still a repeat.
    from openexecutive.api.routes import take_on as route
    from openexecutive.briefing import take_on_maker

    o, other = _offer(), _apart(1)
    take_on.put_made(ME, [o, other])

    def gather(*_a: object, **_k: object) -> maker.Inputs:
        inputs = _inputs("INBOUND\n- Dana <dana@client.example> sent the proposal\n- priya@client.example")
        inputs.already = take_on.about_already(ME)
        return inputs

    monkeypatch.setattr(take_on_maker, "gather", gather)
    replies = [
        {"kind": "email", "title": "I can send Dana the signed proposal, short, and offer a quick call",
         "preview": [{"label": "To", "value": "dana@client.example"}, {"label": "Attach", "value": "Proposal.pdf"}]},
        {"kind": "email", "title": "I can send Priya the March invoice",
         "preview": [{"label": "To", "value": "priya@client.example"}, {"label": "Attach", "value": "Invoice.pdf"}]},
    ]

    async def fake_rewrite(_offer: object, _said: str) -> Any:
        return replies.pop(0)

    monkeypatch.setattr(route, "_rewrite", fake_rewrite)
    r = client.post(f"/take-on/{o.key}/say", json={"text": "make it shorter and offer a quick call"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] and "quick call" in r.json()["title"]
    assert client.post(f"/take-on/{o.key}/say", json={"text": "make it about the invoice"}).status_code == 422


def test_or_tell_me_on_a_watch_already_watched_still_changes_it() -> None:
    # The watch it offers checks a page; changing how often or what it tells
    # them is still that watch, not one "already running on its own".
    page = "https://status.example.com"
    o = take_on.Offer(
        key="watch:status", origin="watch", kind="watch", title="I can watch the payments status page",
        source="News I watch", preview=[("Checks", page), ("Tells you", "when something big changes")],
    )
    inputs = _inputs()
    inputs.own = maker.OnItsOwn(watched=["status.example.com"])
    item = {"kind": "watch", "title": "I can check the payments status page weekly",
            "preview": [{"label": "Checks", "value": page}, {"label": "Tells you", "value": "only big changes"}]}
    assert maker.check([item], inputs, set()) == []
    kept = maker.check([item], inputs, set(), changing=o)
    assert [x.title for x in kept] == [item["title"]]
    # Turned to another page that is watched already: still on its own.
    inputs.own = maker.OnItsOwn(watched=["status.example.com", "other.example.com"])
    other = {"kind": "watch", "title": "I can watch the other status page",
             "preview": [{"label": "Checks", "value": "https://other.example.com"}]}
    assert maker.check([other], inputs, set(), changing=o) == []


def test_hide_asks_why_and_an_unknown_key_is_gone(client: TestClient) -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    assert client.post(f"/take-on/{o.key}/hide", json={"reason": "bogus"}).status_code == 422
    assert client.post(f"/take-on/{o.key}/hide", json={"reason": "not_useful"}).status_code == 204
    assert client.get("/take-on").json()["offers"] == []
    assert client.post("/take-on/made:nope/do").status_code == 404
    assert client.post("/take-on/../etc/do").status_code == 404


@pytest.mark.parametrize(("reason", "told"), [("later", []), ("not_useful", [5])])
def test_later_on_a_repeat_ask_keeps_it_and_a_turn_down_retires_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, reason: str, told: list[int],
) -> None:
    from openexecutive.api.routes import take_on as route
    from openexecutive.workflows import repeat_asks

    offer = take_on.Offer(key="repeat:5", origin="repeat", kind="workflow", title="I can do the Monday numbers",
                          source="Your chats")
    monkeypatch.setattr(take_on, "_repeat", lambda _pid: [offer])
    monkeypatch.setattr(route, "_me", lambda _r: route.Me(ME, 1, True))
    answered: list[int] = []
    monkeypatch.setattr(repeat_asks, "answer_offer", lambda oid, _pid, accept: answered.append(oid) or True)
    assert client.post("/take-on/repeat:5/hide", json={"reason": reason}).status_code == 204
    assert answered == told


def test_hide_on_a_card_already_off_the_list_just_takes_it_away(client: TestClient) -> None:
    # Put off with Later on one screen, then Hide pressed on another that
    # still showed it: no error, and the newer reason sticks.
    o = _offer()
    take_on.put_made(ME, [o])
    assert client.get("/take-on").json()["offers"]
    assert client.post(f"/take-on/{o.key}/hide", json={"reason": "later"}).status_code == 204
    assert client.post(f"/take-on/{o.key}/hide", json={"reason": "not_useful"}).status_code == 204
    back = datetime.now(UTC) + timedelta(days=take_on.LATER_DAYS, hours=1)
    assert _list(now=back) == []
    # One that was never on the list is gone too, quietly.
    assert client.post("/take-on/made:nope/hide", json={"reason": "later"}).status_code == 204


def test_hide_on_a_stale_card_leaves_a_finished_step_as_it_was(client: TestClient) -> None:
    o = _offer("I can chase the invoice", "followup")
    take_on.put_made(ME, [o])
    got = _list()[0]
    assert take_on.start(ME, got)
    take_on.finish(ME, got.key, "done", outcome="Sent it.")
    assert client.post(f"/take-on/{o.key}/hide", json={"reason": "not_useful"}).status_code == 204
    row = take_on._states(take_on.who(ME), None)[o.key]
    assert (row["status"], row["hidden_reason"]) == ("done", "")


def test_gather_reads_an_empty_company_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # The live signals read the audit log, which other tests in the run write to.
    monkeypatch.setattr(maker, "_live", lambda *_a: ([], False, [], []))
    monkeypatch.setattr(maker, "_goals", lambda: [])
    inputs = maker.gather(ME, None, principal=True)
    assert "CONNECTED APPS" in inputs.text
    assert not maker.has_material(inputs)


def test_a_message_step_is_never_a_note_to_the_person_themself(monkeypatch: pytest.MonkeyPatch) -> None:
    # 11-25: Reese was offered "I can send Reese Cole a note", and Do it
    # ended in "I didn't send it: the note was addressed to you".
    monkeypatch.setattr(maker, "_live", lambda *_a: ([], False, [], []))
    monkeypatch.setattr(maker, "_goals", lambda: [])
    monkeypatch.setattr(maker, "_team", lambda: [("Olivia Park", ME), ("Sam Lee", "sam@co.example")])
    monkeypatch.setattr(maker, "person_for", lambda *_a: SimpleNamespace(id=1, full_name="Olivia Park", email=ME, role="", kind="team"))
    inputs = maker.gather(ME, 1, principal=True)
    assert inputs.team == [("Sam Lee", "sam@co.example")]
    to_me = {"kind": "message", "title": "I can send Olivia Park a note", "preview": [{"label": "To", "value": "Olivia Park"}]}
    to_sam = {"kind": "message", "title": "I can send Sam a note", "preview": [{"label": "To", "value": "Sam Lee"}]}
    assert [o.title for o in maker.check([to_me, to_sam], inputs, set())] == ["I can send Sam a note"]


def test_a_run_left_working_can_be_started_again(monkeypatch: pytest.MonkeyPatch) -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    assert take_on.start(ME, o)
    assert not take_on.start(ME, o)
    later = datetime.now(UTC) + background_turn_limit() + timedelta(minutes=1)
    monkeypatch.setattr(take_on, "_now", lambda: later)
    assert take_on.start(ME, o)


def test_app_check_findings_go_to_the_principals_pass_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maker, "_live", lambda *_a: ([], False, [], []))
    monkeypatch.setattr(maker, "_goals", lambda: [])
    monkeypatch.setattr(take_on.hooks, "apps", lambda principal: (["Deals"], ["Deals: Acme renewal at risk"]))
    assert "Acme renewal" in maker.gather(ME, None, principal=True).text
    assert "Acme renewal" not in maker.gather(ME, None, principal=False).text


def test_a_card_closed_another_way_ends_the_offer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=8)
    monkeypatch.setattr(decision_ledger, "get_decision_instances",
                        lambda ids: {8: SimpleNamespace(status=decision_ledger.STATUS_AUTO_NO_RESPONSE)})
    got = client.get("/take-on").json()["offers"][0]
    assert got["status"] == "failed"
    assert client.post(f"/take-on/{o.key}/do").status_code != 409


def test_a_card_that_is_gone_ends_the_offer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.memory import decision_ledger

    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    take_on.finish(ME, o.key, "card", decision_id=8)
    monkeypatch.setattr(decision_ledger, "get_decision_instances", lambda ids: {})
    (got,) = client.get("/take-on").json()["offers"]
    assert got["status"] == "failed" and got["outcome"] == "The card closed before it was done."


def test_a_repeat_offer_shows_what_came_of_it_and_is_taken_up_only_when_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.memory import decision_ledger, session_store
    from openexecutive.workflows import repeat_asks

    rep = take_on.Offer(key="repeat:5", origin="repeat", kind="workflow",
                        title="I can do the pipeline summary every Monday", source="Your chats")
    source = {"open": [rep]}
    # A fresh offer on each read, as the repeat asks give.
    monkeypatch.setattr(take_on, "_repeat", lambda _pid: [copy.copy(o) for o in source["open"]])
    answered: list[bool] = []

    def answer(_id: int, _pid: int, *, accept: bool) -> None:
        answered.append(accept)
        source["open"] = []

    monkeypatch.setattr(repeat_asks, "answer_offer", answer)
    turns: list[dict[str, Any]] = []

    async def fake_turn(_request: object, **kw: Any) -> str:
        turns.append(kw)
        return f"sess-r{len(turns)}"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [])
    assert client.post("/take-on/repeat:5/do").status_code == 202
    assert answered == []
    # It stopped: still there, with Try again.
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [])
    turns[0]["on_finished"](False)
    assert client.get("/take-on").json()["offers"][0]["status"] == "failed" and answered == []
    assert client.post("/take-on/repeat:5/do").status_code == 202
    # It only answered in words: nothing was set up, so the ask stays on the
    # list with what it said, out of Done for you, and isn't retired.
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [
        {"role": "assistant", "content": "I couldn't find last week's pipeline numbers."},
    ])
    turns[1]["on_finished"](True)
    (got,) = client.get("/take-on").json()["offers"]
    assert answered == [] and (got["status"], got["outcome"]) == ("asked", "I couldn't find last week's pipeline numbers.")
    assert take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1)) == []
    assert client.post("/take-on/repeat:5/do").status_code == 202
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [
        {"role": "assistant", "content": "It runs every Monday now.",
         "actions": [{"summary": "Made the Monday pipeline summary", "link": "/workflows"}]},
    ])
    turns[2]["on_finished"](True)
    assert answered == [True]
    assert client.get("/take-on").json()["offers"] == []
    got = take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))
    assert [(g.key, g.status, g.outcome) for g in got] == [("repeat:5", "done", "It runs every Monday now.")]


def test_the_reply_is_read_whatever_is_around_the_array() -> None:
    item = {"kind": "doc", "title": "I can write the plan", "preview": [{"label": "Doc", "value": "Plan"}]}
    body = json.dumps([item])
    for raw in (body, f"```json\n{body}\n```", f"Here are [2] ideas:\n{body}\nThat's all [done].", "[] nothing"):
        got = maker._parse(raw)
        assert got == ([] if raw.startswith("[]") else [item]), raw
    assert maker._parse("no array here") == []
    assert maker._parse(f"Skipped: []\n{body}") == [item]
    # Cut off mid-array: an offer's own preview is never taken for the offers.
    assert maker._parse(body[:-1] + ', {"kind": "email", "ti') == []


def test_check_counts_why_each_offer_was_left_out() -> None:
    dropped: dict[str, int] = {}
    items = [
        {"kind": "email", "title": "I can email x", "preview": [{"label": "To", "value": "x@nowhere.example"}]},
        {"kind": "meeting", "title": "I can book it", "preview": [{"label": "With", "value": "Dana"}]},
        {"kind": "nope", "title": "x", "preview": [{"label": "a", "value": "b"}]},
    ]
    assert maker.check(items, _inputs(), set(), dropped) == []
    assert dropped == {"address not in the data": 1, "meeting without a calendar": 1, "unknown kind": 1}


async def test_a_read_that_finds_nothing_new_stops_the_list_looking_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.briefing import take_on_maker

    monkeypatch.setattr(take_on.hooks, "background_on", lambda: True)
    monkeypatch.setattr(take_on_maker, "gather", lambda *a, **k: take_on_maker.Inputs("", calendar=False, apps=[], team=[]))
    monkeypatch.setattr(take_on_maker, "due", lambda *a, **k: False)
    monkeypatch.setattr(take_on_maker, "_checked", {})
    assert take_on_maker.schedule(ME, 1, principal=True) is True
    await asyncio.gather(*list(take_on_maker._tasks))
    assert take_on_maker.making(ME) is False
    assert take_on_maker.schedule(ME, 1, principal=True) is False


def test_a_short_list_is_topped_up_within_the_hour(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(take_on.hooks, "background_on", lambda: True)
    take_on.record_pass(ME, _inputs().hash)
    changed = _inputs(_inputs().text + "\n\nALREADY\n- set aside for later: I can draft the plan")
    assert not maker.due(ME, changed, short=True)  # just ran
    later = datetime.now(UTC) + timedelta(hours=1, minutes=1)
    assert maker.due(ME, changed, later, short=True)  # something was used up, list short
    assert not maker.due(ME, changed, later)  # a full list waits for the day
    assert not maker.due(ME, _inputs(), later, short=True)  # nothing changed: no new call
    assert not maker.due(ME, _inputs("COMPANY: Acme\n\nCONNECTED APPS\n- none"), later, short=True)


def test_the_pass_is_told_what_is_on_the_list_and_set_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    shown, later, done = _offer("I can draft the plan", "doc"), _offer("I can book the review", "meeting"), _offer()
    take_on.put_made(ME, [shown, later, done])
    _list()
    take_on.hide(ME, later, "later")
    take_on.start(ME, done)
    take_on.finish(ME, done.key, "done")
    seen = take_on.recent_titles(ME)
    assert seen["offered"] == [shown.title]
    assert seen["later"] == [later.title]
    assert seen["done"] == [done.title]
    # Back from Later after its week: on the list again, so offered.
    week = datetime.now(UTC) + timedelta(days=take_on.LATER_DAYS, hours=1)
    monkeypatch.setattr(take_on, "_now", lambda: week)
    back = take_on.recent_titles(ME)
    assert later.title in back["offered"] and back["later"] == []


def test_the_list_asks_for_a_top_up_when_short(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.briefing import take_on_maker

    asked: list[bool] = []
    monkeypatch.setattr(take_on_maker, "schedule", lambda *a, **k: asked.append(k["short"]) or False)
    client.get("/take-on")
    take_on.put_made(ME, [_apart(i) for i in range(3)])
    client.get("/take-on")
    assert asked == [True, False]


# --------------------------------------------------------------------------- #
# A mixed list, read from more of the person's own work
# --------------------------------------------------------------------------- #


def test_a_pass_keeps_at_most_two_of_one_kind_counting_the_list() -> None:
    docs = [_offer(f"I can write plan {i}", "doc") for i in range(3)]
    mail = _offer("I can send Dana the proposal", "email")
    dropped: dict[str, int] = {}
    kept = maker.mixed([*docs, mail], ["doc"], dropped)
    assert [o.title for o in kept] == ["I can write plan 0", mail.title]
    assert dropped == {"too many of one kind": 2}
    assert [o.kind for o in maker.mixed(docs, [])] == ["doc", "doc"]


def test_the_pass_is_told_each_listed_kind_and_to_prefer_steps_that_act(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maker, "_live", lambda *_a: ([], False, [], []))
    monkeypatch.setattr(maker, "_goals", lambda: [])
    take_on.put_made(ME, [_offer("I can draft the plan", "doc")])
    inputs = maker.gather(ME, None, principal=True)
    assert "- on the list now (Doc): I can draft the plan" in inputs.text
    assert inputs.listed == ["doc"]
    assert take_on.on_list(ME) == [("doc", "I can draft the plan")]
    assert "Prefer steps that do something" in maker.SYSTEM
    assert f"never more than {maker.PER_KIND} of one kind" in maker.SYSTEM


def test_the_pass_reads_inside_the_persons_own_recent_chats(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.memory import session_store

    episodic.initialize_db(db)
    now = datetime.now(UTC)
    session_store.create_session("s-mine", "Acme proposal", now.isoformat(), caller_person_id=1, db_path=db)
    session_store.save_message("s-mine", "user", "Remind me to send Dana the signed proposal Friday", db_path=db)
    session_store.save_message("s-mine", "assistant", "I'll keep that in mind.", db_path=db)
    session_store.create_session("s-theirs", "Payroll", now.isoformat(), caller_person_id=2, db_path=db)
    session_store.save_message("s-theirs", "user", "Raise for Sam", db_path=db)
    # A teammate's lines in a chat Olivia shared are not hers, nor the reply to them.
    session_store.save_message("s-mine", "user", "Send Dana the Q3 deck", db_path=db, sender_person_id=2)
    session_store.save_message("s-mine", "assistant", "Sent the deck.", db_path=db)
    old = (now - timedelta(days=5)).isoformat()
    session_store.create_session("s-old", "Old", old, caller_person_id=1, db_path=db)
    session_store.save_message("s-old", "user", "Something from last week", db_path=db)
    with episodic._get_conn(db) as conn:
        conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = 's-old'", (old,))
    lines = maker._chats(SimpleNamespace(id=1), now)
    assert lines == [
        "- Acme proposal\n  You: Remind me to send Dana the signed proposal Friday\n  Executive: I'll keep that in mind."
    ]
    assert maker._chats(None, now) == []
    monkeypatch.setattr(maker, "_live", lambda *_a: ([], False, [], []))
    monkeypatch.setattr(maker, "_goals", lambda: [])
    monkeypatch.setattr(maker, "person_for", lambda *_a: SimpleNamespace(id=1, full_name="Olivia", role=""))
    inputs = maker.gather(ME, 1, principal=True)
    assert "YOUR RECENT CHATS" in inputs.text and "Raise for Sam" not in inputs.text
    assert maker.has_material(inputs)


def test_the_pass_reads_what_became_of_the_persons_cards() -> None:
    """Test lab 11-23: an offer said the decal email was "still waiting on your
    approval" four hours after it was approved and sent. The pass reads each
    of the person's cards and what became of it; someone else's stay out."""
    from openexecutive.memory import decision_ledger

    episodic.initialize_db()

    def card(to: str, subject: str, approver: int) -> int:
        return decision_ledger.create_decision_instance(
            decision_class="take_the_lead_action", department="", originating_session_id=None,
            proposed_payload={"held": True, "tool": "send_email", "input": {"to": to, "subject": subject}},
            idempotency_key=None, gate_mode="propose", approver_person_id=approver, confidence=None,
        )

    sent = card("drew@signs.example", "Window decal quote", 4)
    decision_ledger.mark_resolved(sent, decision_ledger.STATUS_APPROVED_UNCHANGED, resolver_person_id=4)
    card("pat@client.example", "Your two baskets", 4)
    skipped = card("sam@client.example", "Catch-up", 4)
    decision_ledger.mark_resolved(skipped, decision_ledger.STATUS_REJECTED, resolver_person_id=4)
    card("someone@else.example", "Not Casey's", 7)

    lines = maker._cards(SimpleNamespace(id=4), datetime.now(UTC))

    assert len(lines) == 3
    assert any("Window decal quote" in x and x.endswith(": done after your yes") for x in lines)
    assert any("Your two baskets" in x and x.endswith(": waiting on your OK") for x in lines)
    assert any("Catch-up" in x and x.endswith(": you skipped it") for x in lines)
    assert not any("Not Casey's" in x for x in lines)
    assert maker._cards(None, datetime.now(UTC)) == []
    assert "YOUR CARDS" in maker.SYSTEM


def test_a_suggested_watch_is_an_offer_and_one_routed_to_a_head_is_theirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A watch the research pass would like to start is on I can also take
    on, not a Needs you row; one routed to a department's head only on theirs,
    the rest only on the principal's."""
    from openexecutive.monitoring import store as ms

    item = SimpleNamespace(id=1, slug="eia", target="https://www.eia.gov/petroleum/", notes="Diesel prices",
                           route_to_person_id=None)
    routed = SimpleNamespace(id=2, slug="ports", target="Port delays", notes="", route_to_person_id=7)
    monkeypatch.setattr(ms, "list_pending_suggestions", lambda: [item, routed])
    assert [o.key for o in WATCH(1, True)] == ["watch:eia"]
    assert [o.key for o in WATCH(7, False)] == ["watch:ports"]
    assert WATCH(3, False) == []
    assert WATCH(1, True)[0].title == "I can watch Diesel prices"
    # A name keeps its letters.
    item.notes = "AWS status page"
    assert WATCH(1, True)[0].title == "I can watch AWS status page"
    # A note that is a sentence: the title names the page, the note is Why.
    item.notes = "Stripe Apple Pay errors on Nov 17 went unseen by uptime checks. Payments need a watch."
    (o,) = WATCH(1, True)
    assert o.title == "I can watch eia.gov/petroleum"
    assert ("Why", item.notes) in o.preview


# --------------------------------------------------------------------------- #
# Never offer what it already does on its own
# --------------------------------------------------------------------------- #

_INBOX_LINE = '[09:12] from Kyle Harrelson (kyle@lp.example): "Catoosa closing"'
_WAITING_LINE = "to Tara Spivey (tara@title.example), ops@lp.example, sent 3 days ago (Fri 20 Nov), no reply yet: Wiring"


def _own_inputs(own: maker.OnItsOwn) -> maker.Inputs:
    text = "YOUR INBOX\n- " + _INBOX_LINE + "\nINBOUND\n- Dana <dana@client.example> sent the proposal"
    return maker.Inputs(text, calendar=False, apps=[], team=[], own=own,
                        senders=maker._senders([_INBOX_LINE]), unanswered=maker._people([_WAITING_LINE]))


_OVERLAPS = [
    {"kind": "email", "title": "I can reply to Kyle on the Catoosa thread", "source": "Your inbox",
     "preview": [{"label": "To", "value": "kyle@lp.example"}]},
    {"kind": "email", "title": "I can confirm what the closing team needs", "source": "Your inbox",
     "preview": [{"label": "To", "value": "Kyle Harrelson"}]},
    {"kind": "followup", "title": "I can check whether Tara confirmed the wiring", "source": "Your inbox",
     "preview": [{"label": "To", "value": "Tara"}]},
    {"kind": "watch", "title": "I can keep an eye on the Stripe changelog", "source": "Stripe",
     "preview": [{"label": "Checks", "value": "stripe.com/changelog"}]},
    {"kind": "watch", "title": "I can watch Acme's status page", "source": "Your inbox",
     "preview": [{"label": "Checks", "value": "status.acme.example"}]},
    {"kind": "email", "title": "I can ask Al Moss about Q1", "source": "Your inbox",
     "preview": [{"label": "To", "value": "Alice Moss"}]},
    {"kind": "email", "title": "I can send Dana the signed proposal", "source": "Your inbox",
     "preview": [{"label": "To", "value": "dana@client.example"}]},
    {"kind": "followup", "title": "I can chase Sam for the deck he promised in today's meeting", "source": "Your call",
     "preview": [{"label": "To", "value": "Sam"}]},
]


def test_inbox_senders_are_read_from_the_inbox_lines() -> None:
    no_reply = '[11:00] from Dana (dana@client.example): "FYI" — no reply needed'
    assert maker._senders([_INBOX_LINE, '[10:00] from sam@co.example: "Hi"', no_reply, "nonsense"]) == [
        ("Kyle Harrelson", "kyle@lp.example"), ("", "sam@co.example"),
    ]


def test_who_unanswered_emails_went_to_is_read_from_the_waiting_lines() -> None:
    assert maker._people([_WAITING_LINE, "to Sam, Drew and 2 more, sent today at 9:00, no reply yet: x"]) == [
        ("Tara Spivey", "tara@title.example"), ("", "ops@lp.example"),
    ]


WATCHED = maker._watched_terms("https://www.stripe.com/changelog/")


def test_a_watched_page_is_named_as_written_and_by_its_address() -> None:
    assert WATCHED == ["https://www.stripe.com/changelog/", "stripe.com/changelog"]
    assert maker._watched_terms("AI") == []


def test_nothing_it_does_on_its_own_is_offered() -> None:
    dropped: dict[str, int] = {}
    kept = maker.check(_OVERLAPS, _own_inputs(maker.OnItsOwn(inbox=True, watched=WATCHED)), set(), dropped)
    # A follow-up on a promise, a page nobody watches yet, and an email to
    # someone whose name only starts like a sender's all stay.
    assert [o.title for o in kept] == [
        "I can watch Acme's status page", "I can ask Al Moss about Q1", "I can send Dana the signed proposal",
        "I can chase Sam for the deck he promised in today's meeting",
    ]
    assert dropped == {"already on its own": 4}


def test_with_those_off_the_same_steps_are_offered() -> None:
    dropped: dict[str, int] = {}
    kept = maker.check(_OVERLAPS, _own_inputs(maker.OnItsOwn()), set(), dropped)
    assert len(kept) == maker.MAX_OFFERS and dropped == {}
    only_watching = maker.check(_OVERLAPS, _own_inputs(maker.OnItsOwn(watched=WATCHED)), set())
    assert [o.title for o in only_watching if o.kind == "watch"] == ["I can watch Acme's status page"]


def test_the_pass_is_told_what_runs_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maker, "_live", lambda *_a: (["YOUR INBOX\n- " + _INBOX_LINE], False, [], []))
    monkeypatch.setattr(maker, "_apps", lambda _p: ([], []))
    monkeypatch.setattr(maker, "on_its_own", lambda _p: maker.OnItsOwn(inbox=True, watched=WATCHED))
    inputs = maker.gather(ME, None, principal=True)
    assert "ON ITS OWN" in inputs.text
    for line in (maker.REPLIES, maker.FOLLOW_UPS, maker.WATCHING):
        assert f"- {line}" in inputs.text
    assert inputs.own.inbox and inputs.own.watching
    assert "ON ITS OWN" in maker.SYSTEM


def test_what_runs_on_its_own_comes_from_as_you_and_the_watches(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.delegation import handle_it
    from openexecutive.monitoring import store as watch_store

    seen: list[int | None] = []

    def watchlist(**kw: Any) -> list[Any]:
        seen.append(kw.get("for_person"))
        return [SimpleNamespace(target="https://stripe.com/changelog")]

    monkeypatch.setattr(handle_it, "get", lambda pid, **_k: handle_it.HandleIt(person_id=pid, enabled=pid == 1))
    monkeypatch.setattr(watch_store, "list_watchlist", watchlist)
    monkeypatch.setattr(take_on.hooks, "watching_on", lambda: True)
    assert ON_ITS_OWN(SimpleNamespace(id=1)).lines() == [maker.REPLIES, maker.FOLLOW_UPS, maker.WATCHING]
    own = ON_ITS_OWN(SimpleNamespace(id=2))
    assert own.lines() == [maker.WATCHING] and own.watches("keep an eye on stripe.com/changelog")
    assert seen == [1, 2]
    # Watches don't run while the watch list isn't checked, and nothing is watched without a watch.
    monkeypatch.setattr(take_on.hooks, "watching_on", lambda: False)
    assert ON_ITS_OWN(SimpleNamespace(id=2)).lines() == []
    monkeypatch.setattr(take_on.hooks, "watching_on", lambda: True)
    monkeypatch.setattr(watch_store, "list_watchlist", lambda **_k: [])
    assert ON_ITS_OWN(None).lines() == []


def test_the_list_says_what_runs_on_its_own(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maker, "on_its_own", lambda _p: maker.OnItsOwn(inbox=True))
    monkeypatch.setattr(maker, "person_for", lambda *_a: SimpleNamespace(id=1))
    assert client.get("/take-on").json()["on_its_own"] == [maker.REPLIES, maker.FOLLOW_UPS]


# --------------------------------------------------------------------------- #
# The same idea in other words is not offered again
# --------------------------------------------------------------------------- #

# What came back on one company's list in a day, each time in new words or as
# another kind of step, after Not useful.
_ALEX = take_on.Offer(
    key=take_on.made_key("I can email Alex Cohen to confirm the sentelabsai repo move"), origin="made", kind="email",
    title="I can email Alex Cohen to confirm the sentelabsai repo move is ready and ask about the Hysen Labs link",
    source="Your chats", preview=[("To", "alex@sentelabs.ai"), ("Subject", "Repo move and Hysen Labs review link")],
)
_ALEX_AGAIN = {
    "kind": "message",
    "title": "I can message Alex Cohen to ask whether the sentelabsai repo move and the Hysen Labs review link are ready",
    "source": "Your chats",
    "preview": [{"label": "To", "value": "Alex Cohen"},
                {"label": "Says", "value": "Are the sentelabsai repo move and the Hysen Labs review link ready?"}],
}
_APPROVALS = take_on.Offer(
    key=take_on.made_key("I can check the Admin approvals"), origin="made", kind="app",
    title="I can check the Admin approvals and update the sign-up count against the Q4 target of 100",
    source="Your goal", preview=[("App", "Admin"), ("Change", "Update tracked sign-up count to match approvals")],
)
_APPROVALS_AGAIN = {
    "kind": "watch",
    "title": "I can watch the Admin approvals and tell you when sign-ups reach the Q4 target of 100",
    "source": "Your goal",
    "preview": [{"label": "Checks", "value": "Admin approved user count against the Q4 target of 100"}],
}
_OTHER = {
    "kind": "message", "title": "I can ask Sam Lee for the hiring plan draft before Friday", "source": "Your chats",
    "preview": [{"label": "To", "value": "Sam Lee"}, {"label": "Says", "value": "Could you send the hiring plan draft?"}],
}


def test_close_to_matches_the_same_idea_in_other_words_only() -> None:
    alex = take_on.about(_ALEX.title, _ALEX.preview)
    again = take_on.about(_ALEX_AGAIN["title"], [("To", "Alex Cohen"), ("Says", "Are the repo move and link ready?")])
    assert take_on.close_to(again, [alex])
    assert not take_on.close_to(take_on.about(_OTHER["title"]), [alex])
    assert not take_on.close_to(again, [])


def test_the_pass_drops_an_idea_turned_down_in_other_words_or_as_another_kind() -> None:
    take_on.put_made(ME, [_ALEX, _APPROVALS])
    for o in _list():
        take_on.hide(ME, o, "not_useful" if o.key == _ALEX.key else "already_done")
    dropped: dict[str, int] = {}
    inputs = maker.Inputs(
        "TEAM\n- Alex Cohen <alex@sentelabs.ai>\n- Sam Lee <sam@co.example>", calendar=False, apps=[],
        team=[("Alex Cohen", "alex@sentelabs.ai"), ("Sam Lee", "sam@co.example")],
        already=take_on.about_already(ME),
    )
    kept = maker.check([_ALEX_AGAIN, _APPROVALS_AGAIN, _OTHER, dict(_OTHER, title=_OTHER["title"] + " please")],
                       inputs, set(), dropped)
    assert [o.title for o in kept] == [_OTHER["title"]]
    assert dropped == {"same as one already offered": 2, "repeat": 1}


def test_the_list_never_shows_an_idea_turned_down_in_other_words() -> None:
    take_on.put_made(ME, [_ALEX])
    take_on.hide(ME, _list()[0], "not_useful")
    # Made before this rule: the same idea in other words, a near copy of it,
    # and something else.
    again = take_on.Offer(key=take_on.made_key(_ALEX_AGAIN["title"]), origin="made", kind="message",
                          title=_ALEX_AGAIN["title"], source="Your chats", preview=[("To", "Alex Cohen")])
    approvals = take_on.Offer(key=take_on.made_key(_APPROVALS_AGAIN["title"]), origin="made", kind="watch",
                              title=_APPROVALS_AGAIN["title"], source="Your goal",
                              preview=[("Checks", "Admin approved user count against the Q4 target of 100")])
    take_on.put_made(ME, [again, approvals, _APPROVALS])
    titles = [o.title for o in _list()]
    assert _ALEX_AGAIN["title"] not in titles
    # Two about one thing show once.
    assert len(titles) == 1 and "Admin approvals" in titles[0]


def test_hide_remembers_what_an_offer_from_anywhere_was_about(monkeypatch: pytest.MonkeyPatch) -> None:
    watch = take_on.Offer(key="watch:stripe-changelog", origin="watch", kind="watch",
                          title="I can watch the Stripe changelog and pricing diffs for billing changes",
                          source="News I watch", preview=[("Checks", "docs.stripe.com/changelog pricing diffs")])
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: [watch])
    take_on.hide(ME, _list()[0], "not_useful")
    monkeypatch.setattr(take_on, "_watch", lambda _pid, _p: [])
    assert any("Stripe changelog" in t for t in take_on.about_already(ME))
    take_on.put_made(ME, [_offer("I can run a weekly check of the Stripe changelog and pricing diffs for billing", "workflow")])
    assert _list() == []


def test_an_idea_turned_down_many_times_still_never_comes_back() -> None:
    aside = [
        "I can email Alex Cohen to confirm the sentelabsai repo move is ready Hysen Labs link",
        "I can message Alex Cohen about the sentelabsai repo move and Hysen Labs review link",
        "I can ask Alex Cohen whether the sentelabsai repo move and Hysen Labs link landed",
    ]
    new = "I can follow up with Alex Cohen on the sentelabsai repo move and Hysen Labs review link"
    for n in range(1, len(aside) + 1):
        assert take_on.close_to(new, aside[:n])
    assert take_on.close_to(new, aside + ["I can send Priya the March invoice", "Stripe changelog pricing diffs"])


# --------------------------------------------------------------------------- #
# Plug-in points: another place offers come from, and what the pass reads
# --------------------------------------------------------------------------- #


@pytest.fixture
def setup_moves(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A registered source: setup moves for the main person only."""
    seen: dict[str, Any] = {"open": ["a1"], "taken": [], "aside": [], "asked": []}

    def read(person_id: int | None, principal: bool) -> list[take_on.Offer]:
        seen["asked"].append((person_id, principal))
        return [
            take_on.Offer(key=f"setup:{m}", origin="setup", kind="doc", title="I can write your 30-day plan",
                          source="What I found after setup", preview=[("Doc", "30-day plan")])
            for m in seen["open"]
        ] if principal else []

    def taken(key: str) -> None:
        seen["taken"].append(key)
        seen["open"] = []

    monkeypatch.setattr(take_on, "_sources", {})
    take_on.register_source(take_on.Source(
        "setup", read, taken_up=taken, set_aside=lambda key: seen["aside"].append(key),
    ))
    return seen


def test_a_registered_source_offers_to_whom_it_says(setup_moves: dict[str, Any]) -> None:
    assert [o.key for o in _list()] == ["setup:a1"]
    assert take_on.offers_for("sam@co.example", 2, principal=False) == []
    assert setup_moves["asked"] == [(1, True), (2, False)]


def test_a_source_hears_its_offer_was_done_or_set_aside(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, setup_moves: dict[str, Any],
) -> None:
    from openexecutive.api.routes import chat as chat_route
    from openexecutive.memory import decision_ledger, session_store

    turns: list[dict[str, Any]] = []

    async def fake_turn(_request: object, **kw: Any) -> str:
        turns.append(kw)
        return "sess-s1"

    monkeypatch.setattr(chat_route, "start_background_turn", fake_turn)
    monkeypatch.setattr(decision_ledger, "open_for_session", lambda _s: [])
    monkeypatch.setattr(session_store, "load_messages", lambda _s: [{"role": "assistant", "content": "Here's the plan."}])
    assert client.post("/take-on/setup:a1/do").status_code == 202
    assert setup_moves["taken"] == []
    turns[0]["on_finished"](True)
    assert setup_moves["taken"] == ["setup:a1"]
    # A new one, turned down: the source hears it; Later stays with the list.
    setup_moves["open"] = ["b2"]
    assert client.post("/take-on/setup:b2/hide", json={"reason": "later"}).status_code == 204
    assert setup_moves["aside"] == []
    setup_moves["open"] = ["c3"]
    assert client.post("/take-on/setup:c3/hide", json={"reason": "not_useful"}).status_code == 204
    assert setup_moves["aside"] == ["setup:c3"]


def test_a_failing_source_reads_as_no_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_pid: int | None, _principal: bool) -> list[take_on.Offer]:
        raise RuntimeError("down")

    monkeypatch.setattr(take_on, "_sources", {})
    take_on.register_source(take_on.Source("broken", broken))
    take_on.put_made(ME, [_offer()])
    assert [o.origin for o in _list()] == ["made"]


def test_a_source_name_is_its_own() -> None:
    with pytest.raises(ValueError):
        take_on.register_source(take_on.Source("made", lambda *_a: []))
    with pytest.raises(ValueError):
        take_on.register_source(take_on.Source("Bad Name", lambda *_a: []))


def test_a_teammates_pass_reads_their_own_world_only_when_one_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.briefing.live_signals import LiveSignals

    teammate = SimpleNamespace(id=7, full_name="Reese", role="", email="reese@co.example")
    assert maker._live(teammate, False, datetime.now(UTC)) == ([], False, [], [])
    monkeypatch.setattr(take_on.hooks, "teammate_signals",
                        lambda person, _since, _now: LiveSignals(inbox=(f'[09:00] from Dana: "{person.full_name}, the quote"',)))
    lines, calendar, _senders, _people = maker._live(teammate, False, datetime.now(UTC))
    assert "Reese, the quote" in "\n".join(lines) and calendar is False


def test_a_done_link_is_one_of_the_apps_pages_or_https_only() -> None:
    o = _offer()
    take_on.put_made(ME, [o])
    take_on.start(ME, o)
    links = ["/artifacts/abc", "https://docs.example/d/1", "//evil.example/x", "/\\evil.example/x",
             "/\tx", "javascript:alert(1)", "http://plain.example"]
    take_on.finish(ME, o.key, "done", done=[(f"Step {i}", u) for i, u in enumerate(links)])
    got = take_on.finished(ME, datetime.now(UTC) - timedelta(hours=1))[0].done
    assert got == [("Step 0", "/artifacts/abc"), ("Step 1", "https://docs.example/d/1"),
                   ("Step 2", ""), ("Step 3", "")]
    assert [take_on._link(u) for u in links[4:]] == ["", "", ""]
