"""App control from chat: the Executive does what the app's own buttons do.

"Pin this", "rename it Q3 plan", "delete that chat", "turn off Handle it for
me": three tools in the ``app`` tool group (``tool_groups``) do these.

- ``find_in_app(kind, query)`` lists the things of one kind the speaker may
  act on (their chats, topics, documents, …), each with an id.
- ``do_in_app(action, id, value)`` does one action on one of them.
- ``open_in_app(screen)`` gives a link to a screen, for what chat doesn't do.

**The same code as the button.** Each action calls the API route the screen's
button calls, as a function, with a request that names the speaker
(``_as_speaker``). The route's own checks decide what this person may do: a
chat that isn't theirs is a 404 here as it is on the page. The speaker is the
one the chat surface verified (``orchestrator.speaker``); a turn with
none (an email, an unattended run) gets a refusal. Some routes check nothing
about the caller because their page is the principal's; those actions are
``principal_only``.

**What waits for a tap.** The one gate decides (``take_the_lead.in_app``):
Your rules, the level, In training, then what this turn noticed (text from
outside the team or the speaker's own mail, ``levels``), then on Balanced
an action's topic (a delete or a share, ``Action.topic``). Hands-off runs
everything but a Waits rule. A step that waits becomes a card in the chat
(decision class ``app_action``, ``api.routes.decisions``) that only the
speaker can see and answer, with the action spelled out. Their tap runs it
with their own request to ``/decisions/{id}/approve``, so a route that needs
a signed sign-in gets one, and the route checks them again then; a route
that wants that signed request (turning on something that sends as them,
letting more go) leaves the card too. A card In training held allows that
action from then on when it is approved. A card left a day goes stale.

**Adding to it.** ``register_kind`` / ``register_action`` / ``register_screen``
add to the catalog (``app_control_catalog`` holds this repo's), at startup
before any turn, so each turn sees one fixed tool list. The tool definitions
are rebuilt in place, so the list holding them sees the change.
"""
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from openexecutive.delegation.verified import from_chat

logger = logging.getLogger(__name__)

FIND_IN_APP = "find_in_app"
DO_IN_APP = "do_in_app"
OPEN_IN_APP = "open_in_app"
APP_TOOL_NAMES: frozenset[str] = frozenset({FIND_IN_APP, DO_IN_APP, OPEN_IN_APP})

# The decision class a card is stored as (api.routes.decisions).
DECISION_CLASS = "app_action"
# A card not answered in this long is stale: what it named may have changed.
CARD_LIFETIME = timedelta(days=1)
# What "this" means for a chat: the conversation the person is in.
THIS = "this"

_FIND_MAX = 25
_VALUE_MAX = 300


class Refused(Exception):
    """An action that can't be done, with what to tell the person."""


@dataclass(frozen=True)
class Item:
    """One thing in the app the speaker may act on."""

    id: str
    title: str
    detail: str = ""
    # Only the speaker sees it (their own chats and topics, a decision they
    # kept to themselves): left out of a list in a conversation others read.
    private: bool = False


# (request) → everything of the kind the caller may act on.
Finder = Callable[[Any], Awaitable[list[Item]]]
# (request, item id, value) → what was done, past tense ("Pinned “Q3 plan”").
# Raises Refused, or lets the route's HTTPException through.
Runner = Callable[[Any, str, str], Awaitable[str]]
# (request, item id, value) → the value to store and run with, worked out by
# the server in the turn (e.g. the alert ids shown), or Refused.
Preparer = Callable[[Any, str, str], Awaitable[str]]


@dataclass(frozen=True)
class Kind:
    name: str
    # What the person calls one, e.g. "chat".
    label: str
    find: Finder
    principal_only: bool = False
    # Items of this kind need no id (a new department, the alert board).
    no_item: bool = False


