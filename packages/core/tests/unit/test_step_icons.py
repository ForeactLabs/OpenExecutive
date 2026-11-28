"""The picture each "What I did" step shows (orchestrator/step_icons.py)."""
from __future__ import annotations

from openexecutive.orchestrator import step_icons
from openexecutive.orchestrator.activity_labels import summarize_activity


def test_every_mapped_tool_has_a_known_icon() -> None:
    assert set(step_icons._BY_TOOL.values()) <= step_icons.ICONS


def test_unknown_tools_fall_back() -> None:
    assert step_icons.icon_for("") == ""
    assert step_icons.icon_for("", "work") == "think"
    assert step_icons.icon_for("some_extension_tool") == "app"
    assert step_icons.icon_for("ms365__list_calendar_events") == "calendar"
    assert step_icons.icon_for("oe__browser_look") == "page"


def test_site_of_keeps_only_public_names() -> None:
    assert step_icons.site_of("https://www.PapaJohns.com/order/specialty") == "papajohns.com"
    assert step_icons.site_of("http://shop.example.co.uk:8080/x") == "shop.example.co.uk"
    for bad in ("ftp://example.com", "https://127.0.0.1/", "http://localhost:3000", "/knowledge", "", None,
                "https://[::1]/", "javascript:alert(1)"):
        assert step_icons.site_of(bad) == ""


def test_valid_site_never_takes_an_ip_in_any_spelling() -> None:
    assert step_icons.valid_site("papajohns.com") and step_icons.valid_site("123.example.com")
    for bad in ("10.0.0.1", "0x7f.0x1", "127.1", "1.2.3.0x4", "localhost", "a.b-", "example.com\n"):
        assert not step_icons.valid_site(bad)


def test_the_activity_never_carries_the_asked_for_address() -> None:
    evt = summarize_activity([{"id": "1", "name": "browser_open_tab", "input": {"url": "https://www.papajohns.com/"}}])
    assert evt is not None and "site" not in evt
    assert step_icons.opens_page("oe__browser_navigate") and not step_icons.opens_page("browser_look")
