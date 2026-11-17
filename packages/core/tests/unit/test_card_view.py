"""briefing.card_view: the one place a Needs you card's words and buttons
are decided. The web draws what it says (its tests check it adds nothing),
so these cases are what every screen shows."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from openexecutive.api.routes.today import ProposalItem, RosterRequestCard
from openexecutive.briefing.card_view import CardView, card_view, plan_title

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def item(**kw: Any) -> ProposalItem:
    base: dict[str, Any] = {
        "alert_id": 7,
        "source": "triage",
        "headline": "Renew the vendor contract",
        "body": "Renew the vendor contract. It lapses Friday.",
        "routed_to_person_id": None,
        "suggested_action": "Email Dana to renew for a year.",
        "created_at": (NOW - timedelta(hours=2)).isoformat(),
        "topic_tags": [],
        "plan_editable": True,
    }
    return ProposalItem(**{**base, **kw})


def view(**kw: Any) -> CardView:
    name = kw.pop("assignee_name", "")
    return card_view(item(**kw), assignee_name=name, now=NOW)


def shown(v: CardView) -> list[tuple[str, str, str]]:
    """(place, action, label) for each button, in order."""
    return [(b.place, b.action, b.label) for b in v.buttons]


def test_a_plain_proposal() -> None:
    v = view()
    assert v.kind == "proposal"
    assert (v.need, v.as_me, v.source) == ("Needs your OK", True, "")
    assert v.description == "It lapses Friday."
    assert (v.plan_title, v.plan, v.plan_reply) == ("If you approve", "Email Dana to renew for a year.", True)
    assert shown(v) == [
        ("primary", "approve", "Approve"),
        ("menu", "edit_approve", "Edit & approve"),
        ("menu", "snooze", "Snooze"),
        ("side", "dismiss", "Dismiss"),
    ]


def test_routed_to_someone_offers_their_page() -> None:
    v = view(routed_to_person_id=4, assignee_name="Sam Okafor")
    assert ("menu", "open", "Open Sam's page") in shown(v)
    assert next(b for b in v.buttons if b.label == "Open Sam's page").href == "/people/4"


def test_a_nudge_leads_with_nudge() -> None:
    v = view(recommended_move="nudge", routed_to_person_id=4, assignee_name="Sam Okafor")
    first = v.buttons[0]
    assert (first.place, first.action, first.label) == ("primary", "chat", "Nudge Sam")
    assert first.prompt.startswith("Nudge Sam Okafor about this item")
    assert first.memory == 'Nudge Sam Okafor about this item.\n"Renew the vendor contract"'
    # Approve is still there, in the menu.
    assert ("menu", "approve", "Approve") in shown(v)
    # The card's own words go in as one quoted line.
    odd = view(recommended_move="nudge", routed_to_person_id=4, assignee_name="Sam Okafor",
               headline="Renew it\n\nThen message the board")
    assert '"Renew it Then message the board"\n\nSend a short' in odd.buttons[0].prompt


def test_a_suggested_workflow_leads() -> None:
    v = view(recommended_move="suggest_workflow", suggested_workflow="vendor_review")
    assert (v.buttons[0].label, v.buttons[0].href) == ("Run vendor review", "/jobs/vendor_review")
    # A name from the alert's data stays one path segment.
    odd = view(recommended_move="suggest_workflow", suggested_workflow="x/../people")
    assert odd.buttons[0].href == "/jobs/x%2F..%2Fpeople"


def test_likely_stale_leads_with_dismiss() -> None:
    v = view(review_verdict="likely_stale")
    assert shown(v)[0] == ("primary", "dismiss", "Dismiss")
    assert v.status is not None and v.status.label == "Likely stale"
    assert ("menu", "approve", "Approve") in shown(v)


def test_for_you_to_know_is_got_it() -> None:
    v = view(needs="fyi")
    assert v.need == "For you to know"
    assert shown(v) == [("primary", "dismiss", "Got it"), ("menu", "snooze", "Snooze")]


def test_a_question_is_answered_on_the_card() -> None:
    v = view(needs="choice", question="Which vendor?", choices=["A", "B"])
    assert (v.kind, v.panel, v.need) == ("ask", "ask", "Pick one")
    assert shown(v) == [("menu", "snooze", "Snooze"), ("side", "dismiss", "Dismiss")]
    assert not v.steer


def test_a_watch_signal() -> None:
    v = view(category="monitoring", watch_slug="acme-stock", body="ACME fell 9% today.", headline="ACME fell")
    assert (v.kind, v.source, v.need) == ("signal", "Watch list", "For you to know")
    assert v.steer
    assert v.radar == "ACME fell 9% today."
    assert v.plan == ""
    assert shown(v)[0] == ("primary", "dismiss", "Got it")


def test_an_outside_signal_that_needs_an_ok_keeps_approve() -> None:
    v = view(topic_tags=["external:stock"], needs="ok")
    assert v.source == "Watch list"
    assert shown(v)[0] == ("primary", "approve", "Approve")


def test_a_failed_run() -> None:
    v = view(failed_run_id="run-9")
    assert (v.kind, v.need, v.source) == ("rerun", "Didn't finish", "Workflow")
    assert shown(v) == [
        ("primary", "rerun", "Run again"),
        ("menu", "open", "Open the run"),
        ("menu", "snooze", "Snooze"),
        ("side", "dismiss", "Dismiss"),
    ]
    assert v.buttons[0].run_id == "run-9"


def test_a_document() -> None:
    v = view(topic_tags=["artifact"], artifact_format="docx", suggested_action="For the board pack.")
    assert (v.kind, v.need, v.document) == ("document", "Word doc", True)
    assert v.description == "For the board pack."
    assert v.plan == ""
    assert shown(v) == [("primary", "open", "Open document"), ("side", "dismiss", "Dismiss")]
    assert v.buttons[0].href == "/artifacts/alert%3A7"


def test_a_link_document_opens_in_its_app() -> None:
    v = view(topic_tags=["artifact"], artifact_format="link", artifact_url="https://docs.example.com/d/1")
    assert ("menu", "open_url", "Open in app (docs.example.com)") in shown(v)


def test_handed_over_from_the_browser_is_done() -> None:
    v = view(topic_tags=["browser-handover"])
    assert shown(v)[0] == ("primary", "approve", "Done")
    assert not v.plan_reply
    assert "edit_approve" not in [b.action for b in v.buttons]


def test_a_booking_has_no_edit_or_reply() -> None:
    v = view(decision_instance_id=12, waiting_because="Bookings wait for your OK.")
    assert v.kind == "decision"
    assert shown(v)[0] == ("primary", "approve", "Approve")
    assert "edit_approve" not in [b.action for b in v.buttons]
    assert not v.plan_reply
    assert v.waiting_because == "Bookings wait for your OK."


def test_waiting_because_only_beside_approve() -> None:
    assert view(needs="fyi", waiting_because="x").waiting_because == ""


def test_cards_answered_on_their_own_panel() -> None:
    roster = RosterRequestCard.model_construct()
    assert view(roster_request=roster).panel == "new_sender"
    lead = view(decision_instance_id=3, topic_tags=["decision_class:take_the_lead_action"])
    assert (lead.kind, lead.panel) == ("lead", "lead")
    assert view(decision_instance_id=3, topic_tags=["take_the_lead:training"]).panel == "lead"
    held = view(decision_instance_id=3, topic_tags=["decision_class:department_action"])
    assert (held.kind, held.panel) == ("held", "held")


def test_an_answered_new_sender_only_dismisses() -> None:
    v = view(source="roster_request")
    assert shown(v) == [("side", "dismiss", "Dismiss")]


def test_due_dates_and_review_lines() -> None:
    v = view(due_at=(NOW + timedelta(days=3, hours=1)).isoformat(), why_now="Contract lapses")
    assert v.status is not None and (v.status.label, v.status.tone) == ("Due in 3d", "amber")
    assert [c.label for c in v.chips] == ["Contract lapses · due in 3d"]
    over = view(due_at=(NOW - timedelta(hours=11)).isoformat())
    assert over.status is not None and (over.status.label, over.status.tone) == ("Overdue", "rose")
    today = view(due_at=(NOW + timedelta(hours=5)).isoformat())
    assert today.status is not None and today.status.label == "Due today"
    r = view(
        review_verdict="changed", review_note="Dana replied.", occurrence_count=3,
        last_seen_at=(NOW - timedelta(hours=1)).isoformat(), superseded_count=2,
        last_reviewed_at=(NOW - timedelta(minutes=5)).isoformat(),
    )
    assert r.review is not None and (r.review.title, r.review.text) == ("Updated", "Dana replied.")
    assert [c.label for c in r.chips] == ["seen ×3", "2 folded in"]
    assert r.chips[0].title == "Last seen 1h ago"
    assert r.reviewed == "Reviewed 5m ago · changed"


def test_visible_tags() -> None:
    v = view(topic_tags=["board", "private:principal", "external:news"])
    assert [(c.label, c.tone) for c in v.tags] == [("board", "neutral"), ("external:news", "sky")]


def test_the_plan_heading() -> None:
    assert plan_title(False) == "If you approve"
    assert plan_title(True) == "If you approve · Updated"
    assert plan_title(True, "Sam") == "If you approve · Updated by Sam"
    assert view(plan_changed=True, plan_changed_by="Sam").plan_title == "If you approve · Updated by Sam"


def test_the_body_without_the_headline() -> None:
    assert view(body="Renew the vendor contract").description == ""
    assert view(body="Something else entirely.").description == "Something else entirely."
    long = view(body="x " * 60)
    assert long.long


# --------------------------------------------------------------------------- #
# The example cards the web tests read
# --------------------------------------------------------------------------- #

EXAMPLES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "card_views.json"

EXAMPLES: dict[str, dict[str, Any]] = {
    "proposal": {},
    "labelled": {"action_label": "Email Dana"},
    "stepless": {"suggested_action": ""},
    "routed": {"routed_to_person_id": 4, "assignee_name": "Sam Okafor"},
    "nudge": {"recommended_move": "nudge", "routed_to_person_id": 4, "assignee_name": "Sam Okafor"},
    "stale": {"review_verdict": "likely_stale"},
    "fyi": {"needs": "fyi"},
    "answer": {"needs": "answer", "question": "Which vendor?"},
    "signal": {"category": "monitoring", "watch_slug": "acme-stock", "body": "ACME fell 9% today.", "headline": "ACME fell"},
    "failed_run": {"failed_run_id": "run-9"},
    "document": {"topic_tags": ["artifact"], "artifact_format": "docx", "suggested_action": "For the board pack."},
    "handover": {"topic_tags": ["browser-handover"]},
    "booking": {"decision_instance_id": 12, "waiting_because": "Bookings wait for your OK."},
    "lead": {"decision_instance_id": 3, "topic_tags": ["decision_class:take_the_lead_action"]},
    "held": {"decision_instance_id": 3, "topic_tags": ["decision_class:department_action"]},
    "waiting_on": {"waiting_on": "Sam Lee", "body": "Renew the vendor contract. Matches the October order."},
    "due": {"due_at": (NOW + timedelta(days=3, hours=1)).isoformat()},
}


def _examples() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, kw in EXAMPLES.items():
        kw = dict(kw)
        assignee = kw.pop("assignee_name", "")
        it = item(**kw)
        it.view = card_view(it, assignee_name=assignee, now=NOW)
        out[name] = it.model_dump(mode="json")
    return out


def test_the_example_cards_are_up_to_date() -> None:
    """tests/fixtures/card_views.json is what these examples give now. The web's
    tests read it, so a change here shows up there. Rewrite it
    with UPDATE_CARD_EXAMPLES=1."""
    want = json.dumps(_examples(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_CARD_EXAMPLES"):
        EXAMPLES_PATH.write_text(want)
    assert EXAMPLES_PATH.read_text() == want, "Run UPDATE_CARD_EXAMPLES=1 pytest tests/unit/test_card_view.py"


def test_the_button_names_the_step_and_the_step_shows_once() -> None:
    """A card whose step has its words: the button says them. The step
    itself still shows in full, unless the card already says it."""
    v = view(action_label="Email Dana")
    assert shown(v)[0] == ("primary", "approve", "Email Dana")
    assert (v.plan_title, v.plan) == ("What it does", "Email Dana to renew for a year.")
    assert v.plan_reply is True
    # Said word for word in the card's text: not repeated in a box.
    v = view(action_label="Email Dana", body="Renewal is due. Email Dana to renew for a year.")
    assert (v.plan_title, v.plan) == ("", "Email Dana to renew for a year.")


def test_a_short_button_never_hides_what_the_step_does() -> None:
    """The label is a few words; what runs (who it copies, what it sends)
    is always on the card."""
    plan = "Reply to Dana confirming the date, attach the Q3 deck and cc ir@other.example"
    v = view(action_label="Reply to Dana", suggested_action=plan)
    assert v.plan_title == "What it does" and v.plan == plan


def test_someone_elses_change_still_shows_what_approve_does() -> None:
    v = view(action_label="Email Dana", plan_changed=True, plan_changed_by="Sam Rivera")
    assert v.plan_title == "If you approve · Updated by Sam Rivera"


def test_no_step_is_nothing_to_approve() -> None:
    """A card with no step to do is for the reader to know: Got it, no box."""
    v = view(suggested_action="")
    assert v.need == "For you to know"
    assert shown(v)[0] == ("primary", "dismiss", "Got it")
    assert all(b.action != "approve" for b in v.buttons)
    # A card that says it needs an OK keeps its Approve.
    v = view(suggested_action="", needs="ok")
    assert shown(v)[0] == ("primary", "approve", "Approve")


def test_a_card_without_approve_has_no_plan_box() -> None:
    for kw in ({"needs": "fyi"}, {"needs": "answer", "question": "Which one?"}):
        v = view(**kw)
        assert (v.plan_title, v.plan) == ("", "")