@dataclass(frozen=True)
class Action:
    name: str
    kind: str
    # What it does, for the tool description: "Pin a chat to the top of the list".
    does: str
    run: Runner
    # The Balanced topic it is (``take_the_lead.DELETE`` / ``SHARE``), for
    # the one gate (``take_the_lead.in_app``) to weigh.
    topic: str | None = None
    # What ``value`` is, when the action takes one.
    value: str | None = None
    # The card's line, with {title} (quoted) and {value}: "Delete the chat {title}".
    card: str = ""
    principal_only: bool = False
    prepare: Preparer | None = None
    # Not offered to do_in_app: only a card another tool leaves runs it (a
    # workflow change ``save_workflow`` stages, ``workflow_change``).
    hidden: bool = False


@dataclass
class _Catalog:
    kinds: dict[str, Kind] = field(default_factory=dict)
    actions: dict[str, Action] = field(default_factory=dict)
    # name → (app path, what it is)
    screens: dict[str, tuple[str, str]] = field(default_factory=dict)


CATALOG = _Catalog()


# --------------------------------------------------------------------------- #
# Tool definitions (rebuilt in place as the catalog grows)
# --------------------------------------------------------------------------- #

FIND_IN_APP_TOOL: dict[str, Any] = {"name": FIND_IN_APP}
DO_IN_APP_TOOL: dict[str, Any] = {"name": DO_IN_APP}
OPEN_IN_APP_TOOL: dict[str, Any] = {"name": OPEN_IN_APP}


def _rebuild() -> None:
    kinds = sorted(k for k, kind in CATALOG.kinds.items() if not kind.no_item)
    FIND_IN_APP_TOOL.update({
        "description": (
            "List things in Open Executive the person can change, with their ids: "
            + ", ".join(f"{k} ({CATALOG.kinds[k].label}s)" for k in kinds)
            + ". Call it before do_in_app to find the id of what they named. query "
            "narrows by words in the name; leave it out to list the newest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": kinds},
                "query": {"type": "string", "description": "Words from its name, as the person said them."},
            },
            "required": ["kind"],
        },
    })
    offered = sorted(name for name, action in CATALOG.actions.items() if not action.hidden)
    lines = []
    for name in offered:
        action = CATALOG.actions[name]
        extra = f" value: {action.value}." if action.value else ""
        lines.append(f"- {name} ({action.kind}): {action.does}.{extra}")
    DO_IN_APP_TOOL.update({
        "description": (
            "Do something in Open Executive that the person asked for, as its own "
            "button would: pin, rename, delete, share, turn a setting on or off. "
            f"id comes from find_in_app; for the chat you are in, use id \"{THIS}\". "
            "Approvals decides whether it is done now or leaves a card for them to confirm "
            "(a delete or a share waits on Balanced; raising the level or adding a Goes rule "
            "takes their tap): if the result says waiting_for_approval, say it "
            "is waiting for their OK, "
            "never that it is done. If it is refused, say why "
            "in their words; don't retry.\n" + "\n".join(lines)
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": offered},
                "id": {"type": "string", "description": "The item's id from find_in_app, or \"this\" for this chat."},
                "value": {"type": "string", "description": "The action's value, when it takes one."},
            },
            "required": ["action", "id"],
        },
    })
    screens = sorted(CATALOG.screens)
    OPEN_IN_APP_TOOL.update({
        "description": (
            "Give the person a link to a screen in Open Executive, for something chat "
            "can't do or when they ask to go somewhere ('open my settings'). Screens: "
            + "; ".join(f"{s} ({CATALOG.screens[s][1]})" for s in screens)
            + ". The link shows under your reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"screen": {"type": "string", "enum": screens}},
            "required": ["screen"],
        },
    })


def register_kind(kind: Kind) -> None:
    if kind.name in CATALOG.kinds:
        raise ValueError(f"kind {kind.name!r} is already registered")
    CATALOG.kinds[kind.name] = kind
    _rebuild()


def register_action(action: Action) -> None:
    if action.name in CATALOG.actions:
        raise ValueError(f"action {action.name!r} is already registered")
    if action.kind not in CATALOG.kinds:
        raise ValueError(f"action {action.name!r} names unknown kind {action.kind!r}")
    CATALOG.actions[action.name] = action
    _rebuild()


