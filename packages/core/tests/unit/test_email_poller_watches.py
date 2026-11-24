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
        header_id = kw.pop("header_id", None)
        kw_header = await header_id() if header_id is not None else None
        noted.append({**kw, "header": kw_header})
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
            from_name="App", subject="New sign up: Acme Dental",
            raw_text="From: App <noreply@app.example>\nMessage-ID: <su-9@app.example>\n\nMessage-ID: <forged@x>",
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
        "proven": None, "header": "<su-9@app.example>",
    }]


def test_a_name_that_is_only_the_address_is_no_name(monkeypatch: Any) -> None:
    noted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "openexecutive.monitoring.mailbox.note_executive_mail", _noting(noted, 0),
    )
    msg = InboundMessage(message_id="m1", thread_id="", from_addr="robin@hale.example",
                         from_name="Robin@Hale.example", subject="Rent")
    asyncio.run(poller._note_watches(msg, private=True, mail_of=None, proven=None))
    assert noted[0]["from_name"] == "" and noted[0]["private"] is True and noted[0]["header"] is None


def test_the_message_id_is_read_from_the_header_lines_never_the_body() -> None:
    assert poller._message_id_in("From: a@b.example\nMessage-Id:  <m-1@b.example>\n\nhi") == "<m-1@b.example>"
    assert poller._message_id_in("From: a@b.example\n\nMessage-ID: <forged@x>") == ""
