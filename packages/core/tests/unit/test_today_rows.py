"""Home's other rows that are not cards: broken connections
(``today._needs_reconnecting``, GET /today ``waiting_elsewhere``), long text
cut at a word, and the bottom line's counts (``today._to_act_on``)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openexecutive.api.routes import today as today_route


def test_broken_connections_are_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.briefing import needs_reconnecting

    seen: dict[str, object] = {}

    def mailbox_row(email: str | None, href: str) -> dict[str, object] | None:
        seen["mailbox"] = (email, href)
        return {"key": "reconnect:mailbox", "title": "t", "count": 1, "href": href, "action": "Reconnect"}

    monkeypatch.setattr(needs_reconnecting, "mailbox_row", mailbox_row)
    monkeypatch.setattr(
        needs_reconnecting, "account_rows",
        lambda href: [{"key": "reconnect:google_workspace", "title": "g", "count": 1, "href": href, "action": "Reconnect"}],
    )
    monkeypatch.setattr(
        "openexecutive.people.store.get_person", lambda pid: SimpleNamespace(email="me@co.example"),
    )
    rows = today_route._needs_reconnecting(4, accounts=True)
    assert [r.key for r in rows] == ["reconnect:mailbox", "reconnect:google_workspace"]
    assert seen["mailbox"] == ("me@co.example", "/delegate#as-you")
    assert rows[1].href == "/settings/status"
    # A teammate sees their own mailbox, never the Executive's accounts.
    assert [r.key for r in today_route._needs_reconnecting(4, accounts=False)] == ["reconnect:mailbox"]


def test_long_text_is_cut_at_a_word() -> None:
    cut = today_route._cut_at_word("Check the Google Play review status for Open Executive", 30)
    assert cut == "Check the Google Play review…"
    assert today_route._cut_at_word("  short   one ", 30) == "short one"


def test_bottom_line_counts_only_what_needs_action(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta

    past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        "openexecutive.attunement.open_loops.list_open_loops",
        lambda person_id=None, limit=50: [SimpleNamespace(due_at=past), SimpleNamespace(due_at=future)],
    )
    dept = SimpleNamespace(at_risk_count=0, off_track_count=0, unchecked_count=4)
    people = [
        SimpleNamespace(id=1, status="needs_reply"),  # the reader
        SimpleNamespace(id=2, status="awaiting"),
        SimpleNamespace(id=3, status="clear"),
    ]
    response = SimpleNamespace(departments=[dept], people=people)
    counts = today_route._to_act_on(response, 1)  # type: ignore[arg-type]
    assert [(c.text, c.href) for c in counts] == [
        ("1 task overdue", "/tasks"),
        ("4 goals never checked", "/goals"),
        ("1 person to follow up", "/people"),
    ]
    quiet = SimpleNamespace(departments=[SimpleNamespace(at_risk_count=0, off_track_count=0, unchecked_count=0)], people=[])
    monkeypatch.setattr("openexecutive.attunement.open_loops.list_open_loops", lambda person_id=None, limit=50: [])
    assert today_route._to_act_on(quiet, 1) == []  # type: ignore[arg-type]