def register_screen(name: str, path: str, label: str) -> None:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError(f"screen {name!r} needs an app path")
    CATALOG.screens[name] = (path, label)
    _rebuild()


# --------------------------------------------------------------------------- #
# Who is asking, and a request that names them
# --------------------------------------------------------------------------- #


def _session() -> Any:
    from openexecutive.orchestrator.schedule_tools import current_session

    return current_session.get()


def _speaker() -> Any:
    from openexecutive.orchestrator.speaker import speaker

    return speaker()


def _running_app() -> Any:
    """The API's app when it is loaded (its knowledge store is on its state),
    else a stand-in whose state has nothing, so routes build their own."""
    main = sys.modules.get("openexecutive.api.main")
    app = getattr(main, "app", None)
    return app if app is not None else SimpleNamespace(state=SimpleNamespace())


class _AsSpeaker:
    """A request naming the turn's verified speaker, for calling a route as a
    function in the turn. Only ever made from ``_speaker()``, never from
    anything the model wrote."""

    def __init__(self, person: Any, body: dict[str, Any] | None = None) -> None:
        from openexecutive.api.caller import Caller

        email = str(getattr(person, "email", "") or "").strip().lower()
        if email:
            who = Caller("user", email)
        elif getattr(person, "is_principal", False):
            # A principal with no email is who a request naming no one is.
            who = Caller("operator")
        else:
            raise Refused("I can't tell the app who you are. Do this on the screen instead.")
        self.state = SimpleNamespace(caller=who)
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.client = None
        self.app = _running_app()
        self._body = body or {}

    async def json(self) -> Any:
        return self._body


class _WithBody:
    """A real request with the JSON body a route reads set to ``body``: for a
    route that reads its own body, run on a tap whose body is the card's."""

    def __init__(self, request: Any, body: dict[str, Any]) -> None:
        self._request = request
        self._body = body

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)

    async def json(self) -> Any:
        return self._body


def with_body(request: Any, body: dict[str, Any]) -> Any:
    """``request`` reading ``body`` as its JSON (for the catalog)."""
    if isinstance(request, _AsSpeaker):
        request._body = body
        return request
    return _WithBody(request, body)


def refusal_text(exc: BaseException) -> str:
    """What a route's refusal says, in the person's words."""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("detail")
    if isinstance(detail, list):
        detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    text = " ".join(str(detail or exc or "").split())
    if getattr(exc, "status_code", None) == 404 or not text:
        return text or "That isn't there, or isn't yours to change."
    return text[:300]


def _error(text: str) -> str:
    return json.dumps({"error": text})


def _clean_value(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:_VALUE_MAX]


def _noticed(session: Any) -> str | None:
    """What this turn read that the gate weighs (a ``levels`` reason): text
    from outside the team (``levels.outside_reason``), or mail whose read
    the safety check notices (``levels.mail_reason``). None when neither.
    It only notices; ``take_the_lead.in_app`` decides. Never raises: an
    error is mail it couldn't check."""
    from openexecutive.orchestrator import levels

    try:
        from openexecutive.delegation.lockdown import mail_lock_holds
        from openexecutive.delegation.settings import turn_delegation
        from openexecutive.orchestrator import outside_content

        if outside_content.read_outside():
            return levels.outside_reason()
        pinned = turn_delegation(session)
        if pinned is not None and (getattr(pinned, "touched_mail", False) is True or mail_lock_holds(pinned)):
            return levels.mail_reason(pinned)
        return None
    except Exception:
        logger.warning("app_control: couldn't tell what the turn read", exc_info=True)
        return levels.MAIL_UNCHECKED


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


async def _items(kind: Kind, request: Any) -> list[Item]:
    from fastapi import HTTPException

    try:
        return await kind.find(request)
    except Refused as exc:
        raise exc
    except HTTPException as exc:
        raise Refused(refusal_text(exc)) from None


def _matches(item: Item, query: str) -> bool:
    words = [w for w in query.lower().split() if w]
    text = f"{item.title} {item.detail} {item.id}".lower()
    return all(w in text for w in words)


