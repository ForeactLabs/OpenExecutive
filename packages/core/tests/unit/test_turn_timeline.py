"""The "What I did" timeline a chat turn builds from its own events."""

from __future__ import annotations

import json

from openexecutive.memory import session_store
from openexecutive.memory.episodic import initialize_db
from openexecutive.orchestrator.activity_labels import FALLBACK_LABEL
from openexecutive.orchestrator.turn_timeline import (
    DO,
    LOOK,
    WORK,
    TurnTimeline,
    from_saved,
    kind_of,
    past,
    read_chips,
)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_each_round_is_a_step_with_its_time_and_reads() -> None:
    clock = Clock()
    tl = TurnTimeline(clock)
    clock.t = 103
    evt = tl.observe({"type": "activity", "label": "Searching your email…", "tool": "search_my_email", "reads": {"email": 4}})
    assert evt is not None and evt["type"] == "timeline"
    step = evt["steps"][0]
    assert step == {"label": "Searching your email", "kind": LOOK, "at": 3, "state": "current", "detail": "4 emails"}
    assert evt["done"] is False


def test_a_repeat_of_the_same_round_is_one_step() -> None:
    tl = TurnTimeline(Clock())
    tl.observe({"type": "activity", "label": "Reading your email", "tool": "read_my_email", "reads": {"email": 1}})
    tl.observe({"type": "activity", "label": "Reading your email", "tool": "read_my_email", "reads": {"email": 2}})
    assert len(tl.steps) == 1
    assert tl.steps[0].reads == {"email": 3}


def test_an_unnamed_round_adds_no_step() -> None:
    tl = TurnTimeline(Clock())
    assert tl.observe({"type": "activity", "label": "Working…", "tool": ""}) is None
    assert tl.steps == []


def test_progress_names_the_round_it_comes_before() -> None:
    # The executive sends show_progress before the activity of the round it
    # describes, so ``now`` names the next round and ``found`` goes on the
    # work done so far.
    tl = TurnTimeline(Clock())
    tl.observe({"type": "activity", "label": "Searching your email", "tool": "search_my_email"})
    evt = tl.observe({
        "type": "progress",
        "task": "Checking hiring budget",
        "now": "Comparing the LOI with Dana's email",
        "found": ["$96k of sales headcount unspent"],
    })
    assert evt is not None
    assert evt["task"] == "Checking hiring budget"
    assert [s["label"] for s in evt["steps"]] == ["Searched your email", "Comparing the LOI with Dana's email"]
    assert evt["steps"][0]["found"] == ["$96k of sales headcount unspent"]
    assert evt["steps"][1]["state"] == "current"
    # That round's activity fills in the named step, it does not add one.
    evt = tl.observe({"type": "activity", "label": "Reading a file", "tool": "read_document", "reads": {"file": 1}})
    assert evt is not None
    assert [s["label"] for s in evt["steps"]] == ["Searched your email", "Comparing the LOI with Dana's email"]
    assert evt["steps"][1]["kind"] == "look"
    assert evt["steps"][1]["state"] == "current"
    assert evt["steps"][1]["detail"] == "1 file"
    # The next round is a new step again.
    evt = tl.observe({"type": "activity", "label": "Running the numbers", "tool": "calculate"})
    assert evt is not None
    assert evt["steps"][-1]["label"] == "Running the numbers"
    # Once done, the turn's own words read in the past tense too.
    assert evt["steps"][1]["label"] == "Compared the LOI with Dana's email"
    # The same finding sent again is not added twice.
    assert tl.observe({"type": "progress", "found": ["$96k of sales headcount unspent"]}) is None


def test_progress_on_the_first_round_names_it() -> None:
    tl = TurnTimeline(Clock())
    tl.observe({"type": "progress", "now": "Reading the Q4 budget"})
    evt = tl.observe({"type": "activity", "label": "Reading a file", "tool": "read_document"})
    assert evt is not None
    assert [s["label"] for s in evt["steps"]] == ["Reading the Q4 budget"]


def test_a_round_that_only_shows_progress_leaves_its_step_to_the_next() -> None:
    tl = TurnTimeline(Clock())
    tl.observe({"type": "progress", "now": "Checking your inbox"})
    # show_progress alone: the executive's fallback activity for that round.
    assert tl.observe({"type": "activity", "label": FALLBACK_LABEL, "tool": ""}) is None
    evt = tl.observe({"type": "activity", "label": "Reading your email", "tool": "read_my_email", "reads": {"email": 3}})
    assert evt is not None
    assert [(s["label"], s.get("detail")) for s in evt["steps"]] == [("Checking your inbox", "3 emails")]


def test_a_running_tools_own_lines_stay_on_its_step() -> None:
    tl = TurnTimeline(Clock())
    tl.observe({"type": "activity", "label": "Running the weekly report", "tool": "run_workflow"})
    for line in ("Pulling the numbers", "Sending the weekly report"):
        assert tl.observe({"type": "activity", "label": line, "tool": ""}) is not None
    steps = tl.payload()["steps"]
    assert len(steps) == 1
    assert steps[0]["detail"] == "Sending the weekly report"


