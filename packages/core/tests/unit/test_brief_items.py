"""What a brief already told its reader (briefing.brief_items), and how the
morning brief uses it: an item told before and unchanged is one closing
"Still open" line, never retold; lines that only say a part is empty go."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openexecutive.briefing import brief_items, narrative_cache
from openexecutive.briefing.live_signals import LiveSignals
from openexecutive.briefing.narrative import NOTHING_NEW_BRIEF, render_briefing_context


@pytest.fixture(autouse=True)
def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(narrative_cache, "DB_PATH", tmp_path / "cache.db")


NOW = datetime.now(UTC)
SINCE = NOW - timedelta(hours=24)


def _card(alert_id: int, headline: str, *, days: int = 5, **extra: object) -> dict[str, object]:
    return {
        "alert_id": alert_id, "headline": headline, "suggested_action": "Approve",
        "created_at": (NOW - timedelta(days=days)).isoformat(), "category": "action",
        **extra,
    }


def _goal(key_result: str, status: str = "at_risk") -> dict[str, object]:
    return {"key_result": key_result, "current": "40", "target": "100", "status": status}


def _data(*cards: dict[str, object]) -> dict[str, object]:
    return {
        "proposals": list(cards),
        "departments": [{
            "slug": "ops", "title": "Ops", "at_risk_count": 1, "off_track_count": 0,
            "attention_goals": [_goal("Close 4 buyouts")],
        }],
        "people": [{"id": 7, "full_name": "Josef R", "role": "Broker", "awaiting_count": 1}],
    }


def _render(data: dict[str, object], told: dict[str, str] | None, live: LiveSignals | None = None) -> str:
    return render_briefing_context(
        period_label="p", today_data=data, activity=[], since=SINCE,
        live=live or LiveSignals(), told=told, standing_facts="",
    )


def test_told_items_round_trip_per_reader() -> None:
    data = _data(_card(220, "Pine View II buyout"))
    states = brief_items.item_states(data)
    assert set(states) == {"card:220", "dept:ops", next(k for k in states if k.startswith("goal:")), "awaiting:7"}
    brief_items.record("principal_brief_morning", states)
    brief_items.record("principal_brief_morning", {"card:1": "x"}, person_id=4)
    assert brief_items.told("principal_brief_morning") == states
    assert brief_items.told("principal_brief_morning", person_id=4) == {"card:1": "x"}
    assert brief_items.told("principal_brief_morning", person_id=5) == {}


def test_a_card_state_ignores_its_age_but_not_what_it_needs() -> None:
    card = _card(220, "Pine View II buyout", days=3)
    older = {**card, "created_at": (NOW - timedelta(days=9)).isoformat()}
    assert brief_items.card_state(card) == brief_items.card_state(older)
    assert brief_items.card_state(card) != brief_items.card_state({**card, "suggested_action": "Send"})
    assert brief_items.card_state(card) != brief_items.card_state({**card, "review_verdict": "likely_stale"})


def test_a_reworded_review_note_is_not_news() -> None:
    # The alert review rewrites its note and why-now in new words each pass.
    pine = _card(220, "Pine View II buyout", review_note="waiting ~3 days", why_now="Dana asked Monday")
    told = brief_items.item_states(_data(pine))
    reworded = {**pine, "review_note": "still waiting on Dana", "why_now": "no reply since Monday"}
    out = _render(_data(reworded), told)
    assert "Pine View II buyout" in out.split("STILL OPEN, ALREADY TOLD")[1]
    assert "CHANGED OR NOT TOLD BEFORE" not in out


def test_an_unchanged_card_told_before_goes_on_the_closing_line_only() -> None:
    pine = _card(220, "Pine View II buyout")
    statement = _card(230, "Industrial Park statement for the tax filing")
    told = brief_items.item_states(_data(pine, statement))
    changed = {**statement, "suggested_action": "Upload the signed copy"}
    out = _render(_data(pine, changed), told)
    already = out.split("STILL OPEN, ALREADY TOLD")[1]
    assert "Pine View II buyout (open 5d)" in already
    fresh = out.split("STILL OPEN, CHANGED OR NOT TOLD BEFORE")[1].split("STILL OPEN, ALREADY TOLD")[0]
    assert "Industrial Park statement" in fresh and "(changed since your last brief)" in fresh
    assert "Pine View" not in fresh
    # The told goal and person, unchanged, are not news.
    assert "DEPARTMENTS WITH RISK" not in out
    assert "PEOPLE YOU ARE WAITING ON" not in out


def test_without_a_record_everything_still_open_is_told() -> None:
    out = _render(_data(_card(220, "Pine View II buyout")), {})
    assert "STILL OPEN, CHANGED OR NOT TOLD BEFORE" in out and "Pine View II buyout" in out
    assert "ALREADY TOLD" not in out
    assert "Close 4 buyouts" in out and "Josef R" in out
    # Callers that pass nothing (the /today header) render as before.
    legacy = _render(_data(_card(220, "Pine View II buyout")), None)
    assert "STILL OPEN FROM BEFORE" in legacy and "ALREADY TOLD" not in legacy


def test_a_far_off_deadline_told_before_is_left_out() -> None:
    regions = _card(240, "Regions LP buyout", due_at=(NOW + timedelta(days=380)).isoformat())
    soon = _card(241, "Catoosa closing", due_at=(NOW + timedelta(days=3)).isoformat())
    told = brief_items.item_states(_data(regions, soon))
    out = _render(_data(regions, soon), told)
    assert "Regions LP buyout" not in out
    assert "Catoosa closing" in out
    # Told once, when it is new to the reader.
    assert "Regions LP buyout" in _render(_data(regions, soon), {})


def test_a_goal_that_moved_is_news_again() -> None:
    told = brief_items.item_states(_data())
    moved = _data()
    moved["departments"][0]["attention_goals"] = [_goal("Close 4 buyouts", "off_track")]  # type: ignore[index]
    out = _render(moved, told)
    assert "DEPARTMENTS WITH RISK" in out and "Close 4 buyouts" in out


def test_the_own_inbox_reaches_the_brief_with_one_line_for_the_rest() -> None:
    live = LiveSignals(
        inbox=('[07:12] from Josef R: closing statement attached — ties to Catoosa closing',),
        inbox_others=("Chase: statement ready", "UPS: delivered"),
        inbox_others_total=6,
    )
    out = _render(_data(), {}, live)
    assert "YOUR OWN INBOX" in out and "ties to Catoosa closing" in out
    assert "ALSO IN YOUR OWN INBOX: 6 other emails" in out and "Chase: statement ready" in out
    # The /today header has its own render of the inbox.
    assert "YOUR OWN INBOX" not in _render(_data(), None, live)


def test_lines_that_say_nothing_are_dropped() -> None:
    # Braden, 25 and 27 Nov.
    draft = (
        "**Do today**\n"
        "1. Send the Annamarie note as drafted. [Review](https://x/c/255)\n\n"
        "**New since yesterday**\n"
        "- Nothing new in your inbox since the last brief.\n"
        "- Nothing new completed on my side overnight.\n"
        "- Research DMs to you: only 1 of 3 answered in the last 30 days. I'm raising this here rather than sending more.\n\n"
        "**Waiting on**\n"
        "- Nobody is currently waiting on you.\n\n"
        "**At risk**\n"
        "- Nothing flagged as slipping.\n\n"
        "**Still open:** Industrial Park statement (12 days)."
    )
    out = brief_items.drop_empty_lines(draft)
    assert out == (
        "**Do today**\n"
        "1. Send the Annamarie note as drafted. [Review](https://x/c/255)\n\n"
        "**Still open:** Industrial Park statement (12 days)."
    )


def test_real_lines_and_the_nothing_new_line_stay() -> None:
    text = (
        f"{NOTHING_NEW_BRIEF}\n\n"
        "- No reply from Brennan on the PSA since Monday.\n"
        "- Nothing goes to Annamarie until you say yes. [Approve](https://x/c/220)\n\n"
        "**Still open:** Pine View II buyout (8 days)."
    )
    assert brief_items.drop_empty_lines(text) == text


def test_an_inline_heading_keeps_its_heading_only_when_its_text_is_empty() -> None:
    text = "**Today** - No calendar or stuck items in this brief.\n**Needs you** - Approve the draft."
    assert brief_items.drop_empty_lines(text) == "**Needs you** - Approve the draft."


def test_a_line_that_goes_on_past_the_empty_part_stays() -> None:
    text = (
        "**New since yesterday**\n"
        "- No change from Acme since Friday; Dana still needs your sign-off by noon.\n"
        "- Nothing on the calendar blocks the 2pm Dana call, so prep the pricing memo.\n"
        "- Nothing on the calendar today."
    )
    assert brief_items.drop_empty_lines(text) == (
        "**New since yesterday**\n"
        "- No change from Acme since Friday; Dana still needs your sign-off by noon.\n"
        "- Nothing on the calendar blocks the 2pm Dana call, so prep the pricing memo."
    )


def test_only_what_the_context_showed_is_recorded_as_told() -> None:
    cards = [_card(300 + i, f"Older card number {i}") for i in range(3)]
    data = _data(*cards)
    rendered = "- Older card number 0\n- Older card number 1\n(and 1 more on Home)\nClose 4 buyouts\nJosef R"
    states = brief_items.item_states(data, rendered, {})
    assert "card:300" in states and "card:301" in states
    assert "card:302" not in states  # cut by the list's limit: still news
    # Told before and unchanged, it stays told though today's line was cut.
    full = brief_items.item_states(data)
    assert "card:302" in brief_items.item_states(data, rendered, full)
    assert "dept:ops" in states and "awaiting:7" in states