async def handle_find_in_app(tool_input: dict[str, Any]) -> str:
    person = _speaker()
    if person is None:
        return _error("I can only do this when I know who is asking: in the web app signed in, or your own chat app.")
    kind = CATALOG.kinds.get(str(tool_input.get("kind") or ""))
    if kind is None:
        return _error(f"kind must be one of {', '.join(sorted(CATALOG.kinds))}")
    if kind.principal_only and not getattr(person, "is_principal", False):
        return _error(f"Only the account owner can change {kind.label}s.")
    try:
        items = await _items(kind, _AsSpeaker(person))
    except Refused as exc:
        return _error(str(exc))
    except Exception:
        logger.exception("find_in_app: listing %s failed", kind.name)
        return _error("Couldn't read that list just now.")
    query = _clean_value(tool_input.get("query"))
    found = [i for i in items if _matches(i, query)] if query else items
    shown, hidden = _hide_private(found)
    return json.dumps({
        "kind": kind.name,
        "items": [
            {"id": i.id, "title": i.title, **({"detail": i.detail} if i.detail else {})}
            for i in shown[:_FIND_MAX]
        ],
        **({"more": len(shown) - _FIND_MAX} if len(shown) > _FIND_MAX else {}),
        **({"private_left_out": hidden, "private_note": _PRIVATE_NOTE} if hidden else {}),
    })


_PRIVATE_NOTE = (
    "Others read this conversation, so what is private to the speaker is left out of this "
    "list. They see it in a chat only they read, or on its screen."
)


def _hide_private(items: list[Item]) -> tuple[list[Item], int]:
    """Privacy hides: in a conversation others read, leave out what only the
    speaker sees (the chat itself stays, everyone in it reads it already)."""
    from openexecutive.delegation.settings import private_conversation

    session = _session()
    if private_conversation(session):
        return items, 0
    here = str(getattr(session, "session_id", "") or "")
    shown = [i for i in items if not i.private or (here and i.id == here)]
    return shown, len(items) - len(shown)


def card_text(action: Action, title: str, value: str) -> str:
    """What a card says it will do, naming the item and the new value, so a
    card left after outside content was read shows exactly what a tap does."""
    named = f"“{title}”" if title else "it"
    if action.card:
        return action.card.format(title=named, value=value)
    text = action.does + (f": {named}" if title else "")
    return text + (f" → “{value}”" if value else "")


async def _title_of(kind: Kind, request: Any, item_id: str) -> str | None:
    """The item's name when the speaker may act on it, else None."""
    if kind.no_item:
        return ""
    for item in await _items(kind, request):
        if item.id == item_id:
            return item.title
    return None


def _audit(event: str, summary: str, details: dict[str, Any]) -> None:
    from openexecutive.audit import log_event

    session = _session()
    try:
        log_event(
            event, summary, session_id=getattr(session, "session_id", None),
            actor="executive", details=details,
        )
    except Exception:
        logger.warning("app_control: audit write failed", exc_info=True)