def test_a_step_with_no_chip_never_reads_as_done() -> None:
    # message_person failed (no action_taken): the step says it tried.
    tl = TurnTimeline(Clock())
    tl.observe({"type": "activity", "label": "Sending a message", "tool": "message_person"})
    done = tl.finish(())
    assert done is not None
    assert done["steps"] == [{**done["steps"][0], "label": "Sending a message", "state": "tried", "kind": "do"}]


def test_a_meeting_sent_for_approval_waits() -> None:
    tl = TurnTimeline(Clock())
    tl.observe({"type": "action_taken", "tool": "create_calendar_event", "summary": "Sent for approval: Board sync", "target": "x1"})
    done = tl.finish(())
    assert done is not None
    steps = done["steps"]
    assert steps[0]["state"] == "waits"
    assert steps[0]["label"] == "Sent for approval: Board sync"


def test_an_action_names_its_round_and_says_when_it_waits() -> None:
    tl = TurnTimeline(Clock())
    tl.observe({"type": "activity", "label": "Messaging Dana", "tool": "message_person"})
    tl.observe({
        "type": "action_taken",
        "tool": "message_person",
        "summary": "Waiting for your approval",
        "target": "Dana Park",
        "decision_id": 42,
    })
    payload = tl.finish()
    assert payload is not None
    [step] = payload["steps"]
    assert step["kind"] == DO
    assert step["state"] == "waits"
    assert step["card_id"] == 42
    # The summary alone: a target is often an id.
    assert step["label"] == "Waiting for your approval"


def test_finish_adds_held_steps_and_ends_the_current_one() -> None:
    clock = Clock()
    tl = TurnTimeline(clock)
    tl.observe({"type": "activity", "label": "Checking your calendar", "tool": "my_calendar", "reads": {"calendar": 1}})
    clock.t = 152
    payload = tl.finish(("Send an email",))
    assert payload is not None
    assert payload["done"] is True
    assert payload["seconds"] == 52
    assert [s["state"] for s in payload["steps"]] == ["done", "waits"]
    # A done step reads in the past tense.
    assert payload["steps"][0]["label"] == "Checked your calendar"
    assert payload["reads"] == ["Your calendar"]
    # Nothing more is taken once it has finished.
    assert tl.observe({"type": "activity", "label": "Reading", "tool": "x"}) is None


def test_a_turn_with_no_steps_saves_nothing() -> None:
    assert TurnTimeline(Clock()).finish() is None


def test_other_events_and_junk_are_ignored() -> None:
    tl = TurnTimeline(Clock())
    assert tl.observe({"type": "chunk", "content": "hi"}) is None
    assert tl.observe("text") is None
    assert tl.observe({"type": "activity", "label": 5}) is None


def test_kinds_past_tense_and_read_chips() -> None:
    assert kind_of("consult_specialist") == WORK
    assert kind_of("message_person") == DO
    assert kind_of("search_my_email") == LOOK
    assert past("Searching your email") == "Searched your email"
    assert past("Dana's budget") == "Dana's budget"
    assert read_chips({"email": 4, "file": 1, "calendar": 1, "person": 2}) == [
        "4 emails", "1 file", "Your calendar", "2 people",
    ]


def test_from_saved_keeps_only_what_the_chat_shows() -> None:
    saved = from_saved({
        "task": "t",
        "seconds": 9,
        "steps": [
            {"label": "Read a file", "kind": "look", "at": 1, "state": "current", "secret": "x"},
            {"label": 3},
            {"label": "Odd", "kind": "zzz", "state": "done"},
        ],
        "reads": ["1 file", 2],
    })
    assert saved is not None
    assert saved["steps"][0] == {"label": "Read a file", "kind": "look", "at": 1, "state": "done"}
    assert saved["steps"][1]["kind"] == LOOK
    assert saved["reads"] == ["1 file"]
    assert from_saved({"steps": []}) is None
    assert from_saved("nope") is None


def test_saved_with_the_reply_and_read_back(tmp_path) -> None:
    db = tmp_path / "t.db"
    initialize_db(db)
    session_store.create_session("s1", "t", "2026-11-27T00:00:00+00:00", db_path=db)
    tl = TurnTimeline(Clock())
    tl.observe({"type": "activity", "label": "Reading your email", "tool": "read_my_email", "reads": {"email": 2}})
    payload = tl.finish()
    session_store.save_message("s1", "user", "hi", db_path=db)
    session_store.save_message("s1", "assistant", "hello", db_path=db, steps=json.dumps(payload))
    messages = session_store.load_messages("s1", db_path=db)
    assert "steps" not in messages[0]
    assert messages[1]["steps"]["steps"][0]["label"] == "Read your email"
    assert messages[1]["steps"]["steps"][0]["detail"] == "2 emails"
