"""Fewer, better watch finds: how close a find is decides where it shows,
repeats and old stories from a web search are caught, a page whose only
change is a count says nothing, and a watch's card is reviewed once."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openexecutive.alerts import lifecycle, review
from openexecutive.alerts import store as alert_store
from openexecutive.alerts.models import AlertSeverity
from openexecutive.monitoring import describe
from openexecutive.monitoring import pipeline as mp
from openexecutive.monitoring.models import Signal, WatchlistItem
from openexecutive.monitoring.sources.page_watch import only_counts_or_marks
from openexecutive.monitoring.sources.query import published_at, same_story, story_words


def _item(signal_type: str = "query", trigger: dict | None = None) -> WatchlistItem:
    return WatchlistItem(slug="w", signal_type=signal_type, target="acme", trigger_json=trigger or {})


def _signal(kind: str = "query") -> Signal:
    return Signal(
        watchlist_id=1, source_kind=kind, source_external_id="e", captured_at=datetime.now(UTC).isoformat(),
        normalized_summary="s", raw_payload={}, provenance_url="https://x.example/", dedup_key="k",
        severity_hint=AlertSeverity.LOW,
    )


@pytest.mark.parametrize(
    ("score", "where"), [(0.9, "card"), (0.7, "card"), (0.55, "brief"), (0.4, "brief"), (0.2, "page")],
)
def test_closeness_places_a_find(score: float, where: str) -> None:
    assert mp.placement(_item(), _signal(), {"relevance_score": score}) == where


def test_a_find_with_no_read_or_a_self_judging_source_is_a_card() -> None:
    assert mp.placement(_item(), _signal(), None) == "card"
    assert mp.placement(_item("stock"), _signal("stock"), {"relevance_score": 0.1}) == "card"
    assert mp.placement(_item(trigger={"every_change": True}), _signal(), {"relevance_score": 0.1}) == "card"


def test_the_same_story_reworded_is_caught() -> None:
    a = story_words("Open Executive launches nine AI specialists for small firms")
    b = story_words("Small firms get nine AI specialists as Open Executive launches")
    c = story_words("Initech raises prices for its enterprise plan")
    assert same_story(a, b)
    assert not same_story(a, c)
    # Too few words to tell.
    assert not same_story(story_words("Acme news"), story_words("Acme news"))


@pytest.mark.parametrize(
    ("raw", "day"),
    [
        ("2026-08-14", "2026-08-14"),
        ("August 14, 2026", "2026-08-14"),
        ("14 Aug 2026", "2026-08-14"),
        ("2026-08-14T10:00:00Z", "2026-08-14"),
    ],
)
def test_a_publish_date_is_read(raw: str, day: str) -> None:
    got = published_at(raw)
    assert got is not None and got.startswith(day)


@pytest.mark.parametrize("raw", ["", None, "recently", "last week"])
def test_an_unreadable_publish_date_is_none(raw: object) -> None:
    assert published_at(raw) is None


def test_only_a_count_changing_is_not_news() -> None:
    assert only_counts_or_marks("Tracking 25 SaaS companies", "Tracking 26 SaaS companies")
    assert only_counts_or_marks("630 stars on GitHub", "634 stars on GitHub")
    assert only_counts_or_marks("Our team's plan", "Our team’s plan.")
    # A price or a rate is news; so is any changed word.
    assert not only_counts_or_marks("Pro is $29/mo", "Pro is $40/mo")
    assert not only_counts_or_marks("Save 10% today", "Save 20% today")
    assert not only_counts_or_marks("Pro plan", "Team plan")
    assert not only_counts_or_marks("same", "same")


def test_every_change_is_kept_only_where_closeness_places_finds() -> None:
    assert describe.clean_trigger("page_watch", {"every_change": True}).get("every_change") is True
    assert "every_change" not in describe.clean_trigger("stock", {"every_change": True})


# --------------------------------------------------------------------- #
# A watch's card is reviewed once
# --------------------------------------------------------------------- #


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "review.db"
    monkeypatch.setattr(alert_store, "DB_PATH", db_path)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_ttl_settings", lambda: (3, 14))
    alert_store.initialize_db(db_path)
    return db_path


def _reviewed_card(db: Path, *, watch: bool, reviewed: datetime, seen: datetime | None) -> int:
    aid = alert_store.insert_alert(
        source="query" if watch else "email", external_id=f"x-{watch}", severity="medium",
        headline="h", body="b", topic_tags=["external:query", "external:news-acme"] if watch else [],
        dedup_key="watch:news-acme" if watch else "mail-1", db_path=db,
    )
    assert aid is not None
    with alert_store._get_conn(db) as conn:
        conn.execute(
            "UPDATE alerts SET created_at = ?, last_reviewed_at = ?, last_seen_at = ? WHERE id = ?",
            ((reviewed - timedelta(days=1)).isoformat(), reviewed.isoformat(),
             seen.isoformat() if seen else None, aid),
        )
    return aid


def test_a_reviewed_watch_card_is_looked_at_again_only_when_a_find_joins_it(db: Path) -> None:
    now = datetime.now(UTC)
    reviewed = now - timedelta(hours=12)
    quiet = _reviewed_card(db, watch=True, reviewed=reviewed, seen=None)
    settings = review.ReviewSettings(min_age_hours=0, interval_hours=6, max_per_scan=10)

    ids = [a.id for a in review.select_candidates(now, settings, db_path=db)]
    assert quiet not in ids

    with alert_store._get_conn(db) as conn:
        conn.execute("UPDATE alerts SET last_seen_at = ? WHERE id = ?", ((now - timedelta(hours=1)).isoformat(), quiet))
    ids = [a.id for a in review.select_candidates(now, settings, db_path=db)]
    assert quiet in ids


def test_other_cards_are_still_reviewed_on_the_interval(db: Path) -> None:
    now = datetime.now(UTC)
    other = _reviewed_card(db, watch=False, reviewed=now - timedelta(hours=12), seen=None)
    settings = review.ReviewSettings(min_age_hours=0, interval_hours=6, max_per_scan=10)
    assert other in [a.id for a in review.select_candidates(now, settings, db_path=db)]


# --------------------------------------------------------------------- #
# A web search reports a story once, with its date
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_search_drops_stories_already_reported_and_dates_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    from openexecutive.monitoring.sources import query as q

    seen_content: list[str] = []

    class _Agent:
        async def analyze_with_tools(self, content: str, **_: object) -> object:
            seen_content.append(content)
            return object()

    monkeypatch.setattr(q, "build_web_search_tool", lambda: {"type": "web_search"})
    monkeypatch.setattr(q, "_QueryResearchAgent", _Agent)
    monkeypatch.setattr(q, "validate_target_url", lambda url: (True, ""))
    monkeypatch.setattr(q, "_reported", lambda item, db_path=None: [
        ("https://news.example/old", "Open Executive launches nine AI specialists for small firms"),
    ])
    monkeypatch.setattr(q, "_extract_results", lambda message, slug: [
        {"title": "Small firms get nine AI specialists as Open Executive launches",
         "url": "https://other.example/rewrite", "summary": "s", "published": "2026-08-14"},
        {"title": "Same link again", "url": "https://news.example/old", "summary": "s"},
        {"title": "Initech raises prices for its enterprise plan",
         "url": "https://news.example/initech", "summary": "s", "published": "November 20, 2026"},
    ])
    item = WatchlistItem(id=1, slug="news-oe", signal_type="query", target="Open Executive news")

    signals = await q.QuerySource().poll(item)

    assert [s.provenance_url for s in signals] == ["https://news.example/initech"]
    assert signals[0].published_at is not None and signals[0].published_at.startswith("2026-11-20")
    assert "<already_reported>" in seen_content[0]
    assert "Open Executive launches nine AI specialists" in seen_content[0]
