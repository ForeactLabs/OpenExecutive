"""Every email that reaches the Executive is checked against the email
watches (monitoring.mailbox.note_executive_mail), even one that gets no
turn: "tell me every time a sign-up email comes in" is mostly automated
mail."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import openexecutive.integrations.email_poller as poller
from openexecutive.integrations.mail_sources import InboundMail
from openexecutive.integrations.workspace.google import GoogleMail
from openexecutive.integrations.workspace.mail import InboundMessage


def _noting(noted: list[dict[str, Any]], result: int) -> Any:
    async def note(**kw: Any) -> int:
        noted.append(kw)
        return result

    return note


async def _no_send(_to: str, _body: str, _in_thread: bool) -> str | None:
    return None


def test_automated_mail_gets_no_turn_but_is_checked_against_the_watches(monkeypatch: Any) -> None:
    noted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "openexecutive.monitoring.mailbox.note_executive_mail", _noting(noted, 1),
    )
    run = AsyncMock(side_effect=AssertionError("a turn ran"))
    monkeypatch.setattr(poller, "_run_executive", run)
    mail = InboundMail(
        message=InboundMessage(
            message_id="18c0ffee00000002", thread_id="th9", from_addr="noreply@app.example",
            from_name="App", subject="New sign up: Acme Dental", raw_text="From: App <noreply@app.example>",
        ),
        source="mailbox",
        own_address="exec@example.com",
        send_answer=_no_send,
    )
    outcome = asyncio.run(poller._handle_mail(mail, MagicMock(), mailbox=GoogleMail(), done=AsyncMock()))
    assert outcome == poller._SKIPPED
    run.assert_not_awaited()
    assert noted == [{
        "message_id": "18c0ffee00000002", "thread_id": "th9", "from_addr": "noreply@app.example",
        "from_name": "App", "subject": "New sign up: Acme Dental", "private": True, "mail_of": None,
        "proven": None,
    }]


def test_a_name_that_is_only_the_address_is_no_name(monkeypatch: Any) -> None:
    noted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "openexecutive.monitoring.mailbox.note_executive_mail", _noting(noted, 0),
    )
    msg = InboundMessage(message_id="m1", thread_id="", from_addr="robin@hale.example",
                         from_name="Robin@Hale.example", subject="Rent")
    asyncio.run(poller._note_watches(msg, private=True, mail_of=None, proven=None))
    assert noted[0]["from_name"] == "" and noted[0]["private"] is True
