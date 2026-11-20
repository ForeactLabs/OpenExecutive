"""alerts.dispatcher.dispatch_email: the alert to its person's own address,
the way a brief reaches them (``deliver_to_person``, email only), never to the
Executive's own mailbox."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openexecutive.alerts import card_refs, dispatcher
from openexecutive.alerts.models import PRIVATE_ALERT_TAG, Alert
from openexecutive.people import store as people_store
from openexecutive.scheduler import runner
from openexecutive.scheduler.runner import PrincipalDelivery

OWNER = SimpleNamespace(id=1, full_name="Olivia Owner", email="olivia@example.com", archived=False)
BEN = SimpleNamespace(id=2, full_name="Ben Teammate", email="ben@example.com", archived=False)
NO_MAIL = SimpleNamespace(id=3, full_name="No Address", email=None, archived=False)


def _alert(
    severity: str = "high", routed: int | None = None, tags: list[str] | None = None, action: str = "Call Acme",
) -> Alert:
    return Alert(
        id=7, source="email", external_id="x-1", severity=severity, headline="Vendor outage",
        body="Acme is down.", suggested_action=action, created_at="2026-09-18T00:00:00+00:00",
        routed_to_person_id=routed, topic_tags=tags or [],
    )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def deliver(person: Any, text: str, **kw: Any) -> PrincipalDelivery:
        calls.append({"person": person, "text": text, **kw})
        return PrincipalDelivery(True, "sent", "sent", "email")

    monkeypatch.setattr(runner, "deliver_to_person", deliver)
    monkeypatch.setattr(people_store, "find_principal_person", lambda: OWNER)
    monkeypatch.setattr(people_store, "get_person", lambda pid: {1: OWNER, 2: BEN, 3: NO_MAIL}.get(pid))
    monkeypatch.setattr(card_refs, "card_url", lambda alert_id: f"https://app.example/?card={alert_id}")
    return calls


def test_the_alert_goes_to_the_principals_own_address_with_a_link(sent: list[dict[str, Any]]) -> None:
    assert asyncio.run(dispatcher.dispatch_email(_alert())) is True
    (call,) = sent
    assert call["person"] is OWNER and call["email_only"] is True
    assert call["label"] == "Vendor outage"
    assert call["text"] == (
        "Vendor outage\n\nAcme is down.\n\nSuggested action: Call Acme\n\n"
        "[Open it on Home](https://app.example/?card=7)"
    )


def test_no_suggested_action_leaves_the_line_out(sent: list[dict[str, Any]]) -> None:
    asyncio.run(dispatcher.dispatch_email(_alert(action="")))
    assert "Suggested action" not in sent[0]["text"] and "None" not in sent[0]["text"]


def test_an_urgent_alert_says_so_in_plain_words(sent: list[dict[str, Any]]) -> None:
    asyncio.run(dispatcher.dispatch_email(_alert("urgent")))
    assert sent[0]["label"] == "Urgent: Vendor outage"


def test_a_routed_alert_goes_to_its_person_unless_private(sent: list[dict[str, Any]]) -> None:
    asyncio.run(dispatcher.dispatch_email(_alert(routed=2)))
    asyncio.run(dispatcher.dispatch_email(_alert(routed=2, tags=[PRIVATE_ALERT_TAG])))
    assert [c["person"] for c in sent] == [BEN, OWNER]


def test_no_address_is_a_failed_delivery(sent: list[dict[str, Any]]) -> None:
    assert asyncio.run(dispatcher.dispatch_email(_alert(routed=3))) is False
    assert sent == []


def test_a_failed_send_is_a_failed_delivery(monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]]) -> None:
    async def fails(person: Any, text: str, **kw: Any) -> PrincipalDelivery:
        return PrincipalDelivery(False, "email: down", "send_failed")

    monkeypatch.setattr(runner, "deliver_to_person", fails)
    assert asyncio.run(dispatcher.dispatch_email(_alert())) is False


def test_an_exception_is_a_failed_delivery(monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]]) -> None:
    async def boom(person: Any, text: str, **kw: Any) -> PrincipalDelivery:
        raise RuntimeError("down")

    monkeypatch.setattr(runner, "deliver_to_person", boom)
    assert asyncio.run(dispatcher.dispatch_email(_alert())) is False