async def handle_do_in_app(tool_input: dict[str, Any]) -> str:
    from fastapi import HTTPException

    session = _session()
    person = _speaker()
    if person is None or getattr(person, "id", None) is None:
        return _error("I can only do this when I know who is asking: in the web app signed in, or your own chat app.")
    action = CATALOG.actions.get(str(tool_input.get("action") or ""))
    if action is None or action.hidden:
        return _error("Unknown action. Pick one from the list.")
    kind = CATALOG.kinds[action.kind]
    if (action.principal_only or kind.principal_only) and not getattr(person, "is_principal", False):
        return _error("Only the account owner can do that.")
    item_id = _clean_value(tool_input.get("id"))
    if item_id == THIS and action.kind == "chat":
        item_id = str(getattr(session, "session_id", "") or "")
    value = _clean_value(tool_input.get("value"))
    if action.value and not value:
        return _error(f"{action.name} needs a value: {action.value}.")
    if not item_id and not kind.no_item:
        return _error("id is required: find it with find_in_app.")
    try:
        request = _AsSpeaker(person)
        title = await _title_of(kind, request, item_id)
        if title is None:
            return _error(f"That {kind.label} isn't there, or isn't yours to change. Look it up with find_in_app.")
        if action.prepare is not None:
            with from_chat():
                value = await action.prepare(request, item_id, value)
    except Refused as exc:
        return _error(str(exc))
    except HTTPException as exc:
        return _error(refusal_text(exc))
    except Exception:
        logger.exception("do_in_app: checking %s failed", action.name)
        return _error("Couldn't check that just now. Try again in a moment.")

    details = {"action": action.name, "kind": kind.name, "id": item_id, "person_id": person.id}
    hit = _asks_the_engine(action, card_text(action, title or "", value), session)
    if hit is not None:
        return _card_result(
            person.id, session, action, item_id, value, title or "", details,
            why=hit.reason, allow=_training_grant(action) if _in_training(hit) else None,
        )

    try:
        with from_chat():
            did = await action.run(request, item_id, value)
    except Refused as exc:
        return _error(str(exc))
    except HTTPException as exc:
        if exc.status_code == 409 and "caller_signing_required" in str(exc.detail):
            # The route wants the person's own signed request (turning on
            # something that sends as them, changing Your rules), which a
            # chat turn never is: their tap on a card is that request.
            return _card_result(person.id, session, action, item_id, value, title or "", details)
        _audit("app_action_refused", f"{action.name} refused", {**details, "status": exc.status_code})
        return _error(refusal_text(exc))
    except Exception:
        logger.exception("do_in_app: %s failed", action.name)
        return _error("That didn't go through. Try again in a moment, or do it on the screen.")
    _audit("app_action_done", did[:200], details)
    return json.dumps({"status": "done", "did": did})


def _card_result(
    person_id: int, session: Any, action: Action, item_id: str, value: str, title: str, details: dict[str, Any],
    *, why: str = "", allow: dict[str, str] | None = None,
) -> str:
    """Leave the action on a card for the speaker's tap, and say so."""
    card = card_text(action, title, value)
    try:
        decision_id = _leave_card(person_id, session, action, item_id, value, title, card, allow=allow)
    except Exception:
        logger.exception("do_in_app: couldn't leave the card for %s", action.name)
        return _error("Couldn't leave the card just now. Try again in a moment.")
    _audit("app_action_proposed", f"Left a card: {card[:120]}", {**details, "decision_id": decision_id})
    return json.dumps({
        "status": "waiting_for_approval",
        "decision_id": decision_id,
        "card": card,
        **({"why": why} if why else {}),
        "note": waiting_note(session),
    })


def _asks_the_engine(action: Action, does: str, session: Any) -> Any:
    """Ask the one gate (``take_the_lead.in_app``) whether ``action`` runs
    now, passing on what this turn noticed (``_noticed``). Its ``Hit`` when
    it waits, else None."""
    from openexecutive.orchestrator import take_the_lead

    return take_the_lead.in_app(action.name, action.does, does, topic=action.topic, noticed=_noticed(session))


def _in_training(hit: Any) -> bool:
    from openexecutive.orchestrator import take_the_lead

    return getattr(hit, "kind", None) == take_the_lead.TRAINING


def _training_grant(action: Action) -> dict[str, str] | None:
    """What approving a card In training held allows from then on: this
    action, as ``take_the_lead.allowance`` names it."""
    from openexecutive.orchestrator import take_the_lead

    found = take_the_lead.allowance(f"{take_the_lead.APP_STEP}{action.name}", {"label": action.does})
    return {"key": found[0], "label": found[1]} if found is not None else None


async def handle_open_in_app(tool_input: dict[str, Any]) -> str:
    screen = CATALOG.screens.get(str(tool_input.get("screen") or ""))
    if screen is None:
        return _error(f"screen must be one of {', '.join(sorted(CATALOG.screens))}")
    path, label = screen
    return json.dumps({"status": "link", "link": path, "screen": label})


# --------------------------------------------------------------------------- #
# The card: stored, then carried out on the speaker's tap
# --------------------------------------------------------------------------- #


