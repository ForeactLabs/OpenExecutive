"""Which picture a "What I did" step shows, from the tool its round ran.

The timeline's dots say what each step touched: an envelope for email, a
calendar, an eye for reading a page, a pointer for using one, a person, and
so on. A step on a website also carries that site's address (``site``), so
the chat can show the site's own icon (its favicon, ``api/routes/favicon``)
in place of the generic one.

The names here are the contract with the web chat and the phone: each one
draws its own picture for every name in ``ICONS``, and anything it does not
know falls back to the step's group (Looked at / Worked out / Did).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

MAIL = "mail"
CALENDAR = "calendar"
# A website: the site's favicon when the step knows the site.
WEB = "web"
# Reading what is on a page.
PAGE = "page"
# Clicking, typing or choosing on a page.
CLICK = "click"
DOC = "doc"
PERSON = "person"
CHAT = "chat"
PHONE = "phone"
MEMORY = "memory"
THINK = "think"
TASK = "task"
WORKFLOW = "workflow"
# A card for the person's OK, an alert, the watchlist.
CARD = "card"
# A connected app, or Open Executive itself.
APP = "app"

ICONS = frozenset({
    MAIL, CALENDAR, WEB, PAGE, CLICK, DOC, PERSON, CHAT, PHONE, MEMORY, THINK, TASK, WORKFLOW, CARD, APP,
})

_BY_TOOL: dict[str, str] = {
    # Email: the Executive's own mailbox and the person's.
    "search_my_email": MAIL,
    "read_my_email": MAIL,
    "read_my_email_attachment": MAIL,
    "save_my_email_attachment": MAIL,
    "ghostwrite_email": MAIL,
    "my_email_awaiting_reply": MAIL,
    "my_email_read_before": MAIL,
    "what_came_in": MAIL,
    "mark_draft_sent": MAIL,
    # Calendar
    "my_calendar": CALENDAR,
    "create_calendar_event": CALENDAR,
    "create_instant_meeting": CALENDAR,
    "cancel_calendar_event": CALENDAR,
    # The web and the person's browser
    "run_executive_research": WEB,
    "browser_navigate": WEB,
    "browser_open_tab": WEB,
    "browser_switch_tab": WEB,
    "browser_tabs": WEB,
    "browser_show_page": APP,
    "browser_look": PAGE,
    "browser_scroll": PAGE,
    "browser_point": PAGE,
    "browser_click": CLICK,
    "browser_click_at": CLICK,
    "browser_type": CLICK,
    "browser_select": CLICK,
    "browser_key": CLICK,
    "browser_hand_over": CARD,
    # Files and documents
    "read_document": DOC,
    "draft_artifact": DOC,
    "save_file": DOC,
    "run_python_job": DOC,
    "list_artifacts": DOC,
    "get_artifact": DOC,
    # People
    "lookup_person": PERSON,
    "list_people": PERSON,
    "upsert_person": PERSON,
    "archive_person": PERSON,
    "resolve_roster_request": PERSON,
    "set_department_head": PERSON,
    "ask_about_person": PERSON,
    # Messages
    "send_slack_dm": CHAT,
    "send_discord_dm": CHAT,
    "send_telegram_message": CHAT,
    "message_person": CHAT,
    "send_department_message": CHAT,
    "send_company_broadcast": CHAT,
    # Phone and texts
    "read_texts": PHONE,
    "send_text": PHONE,
    "call_for_me": PHONE,
    "read_phone_messages": PHONE,
    # Memory
    "recall_history": MEMORY,
    "search_past_chats": MEMORY,
    "remember_fact": MEMORY,
    "keep_figure": MEMORY,
    "forget_fact": MEMORY,
    "update_company_profile": MEMORY,
    "record_decision_outcome": MEMORY,
    # Thinking it through
    "consult_specialist": THINK,
    # Tasks, follow-ups, reminders and goals
    "remind_me": TASK,
    "schedule_followup": TASK,
    "list_followups": TASK,
    "cancel_followup": TASK,
    "move_followup": TASK,
    "list_open_loops": TASK,
    "close_open_loop": TASK,
    "update_open_loop": TASK,
    "reopen_open_loop": TASK,
    "assign_open_loop": TASK,
    "list_department_goals": TASK,
    "update_department_goal": TASK,
    "create_goal": TASK,
    "leave_out_of_brief": TASK,
    # Workflows and skills
    "run_workflow": WORKFLOW,
    "draft_workflow": WORKFLOW,
    "save_workflow": WORKFLOW,
    "list_workflows": WORKFLOW,
    "suggest_workflow": WORKFLOW,
    "search_skills": WORKFLOW,
    "load_skill": WORKFLOW,
    # Cards, alerts and the watchlist
    "propose_actions": CARD,
    "answer_card": CARD,
    "ack_alert": CARD,
    "find_alerts": CARD,
    "create_alert": CARD,
    "add_watchlist_entry": CARD,
    "list_watchlist": CARD,
    "remove_watchlist_entry": CARD,
    "tune_watchlist_entry": CARD,
    "list_my_activity": CARD,
    # Connected apps and the app itself
    "call_tool": APP,
    "search_tools": APP,
    "load_mcp_server": APP,
    "find_in_app": APP,
    "do_in_app": APP,
    "open_in_app": APP,
    "read_user_guide": APP,
    "propose_form_values": APP,
}

# A connected app's tool (``server__tool``), by the words in its name.
_APP_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gmail|outlook|mail|inbox"), MAIL),
    (re.compile(r"calendar|event|meeting"), CALENDAR),
    (re.compile(r"drive|onedrive|sharepoint|docs?_|sheet|file|document|notion|confluence"), DOC),
    (re.compile(r"slack|teams|discord|telegram|message|chat"), CHAT),
    (re.compile(r"contact|people|person"), PERSON),
)

# The browser tools that open an address. The site a step was on comes from
# what the browser reports after it ran, never from the address asked for.
_OPENS = frozenset({"browser_navigate", "browser_open_tab"})
# A step on a page carries the site it is on.
_ON_PAGE = frozenset({
    "browser_navigate", "browser_open_tab", "browser_look", "browser_scroll", "browser_point",
    "browser_click", "browser_click_at", "browser_type", "browser_select", "browser_key",
})
# These move to a page whose address the round doesn't say.
_LEAVES = frozenset({"browser_switch_tab", "browser_tabs", "browser_show_page"})

_HOST = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def icon_for(tool: str, kind: str = "") -> str:
    """The picture for a round of ``tool``; for no tool or one it doesn't
    know, ``""`` (the chat then shows the step's group, ``kind``)."""
    tool = (tool or "").removeprefix("oe__")
    if tool in _BY_TOOL:
        return _BY_TOOL[tool]
    if "__" in tool:
        name = tool.lower()
        for pattern, icon in _APP_WORDS:
            if pattern.search(name):
                return icon
        return APP
    if tool:
        # An installed extension's own tool.
        return APP
    return THINK if kind == "work" else ""


def site_of(url: Any) -> str:
    """The host of an http(s) address, lowercased and without ``www.``, or
    ``""`` when it isn't one (a path, an IP address, localhost)."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host if valid_site(host) else ""


def valid_site(host: Any) -> bool:
    """Whether ``host`` is a public-looking domain name (letters, digits,
    hyphens and dots, with a dot, ending in a name that starts with a
    letter): never an IP address in any spelling (``10.0.0.1``,
    ``0x7f.0x1``) or a single-label name like ``localhost``."""
    if not isinstance(host, str) or not _HOST.match(host):
        return False
    return host.rsplit(".", 1)[-1][:1].isalpha()


def opens_page(tool: str) -> bool:
    """Whether a round of ``tool`` opens an address: the page it is on is
    known only once the browser reports it (``step_site``)."""
    return tool.removeprefix("oe__") in _OPENS


def on_page(tool: str) -> bool:
    """Whether a round of ``tool`` happens on the open page (it keeps the
    site the turn last opened)."""
    return tool.removeprefix("oe__") in _ON_PAGE


def leaves_page(tool: str) -> bool:
    """Whether a round of ``tool`` moves to a page the turn can't name."""
    return tool.removeprefix("oe__") in _LEAVES
