"""A forwarded email is read, not dropped as quoted history.

Apple Mail and iPhone forward with "Begin forwarded message:" and quote the
whole forwarded email with ">" below it, so ``sender_new_text`` (the
sender's own words) left nothing of it: the Executive opened a forward and
saw only its logo placeholders. ``readable_text`` keeps the forward, unquoted,
under "Forwarded message:", while reply history stays out.
"""
from __future__ import annotations

from types import SimpleNamespace

from openexecutive.integrations.email_poller import (
    FORWARDED_HEADING,
    _email_memory_text,
    readable_text,
    sender_new_text,
)

APPLE_FORWARD = (
    "\r\n\r\n> Begin forwarded message:\r\n> \r\n"
    "> From: \"Power Co\" <letters@power.example>\r\n"
    "> Subject: Tree trimming easement - Parcel 12\r\n"
    "> Date: November 3, 2026\r\n> \r\n"
    "> Dear owner,\r\n> The easement covers 640 feet along Mill Road.\r\n"
    "> ￼\r\n> Call Dana Ruiz at 555-0100.\r\n"
)


def test_an_apple_mail_forward_keeps_the_forwarded_email() -> None:
    text = readable_text(APPLE_FORWARD, "Fwd: Tree trimming easement")
    assert text.startswith(FORWARDED_HEADING)
    assert "The easement covers 640 feet along Mill Road." in text
    assert "Call Dana Ruiz at 555-0100." in text
    assert "From: \"Power Co\" <letters@power.example>" in text
    # Its own ">" quoting is taken off; the marker line isn't repeated.
    assert "\n>" not in text and "Begin forwarded message" not in text


def test_the_senders_note_comes_before_the_forward() -> None:
    body = "Can you answer this one?\n\n" + APPLE_FORWARD
    text = readable_text(body)
    assert text.startswith("Can you answer this one?\n\nForwarded message:\n")
    # Their own words alone are unchanged.
    assert sender_new_text(body) == "Can you answer this one?"


def test_a_gmail_forward_keeps_the_forwarded_email() -> None:
    body = "FYI.\n\n---------- Forwarded message ---------\nFrom: Bank\nDate: Mon\n\nRate is 5%"
    assert readable_text(body) == "FYI.\n\nForwarded message:\nFrom: Bank\nDate: Mon\n\nRate is 5%"


def test_an_outlook_forward_keeps_the_forwarded_email_by_its_subject() -> None:
    body = "See below.\n\n________________________________\nFrom: Bank\nSent: Monday\n\nRate is 5%"
    assert readable_text(body, "FW: Rates").endswith("Forwarded message:\nFrom: Bank\nSent: Monday\n\nRate is 5%")
    # The same block under a reply is history, still left out.
    assert readable_text(body, "RE: Rates") == "See below."


def test_reply_history_is_still_left_out() -> None:
    body = "Sounds good.\n\nOn Mon, Dana <d@example.com> wrote:\n> Shall we meet Friday?\n> Begin forwarded message:\n> old"
    assert readable_text(body, "Re: Friday") == "Sounds good."


def test_each_part_has_its_own_cap() -> None:
    body = "x" * 50 + "\n\n---------- Forwarded message ---------\n" + "y" * 500
    text = readable_text(body, max_chars=10, forwarded_chars=100)
    assert text == "x" * 10 + "\n\nForwarded message:\n" + "y" * 100


def test_peer_memory_still_notes_an_apple_forward_without_its_text() -> None:
    raw = "Message ID: 1\nSubject: Fwd: Tree trimming\nFrom: a@example.com\n\n--- BODY ---\n" + APPLE_FORWARD
    assert "640 feet" not in _email_memory_text(raw)
    assert "[Forwarded an earlier message]" in _email_memory_text(raw)


def test_read_my_email_shows_a_forwarded_email() -> None:
    from openexecutive.orchestrator.mail_read_tools import _render_thread

    message = SimpleNamespace(
        text=APPLE_FORWARD, subject="Fwd: Tree trimming easement", date="Tue", received_at="",
        labels=["INBOX"], from_addr="ann@example.com", from_name="Ann", to=["me@example.com"], cc=[],
    )
    rendered = _render_thread([message], 1, "me@example.com")
    assert "The easement covers 640 feet along Mill Road." in rendered


def test_the_inbox_drafter_sees_a_forwarded_email() -> None:
    from openexecutive.delegation.inbox_classifier import render_email

    message = SimpleNamespace(
        text=APPLE_FORWARD, subject="Fwd: Tree trimming easement", from_name="Ann",
        from_addr="ann@example.com", sender_authenticated=True, to=["me@example.com"], cc=[],
        attachments=[], has_attachments=False,
    )
    assert "640 feet along Mill Road" in render_email(message, relation="teammate")


def test_a_forward_they_sent_stays_out_of_their_own_words() -> None:
    from openexecutive.orchestrator.mail_read_tools import _render_thread

    message = SimpleNamespace(
        text="Please look.\n\n" + APPLE_FORWARD, subject="Fwd: Tree trimming easement", date="Tue",
        received_at="", labels=["SENT"], from_addr="me@example.com", from_name="Me", to=["x@example.com"], cc=[],
    )
    rendered = _render_thread([message], 1, "me@example.com")
    yours, _, rest = rendered.partition("<untrusted_content")
    assert "Yours" in yours and "Please look." in yours
    assert "640 feet" not in yours
    assert "640 feet" in rest


def test_a_note_below_an_apple_forward_stays_the_senders() -> None:
    from openexecutive.integrations.email_poller import split_forward

    own, forwarded = split_forward("> Begin forwarded message:\n> From: A\n> body\n\nmy note below", "Fwd: x")
    assert own == "my note below"
    assert forwarded == "From: A\nbody"


def test_the_principals_unquoted_iphone_forward_is_not_their_own_words() -> None:
    from openexecutive.integrations.email_poller import _forwarded, _principal_email_text

    raw = (
        "Message ID: 1\nSubject: Fwd: Invoice\nFrom: owner@example.com\n\n--- BODY ---\n"
        "Look\n\nBegin forwarded message:\n\nFrom: A\n\nPay invoice"
    )
    assert _forwarded(raw)
    text = _principal_email_text(raw, "owner@example.com")
    own, _, rest = text.partition("<untrusted_content")
    assert "Look" in own and "Pay invoice" not in own
    assert "Pay invoice" in rest
