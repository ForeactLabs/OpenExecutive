"""Test that PR-C's executive_reflection extension renders external
signals correctly in its context block."""
from __future__ import annotations

from openexecutive.workflows.executive_reflection import (
    _render_reflection_context,
)


def _empty_today() -> dict:
    return {"departments": [], "people": [], "proposals": []}


def test_render_includes_external_signals_section() -> None:
    """What the watch list showed (a card or a brief line) appears, fenced as
    the watched source's words, with the day and the initiative it touched."""
    signals = [
        {
            "severity_hint": "urgent",
            "source_kind": "stock",
            "normalized_summary": "Apple (AAPL) down 7.0% vs prev close",
            "processed_outcome": "alerted",
            "captured_at": "2026-05-27T09:00:00+00:00",
        },
        {
            "severity_hint": "high",
            "source_kind": "query",
            "normalized_summary": "Rival cuts prices",
            "processed_outcome": "brief_line",
            "enrichment": {"matched_initiative": "Pricing refresh"},
        },
    ]
    rendered = _render_reflection_context(
        period_label="2026-05-28",
        today_data=_empty_today(),
        activity=[],
        recent_alerts=[],
        external_signals=signals,
    )
    assert "FOUND ON THE WATCH LIST" in rendered
    assert "- 2026-05-27 [stock, card] Apple (AAPL) down 7.0% vs prev close" in rendered
    assert "- [query, brief line] Rival cuts prices (touches: Pricing refresh)" in rendered
    assert "<untrusted_content" in rendered


def test_render_skips_external_section_when_empty() -> None:
    """No EXTERNAL SIGNALS block when the list is empty — keeps the
    reflection prompt terse on quiet days."""
    rendered = _render_reflection_context(
        period_label="2026-05-28",
        today_data=_empty_today(),
        activity=[],
        recent_alerts=[],
        external_signals=[],
    )
    assert "FOUND ON THE WATCH LIST" not in rendered
    # When everything is empty the function still emits the "nothing
    # worth acting on" fallback — verify that still triggers.
    assert "No signals worth acting on" in rendered


def test_render_truncates_long_signals_list() -> None:
    """The render cap (10) keeps a runaway scan from blowing the prompt budget."""
    signals = [
        {
            "severity_hint": "low",
            "source_kind": "rss",
            "normalized_summary": f"Entry {i}",
            "processed_outcome": "alerted",
        }
        for i in range(50)
    ]
    rendered = _render_reflection_context(
        period_label="2026-05-28",
        today_data=_empty_today(),
        activity=[],
        recent_alerts=[],
        external_signals=signals,
    )
    # The 10th is in, the 11th is out.
    assert "Entry 9" in rendered
    assert "Entry 10" not in rendered


# --------------------------------------------------------------------------- #
# Yesterday's standup + review verdicts (alert lifecycle)
# --------------------------------------------------------------------------- #


def test_render_includes_previous_reflection_block_and_caps_it() -> None:
    rendered = _render_reflection_context(
        period_label="2026-09-11",
        today_data=_empty_today(),
        activity=[],
        recent_alerts=[],
        external_signals=[],
        previous_reflection="**Acted on:** DM'd Dana about the renewal\n" + ("x" * 5000),
    )
    assert "YESTERDAY'S STANDUP (already handled — do not repeat):" in rendered
    assert "DM'd Dana about the renewal" in rendered
    assert len(rendered) < 2500


def test_render_skips_previous_block_when_none() -> None:
    rendered = _render_reflection_context(
        period_label="2026-09-11",
        today_data=_empty_today(),
        activity=[],
        recent_alerts=[],
        external_signals=[],
    )
    assert "YESTERDAY'S STANDUP" not in rendered


def test_render_open_alerts_carry_alert_id_and_review_verdict() -> None:
    rendered = _render_reflection_context(
        period_label="2026-09-11",
        today_data=_empty_today(),
        activity=[],
        recent_alerts=[
            {
                "alert_id": 42, "severity": "high", "headline": "Acme renewal at risk",
                "topic_tags": ["customer"], "review_verdict": "relevant",
                "review_note": "Dana has not replied in 3 days", "recommended_move": "nudge",
            },
            {"alert_id": 43, "severity": "low", "headline": "plain", "topic_tags": []},
        ],
        external_signals=[],
    )
    assert "alert_id=42 [high] Acme renewal at risk tags=customer review=relevant (Dana has not replied in 3 days) next=nudge" in rendered
    assert "alert_id=43 [low] plain tags=" in rendered
    assert "review=" not in rendered.split("alert_id=43")[1]


def test_reflection_prompt_has_memory_rule_and_never_instructs_ack_alert() -> None:
    from openexecutive.workflows.executive_reflection import _build_reflection_system

    prompt = _build_reflection_system({"discord"}, True)
    assert "YESTERDAY'S STANDUP" in prompt
    assert "Do NOT re-act" in prompt
    assert "ack_alert" not in prompt
