"""Email watches: one person's own inbox on the watch list
(monitoring/mailbox.py, monitoring/sources/mailbox.py).

"Tell me every time a sign-up email comes in" (11-19 1:1): match by sender,
by subject words or both, every email, until turned off, and only its owner
sees it."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openexecutive.alerts.models import PERSONAL_SOURCES, AlertSeverity
from openexecutive.alerts.store import initialize_db as initialize_alerts_db
from openexecutive.briefing.ranking import watch_slug_of
from openexecutive.delegation import gmail as gm
from openexecutive.memory.episodic import initialize_db as initialize_episodic_db
from openexecutive.monitoring import describe, mailbox
from openexecutive.monitoring import store as ms
from openexecutive.monitoring.models import SOURCE_KIND_MAILBOX, Signal
from openexecutive.monitoring.pipeline import _signal_to_alert_event
from openexecutive.monitoring.sources import get_source_for_kind
from openexecutive.monitoring.sources import mailbox as mailbox_source
from openexecutive.orchestrator import watchlist_tools as wt

OWNER = 7
OTHER = 8


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "mailbox_watch.db"
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db_path)
    monkeypatch.setattr("openexecutive.alerts.store.DB_PATH", db_path)
    initialize_episodic_db(db_path)
    initialize_alerts_db(db_path)
    ms.initialize_db(db_path)
    return db_path


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def record(event: str, summary: str, **kwargs: Any) -> None:
        rows.append({"event": event, **kwargs})

    monkeypatch.setattr(wt, "audit_log", record)
    return rows


def _email_watch(db: Path, *, owner: int = OWNER, trigger: dict[str, Any] | None = None, slug: str = "email-sign-ups") -> Any:
    new_id = ms.insert_watchlist_item(
        slug=slug,
        signal_type=SOURCE_KIND_MAILBOX,
        target="sign-up emails",
        config=mailbox.owner_config({}, owner),
        trigger=trigger if trigger is not None else {"keywords": ["sign up", "signed up"]},
        cadence="real_time",
        db_path=db,
    )
    item = ms.get_watchlist_item(new_id, db_path=db)
    assert item is not None
    return item


# --------------------------------------------------------------------------- #
# What matches
# --------------------------------------------------------------------------- #


def test_an_email_matches_by_subject_word_without_a_sender() -> None:
    trigger = {"keywords": ["sign up"]}
    assert mailbox.matches(trigger, from_addr="noreply@app.example", from_name="App", subject="New sign up: Dana")
    assert mailbox.matches(trigger, from_addr="a@b.example", from_name="", subject="NEW SIGN UP")
    assert not mailbox.matches(trigger, from_addr="a@b.example", from_name="", subject="Invoice")


def test_an_email_matches_by_sender_and_both_means_both() -> None:
    assert mailbox.matches({"from": ["pat@acme.example"]}, from_addr="PAT@acme.example", from_name="Pat", subject="hi")
    both = {"from": ["pat@acme.example"], "keywords": ["contract"]}
    assert mailbox.matches(both, from_addr="pat@acme.example", from_name="Pat", subject="The contract")
    assert not mailbox.matches(both, from_addr="pat@acme.example", from_name="Pat", subject="Lunch")
    assert not mailbox.matches(both, from_addr="sam@acme.example", from_name="Sam", subject="The contract")


def test_an_empty_trigger_matches_nothing() -> None:
    assert not mailbox.matches({}, from_addr="a@b.example", from_name="A", subject="anything")


def test_the_trigger_keeps_real_addresses_and_plain_words() -> None:
    cleaned = mailbox.clean_trigger({
        "from": ["Pat@Acme.example", "not an address", "pat@acme.example"],
        "keywords": ["  sign   up ", "Sign up", ""],
        "only_big": True,
    })
    assert cleaned == {"from": ["pat@acme.example"], "keywords": ["sign up"]}
    assert mailbox.clean_trigger({"keywords": "sign up"}) == {"keywords": ["sign up"]}
    assert mailbox.clean_trigger("nope") == {}


# --------------------------------------------------------------------------- #
# Reading the inbox
# --------------------------------------------------------------------------- #


@dataclass
class _Mailbox:
    messages: list[Any]
    asked: list[datetime]

    # Ids a real client lists but drops (the person's own mail, or mail
    # deleted since it was listed), so the page comes back short.
    dropped: frozenset[str] = frozenset()

    async def received_since(self, after: datetime, *, before: datetime | None = None, limit: int = 25) -> list[Any]:
        if before is None:
            self.asked.append(after)
        newest_first = sorted(
            (m for m in self.messages if before is None or datetime.fromisoformat(m.received_at) < before),
            key=lambda m: m.received_at, reverse=True,
        )
        return [m for m in newest_first[:limit] if m.id not in self.dropped]


def _person(**kw: Any) -> Any:
    return SimpleNamespace(id=OWNER, email="jordan@acme.example", archived=False, is_principal=True, **kw)


def _patch_mailbox(monkeypatch: pytest.MonkeyPatch, box: _Mailbox, *, status: str = "connected") -> None:
    async def gmail_status(email: str, **_: Any) -> str:
        return status

    monkeypatch.setattr(gm, "gmail_status", gmail_status)
    monkeypatch.setattr(gm, "gmail_for", lambda email: box)
    monkeypatch.setattr("openexecutive.people.store.get_person", lambda pid: _person() if pid == OWNER else None)
    monkeypatch.setattr(mailbox_source, "observe", lambda item, note: None)


def test_every_matching_email_since_the_watch_was_set_is_its_own_signal(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _email_watch(db)
    set_at = datetime.fromisoformat(item.created_at)
    later = (set_at + timedelta(minutes=3)).isoformat()
    before = (set_at - timedelta(hours=2)).isoformat()
    box = _Mailbox([
        gm.MailMessage(id="m1", thread_id="t1", from_addr="noreply@app.example", from_name="App",
                       subject="New sign up: Dana", received_at=later, text="Dana's phone is 555"),
        gm.MailMessage(id="m2", thread_id="t2", from_addr="noreply@app.example", from_name="App",
                       subject="Someone signed up", received_at=later),
        gm.MailMessage(id="m3", thread_id="t3", from_addr="ar@vendor.example", from_name="AR",
                       subject="Invoice 12", received_at=later),
        gm.MailMessage(id="m0", thread_id="t0", from_addr="noreply@app.example", from_name="App",
                       subject="Old sign up", received_at=before),
    ], [])
    _patch_mailbox(monkeypatch, box)

    signals = asyncio.run(get_source_for_kind(SOURCE_KIND_MAILBOX).poll(item, db_path=db))  # type: ignore[union-attr]

    assert [s.source_external_id for s in signals] == ["m1", "m2"]
    first = signals[0]
    assert first.dedup_key == f"mailbox:{item.id}:m1"
    assert first.severity_hint == AlertSeverity.HIGH
    assert first.normalized_summary == "App: New sign up: Dana"
    # The email's text never leaves the mailbox.
    assert first.raw_payload == {} and "555" not in json.dumps(first.model_dump(mode="json"))
    # Read from when it was set, the first time.
    assert box.asked == [set_at]


def test_a_poll_reads_back_page_by_page_to_where_it_last_read(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A day disconnected, then 130 emails: the newest page alone would miss
    # the sign-up that came first, and so would stopping at a short page.
    item = _email_watch(db)
    set_at = datetime.fromisoformat(item.created_at)
    mail = [
        gm.MailMessage(id=f"n{i}", thread_id=f"t{i}", from_addr="news@shop.example", from_name="Shop",
                       subject="Sale", received_at=(set_at + timedelta(minutes=10 + i)).isoformat())
        for i in range(130)
    ]
    mail.append(gm.MailMessage(id="first", thread_id="tf", from_addr="noreply@app.example", from_name="App",
                               subject="New sign up", received_at=(set_at + timedelta(minutes=1)).isoformat()))
    # One of the newest is their own note to self: the first page is short.
    _patch_mailbox(monkeypatch, _Mailbox(mail, [], dropped=frozenset({"n129"})))

    signals = asyncio.run(mailbox_source.MailboxSource().poll(item, db_path=db))

    assert [s.source_external_id for s in signals] == ["first"]


def test_a_backlog_comes_through_oldest_first_a_poll_at_a_time(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Back after a day away to 25 sign-ups: each poll tells at most PER_POLL
    # (one triage call each), oldest first, and the next poll picks up where
    # it stopped, never skipping one.
    item = _email_watch(db)
    set_at = datetime.fromisoformat(item.created_at)
    _patch_mailbox(monkeypatch, _Mailbox([
        gm.MailMessage(id=f"s{i:02d}", thread_id=f"t{i}", from_addr="noreply@app.example", from_name="App",
                       subject="New sign up", received_at=(set_at + timedelta(minutes=i)).isoformat())
        for i in range(25)
    ], []))
    told: list[str] = []
    for _ in range(3):
        fresh = ms.get_watchlist_item(item.id, db_path=db)
        assert fresh is not None
        signals = asyncio.run(mailbox_source.MailboxSource().poll(fresh, db_path=db))
        for signal in signals:
            ms.insert_signal(signal, db_path=db)  # the pipeline records each
        told.append(",".join(s.source_external_id for s in signals))
    assert told[0] == ",".join(f"s{i:02d}" for i in range(mailbox_source.PER_POLL))
    assert told[1] == ",".join(f"s{i:02d}" for i in range(mailbox_source.PER_POLL, 25))
    assert told[2] == ""  # the overlap re-reads only what was already told


def test_a_later_poll_reads_from_a_little_before_the_last_read_that_worked(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _email_watch(db)
    box = _Mailbox([], [])
    _patch_mailbox(monkeypatch, box)
    asyncio.run(mailbox_source.MailboxSource().poll(item, db_path=db))
    item = ms.get_watchlist_item(item.id, db_path=db)
    read = datetime.fromisoformat(item.config_json[mailbox_source.READ_THROUGH_KEY])
    # Polls that couldn't read (inbox disconnected for hours) don't move it on.
    _patch_mailbox(monkeypatch, box, status="needs_reconnect")
    later = item.model_copy(update={
        "created_at": (read - timedelta(days=1)).isoformat(),
        "last_polled_at": (read + timedelta(hours=2)).isoformat(),
    })
    asyncio.run(mailbox_source.MailboxSource().poll(later, db_path=db))
    assert ms.get_watchlist_item(item.id, db_path=db).config_json[mailbox_source.READ_THROUGH_KEY] == read.isoformat()
    _patch_mailbox(monkeypatch, box)
    asyncio.run(mailbox_source.MailboxSource().poll(later, db_path=db))
    assert box.asked[-1] == read - timedelta(minutes=10)


def test_an_unconnected_inbox_is_not_read(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _email_watch(db)
    box = _Mailbox([gm.MailMessage(id="m1", thread_id="t1", subject="sign up")], [])
    _patch_mailbox(monkeypatch, box, status="not_configured")
    notes: list[str] = []
    monkeypatch.setattr(mailbox_source, "observe", lambda item, note: notes.append(note))
    assert asyncio.run(mailbox_source.MailboxSource().poll(item, db_path=db)) == []
    assert box.asked == [] and notes == [mailbox_source.NOT_CONNECTED]


# --------------------------------------------------------------------------- #
# Who sees it, who it tells
# --------------------------------------------------------------------------- #


def test_only_its_owner_sees_an_email_watch(db: Path) -> None:
    ms.insert_watchlist_item(slug="stock-nwt", signal_type="stock", target="NWT", db_path=db)
    _email_watch(db)

    def slugs(**kw: Any) -> list[str]:
        return [i.slug for i in ms.list_watchlist(db_path=db, **kw)]

    assert slugs() == ["stock-nwt"]
    assert slugs(for_person=OTHER) == ["stock-nwt"]
    assert slugs(for_person=OWNER) == ["stock-nwt", "email-sign-ups"]
    assert slugs(every_watch=True) == ["stock-nwt", "email-sign-ups"]


def test_an_email_watch_never_shares_a_name() -> None:
    # Stored with a random ending: no one else's watch runs into it, and its
    # name can't be found by trying names.
    first, second = mailbox.own_slug("email-sign-ups"), mailbox.own_slug("email-sign-ups")
    assert first.startswith("email-sign-ups-") and first != second
    long = mailbox.own_slug("y" * 61)
    assert len(long) == 61 and long.startswith("y" * 54 + "-")


def test_company_wide_reads_leave_its_signals_out(db: Path) -> None:
    item = _email_watch(db)
    ms.insert_signal(Signal(
        watchlist_id=item.id, source_kind=SOURCE_KIND_MAILBOX, source_external_id="m1",
        captured_at=datetime.now(UTC).isoformat(), normalized_summary="App: New sign up",
        provenance_url="https://mail.google.com/", severity_hint=AlertSeverity.HIGH,
        dedup_key=f"mailbox:{item.id}:m1",
    ), db_path=db)
    assert ms.list_recent_signals(db_path=db) == []
    assert len(ms.list_signals_for_watchlist(item.id, db_path=db)) == 1


def test_every_email_is_its_own_personal_card_for_the_owner(db: Path) -> None:
    item = _email_watch(db)
    signal = Signal(
        watchlist_id=item.id, source_kind=SOURCE_KIND_MAILBOX, source_external_id="m1",
        captured_at=datetime.now(UTC).isoformat(), normalized_summary="App: New sign up",
        provenance_url="https://mail.google.com/", severity_hint=AlertSeverity.HIGH,
        dedup_key=f"mailbox:{item.id}:m1",
    )
    event = _signal_to_alert_event(signal, item)
    assert event.dedup_hint == "watch:email-sign-ups#m1"
    assert watch_slug_of(SimpleNamespace(
        source=event.source, dedup_key=event.dedup_hint, topic_tags=["external:email-sign-ups"],
    )) == "email-sign-ups"
    assert event.routed_to_person_id == OWNER
    assert event.source in PERSONAL_SOURCES
    # Set to tell someone else, or a department: it still tells only its
    # owner (text in an email it read can't send their mail to anyone).
    told = item.model_copy(update={"route_to_person_id": OTHER, "route_to_department": "sales"})
    event = _signal_to_alert_event(signal, told)
    assert (event.routed_to_person_id, event.department) == (OWNER, "")


def test_its_audit_rows_are_its_owners() -> None:
    item = SimpleNamespace(signal_type=SOURCE_KIND_MAILBOX, config_json={"mailbox_of": OWNER})
    assert mailbox.audit_privacy(item) == {"private": True, "private_to_person": OWNER}  # type: ignore[arg-type]
    other = SimpleNamespace(signal_type="stock", config_json={"mailbox_of": OWNER})
    assert mailbox.audit_privacy(other) == {}  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #


def test_it_says_what_it_watches_in_plain_words() -> None:
    assert describe.trigger_line(SOURCE_KIND_MAILBOX, {"keywords": ["sign up"]}) == "An email comes in that mentions “sign up”"
    assert describe.trigger_line(SOURCE_KIND_MAILBOX, {"from": ["pat@acme.example", "sam@acme.example"]}) == (
        "An email comes in from pat@acme.example or sam@acme.example"
    )
    # Checked every few minutes, not every 15.
    assert describe.checks_every(SOURCE_KIND_MAILBOX, "real_time") == "every 5 minutes"


# --------------------------------------------------------------------------- #
# The Executive's tools
# --------------------------------------------------------------------------- #


def _speaker(monkeypatch: pytest.MonkeyPatch, person_id: int | None, *, status: str = "connected") -> None:
    from openexecutive.orchestrator import delegation_tools

    monkeypatch.setattr(wt, "_speaker_id", lambda: person_id)

    def writer(tool: str) -> Any:
        if person_id is None:
            return json.dumps({"error": f"{tool} is not available on this turn. Do not retry."})
        return SimpleNamespace(person=SimpleNamespace(id=person_id), email="jordan@acme.example", mailbox=None)

    async def gmail_status(email: str, **_: Any) -> str:
        return status

    monkeypatch.setattr(delegation_tools, "_writer", writer)
    monkeypatch.setattr(gm, "gmail_status", gmail_status)
    monkeypatch.setattr(wt, "_scan_soon", lambda: None)
    monkeypatch.setattr("openexecutive.delegation.lockdown.outside_reach_refusal", lambda *a, **k: None)


def _add(**kw: Any) -> dict[str, Any]:
    body = {"slug": "email-sign-ups", "signal_type": "mailbox", "target": "sign-up emails", **kw}
    return json.loads(asyncio.run(wt.handle_add_watchlist_entry(body)))


def test_the_executive_watches_the_speakers_own_inbox(
    db: Path, monkeypatch: pytest.MonkeyPatch, _no_audit: list[dict[str, Any]],
) -> None:
    _speaker(monkeypatch, OWNER)
    out = _add(trigger={"keywords": ["sign up"]}, config={"mailbox_of": OTHER})
    assert out["ok"] is True, out
    assert out["slug"].startswith("email-sign-ups-")  # mailbox.own_slug
    row = ms.get_watchlist_item_by_slug(out["slug"], db_path=db)
    assert row is not None
    assert row.config_json["mailbox_of"] == OWNER and row.config_json["enrich"] is False
    assert row.trigger_json == {"keywords": ["sign up"]}
    assert row.cadence == "real_time"
    assert _no_audit[-1]["private"] is True and _no_audit[-1]["private_to_person"] == OWNER


def test_an_email_watch_needs_a_sender_or_a_word(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _speaker(monkeypatch, OWNER)
    assert "error" in _add(trigger={})
    assert ms.list_watchlist(db_path=db, every_watch=True) == []


def test_an_unconnected_inbox_says_how_to_connect(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _speaker(monkeypatch, OWNER, status="not_configured")
    out = _add(trigger={"keywords": ["sign up"]})
    assert out["status"] == "not_configured" and "note" in out
    assert ms.get_watchlist_item_by_slug("email-sign-ups", db_path=db) is None


def test_someone_else_cant_see_or_change_an_email_watch(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _email_watch(db)
    _speaker(monkeypatch, OTHER)
    listed = json.loads(asyncio.run(wt.handle_list_watchlist({"enabled_only": False})))
    assert listed["count"] == 0
    assert "error" in json.loads(asyncio.run(wt.handle_tune_watchlist_entry({"slug": "email-sign-ups", "enabled": False})))
    assert "error" in json.loads(asyncio.run(wt.handle_remove_watchlist_entry({"slug": "email-sign-ups"})))
    assert ms.get_watchlist_item_by_slug("email-sign-ups", db_path=db) is not None

    _speaker(monkeypatch, OWNER)
    listed = json.loads(asyncio.run(wt.handle_list_watchlist({"enabled_only": False})))
    assert [w["slug"] for w in listed["watchlist"]] == ["email-sign-ups"]
    assert listed["watchlist"][0]["tells_you_when"] == "An email comes in that mentions “sign up” or “signed up”"
    tuned = json.loads(asyncio.run(wt.handle_tune_watchlist_entry({"slug": "email-sign-ups", "trigger": {"from": ["x"]}})))
    assert "error" in tuned  # no real address and no word: nothing would ever match


def test_research_never_proposes_an_email_watch() -> None:
    out = json.loads(asyncio.run(wt.handle_propose_watch(
        {"slug": "email-x", "signal_type": "mailbox", "target": "x"}, [],
    )))
    assert "error" in out


def test_an_inbox_watch_can_be_set_after_their_mail_was_read(db: Path) -> None:
    # "Check Dana's email and tell me when she replies": the read shuts the
    # watch list's outside fetches, not a watch on their own inbox.
    from openexecutive.delegation import lockdown

    assert not lockdown.mail_touched_withholds("add_watchlist_entry", {"signal_type": "mailbox", "slug": "x"})
    assert lockdown.mail_touched_withholds("add_watchlist_entry", {"signal_type": "rss", "slug": "x"})
    _email_watch(db)
    ms.insert_watchlist_item(slug="rss-acme", signal_type="rss", target="https://acme.example/feed", db_path=db)
    assert not lockdown.mail_touched_withholds("tune_watchlist_entry", {"slug": "email-sign-ups", "enabled": False})
    assert lockdown.mail_touched_withholds("tune_watchlist_entry", {"slug": "rss-acme", "enabled": False})
    assert lockdown.mail_touched_withholds("tune_watchlist_entry", "not a dict")