def shown_here(session: Any = None) -> bool:
    """Whether a card left on this turn shows in the chat it was asked in
    (the web chat and the phone app, or a chat app with Approve and Skip
    buttons). Never raises: an error is no."""
    from openexecutive.integrations.channel_cards import card_here

    try:
        return card_here(session if session is not None else _session())
    except Exception:
        logger.warning("app_control: couldn't tell where the card shows", exc_info=True)
        return False


def waiting_note(session: Any = None) -> str:
    """What the Executive tells the person about a card it just left: under
    the reply where the chat shows it, else on Needs you."""
    if shown_here(session):
        return (
            "Nothing has happened yet. A card is under this reply for them to confirm. "
            "Tell them it is waiting for their OK."
        )
    return (
        "Nothing has happened yet. This chat can't show the card, so it is on Needs you in the web "
        "and phone app for them to confirm. Tell them it is waiting there for their OK."
    )


def _leave_card(
    person_id: int, session: Any, action: Action, item_id: str, value: str, title: str, card: str,
    *, allow: dict[str, str] | None = None,
) -> int:
    """Leave the card for ``person_id``, theirs alone. Where the chat can't
    show it (a call, mail, a chat app without buttons) it also goes on their
    Needs you, so a card is never left where nobody sees it: if that fails,
    the card is closed and this raises, so the tool says it couldn't."""
    from openexecutive.alerts import own_chat
    from openexecutive.memory.decision_ledger import (
        close_externally,
        create_decision_instance,
        put_on_needs_you,
    )

    decision_id = create_decision_instance(
        decision_class=DECISION_CLASS,
        department="",
        originating_session_id=getattr(session, "session_id", None),
        proposed_payload={
            "action": action.name, "id": item_id, "value": value, "title": title, "card": card,
            **({"allow": allow} if allow else {}),
        },
        idempotency_key=None,
        gate_mode="propose",
        approver_person_id=person_id,
        confidence=None,
    )
    if not own_chat.keep_with(decision_id, person_id):
        # Not kept to its person, it would show to everyone: never left so.
        close_externally(decision_id, reason="not_kept_with_person")
        raise RuntimeError(f"couldn't keep card {decision_id} with its person")
    if not shown_here(session):
        first, _, rest = card.partition("\n")
        try:
            put_on_needs_you(
                decision_id, DECISION_CLASS, headline=first or title, body=rest.strip() or first,
                suggested_action=first or title, approver_person_id=person_id,
            )
        except Exception:
            logger.exception("app_control: couldn't put card %d on Needs you", decision_id)
            close_externally(decision_id, reason="not_on_needs_you")
            raise
    return decision_id


def leave_card(person_id: int, action: str, item_id: str, value: str, title: str, card: str) -> int:
    """Leave a card for a hidden action another tool stages (its ``value``
    may be longer than ``do_in_app`` takes). Returns the decision id."""
    found = CATALOG.actions.get(action)
    if found is None:
        raise ValueError(f"unknown action {action!r}")
    return _leave_card(person_id, _session(), found, item_id, value, title, card)


def card_stale(created_at: str, now: datetime | None = None) -> bool:
    try:
        made = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return True
    if made.tzinfo is None:
        made = made.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - made > CARD_LIFETIME


async def carry_out(payload: dict[str, Any], request: Any) -> str:
    """Do a card's action with the tapper's own request. Raises Refused, or
    the route's HTTPException."""
    action = CATALOG.actions.get(str(payload.get("action") or ""))
    if action is None:
        raise Refused("That isn't something this app can do any more.")
    return await action.run(request, str(payload.get("id") or ""), str(payload.get("value") or ""))


APP_TOOLS: list[dict[str, Any]] = [DO_IN_APP_TOOL, FIND_IN_APP_TOOL, OPEN_IN_APP_TOOL]
APP_TOOL_HANDLERS: dict[str, Any] = {
    FIND_IN_APP: handle_find_in_app,
    DO_IN_APP: handle_do_in_app,
    OPEN_IN_APP: handle_open_in_app,
}

_rebuild()
