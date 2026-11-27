"""The person's own texts, through Google Messages in their browser (``delegation.texts``).

- ``GET /delegation/texts``: their switch and what their browser last said.
- ``PUT /delegation/texts``: turn texts on or off. Off drops what was kept.
- ``POST /delegation/texts/seen``: the browser extension sends what Google
  Messages shows: whether it is paired, and the texts it sees.
- ``GET /delegation/texts/conversations``: the Texts page: their
  conversations, latest first, each with what the Executive did about it,
  how its latest text from them went, and what was said.
- ``GET /delegation/texts/next``: the extension asks for the next text to
  send, waiting up to ``wait`` seconds for one.
- ``POST /delegation/texts/sends/{id}``: the extension says how a send went.

Every route acts on the signed-in caller's own texts only
(``delegation._caller``). The extension calls them from an Open Executive tab,
signed in as that person, like its browser steps (``api/routes/browser.py``).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from openexecutive.api.routes.delegation import _caller, _person_id
from openexecutive.delegation import texts as tx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/delegation/texts")

# The longest one ask for the next send waits.
MAX_WAIT_S = 20.0


class TextsOut(BaseModel):
    # Whether texts are on for this install. Off: the setting isn't shown
    # and the extension doesn't read anything.
    available: bool
    enabled: bool
    # What their browser last said: Google Messages is paired with their
    # phone, when, and whether Chrome is open now.
    paired: bool
    seen_at: str | None
    connected: bool
    conversations: int


class TextsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class TextItemIn(BaseModel):
    ref: str = Field(min_length=1, max_length=300)
    conversation: str = Field(min_length=1, max_length=300)
    title: str = Field(default="", max_length=200)
    sender: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=tx.MAX_TEXT)
    at: datetime
    number: str = Field(default="", max_length=40)
    is_group: bool = False
    outgoing: bool = False
    earlier: bool = False


class SeenIn(BaseModel):
    paired: bool = False
    items: list[TextItemIn] = Field(default_factory=list, max_length=tx.MAX_REPORT)


class SeenOut(BaseModel):
    enabled: bool
    received: int


class SendOut(BaseModel):
    id: int
    # Google Messages' key for the conversation, or "" for a new one to ``number``.
    conversation: str
    number: str
    text: str


class NextOut(BaseModel):
    enabled: bool
    send: SendOut | None


class SendResultIn(BaseModel):
    status: Literal["sent", "failed", "unsure"]
    error: str = Field(default="", max_length=300)
    # For a text to a new number: the conversation it went to.
    conversation: str = Field(default="", max_length=300)


class SendResultOut(BaseModel):
    id: int
    status: str


class ThreadTextOut(BaseModel):
    # "" for their own texts (``outgoing``), else who sent it.
    sender: str
    text: str
    outgoing: bool
    at: str


class ThreadOut(BaseModel):
    id: str
    who: str
    number: str
    is_group: bool
    at: str
    note: str
    status: Literal["read", "sending", "sent", "failed", "unsure"]
    texts: list[ThreadTextOut]


class ThreadsOut(BaseModel):
    enabled: bool
    conversations: list[ThreadOut]


async def _state(person_id: int) -> TextsOut:
    settings = await asyncio.to_thread(tx.get_settings, person_id)
    convs = await asyncio.to_thread(tx.conversations, person_id, limit=200)
    return TextsOut(
        available=settings.available,
        enabled=settings.enabled,
        paired=settings.paired,
        seen_at=settings.seen_at,
        connected=tx.connected(person_id),
        conversations=len(convs),
    )


@router.get("", response_model=TextsOut)
async def get_texts(request: Request) -> TextsOut:
    return await _state(_person_id(_caller(request)))


@router.put("", response_model=TextsOut)
async def put_texts(request: Request, body: TextsIn) -> TextsOut:
    person_id = _person_id(_caller(request))
    before = await asyncio.to_thread(tx.get_settings, person_id)
    if body.enabled and not before.available:
        raise HTTPException(status_code=404, detail="Texts aren't turned on here.")
    await asyncio.to_thread(tx.save_settings, person_id, enabled=body.enabled)
    if body.enabled != before.enabled:
        _audit(person_id, body.enabled)
    return await _state(person_id)


def _audit(person_id: int, enabled: bool) -> None:
    try:
        from openexecutive.audit import log_event

        log_event(
            "delegation_texts_changed",
            f"Texts turned {'on' if enabled else 'off'} by person {person_id}",
            details={"person_id": person_id, "enabled": enabled},
        )
    except Exception:
        logger.debug("texts: audit row failed", exc_info=True)


@router.get("/conversations", response_model=ThreadsOut)
async def get_conversations(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> ThreadsOut:
    """The caller's own conversations, for the Texts page. Empty while
    texts are off for them or for the install."""
    person_id = _person_id(_caller(request))
    settings = await asyncio.to_thread(tx.get_settings, person_id)
    if not settings.active:
        return ThreadsOut(enabled=False, conversations=[])
    found = await asyncio.to_thread(tx.threads, person_id, limit=limit)
    return ThreadsOut(
        enabled=True,
        conversations=[
            ThreadOut(
                id=t.id, who=t.who, number=t.number, is_group=t.is_group, at=t.at, note=t.note,
                status=t.status,  # type: ignore[arg-type]
                texts=[ThreadTextOut(sender="" if x.outgoing else x.sender, text=x.text, outgoing=x.outgoing, at=x.at)
                       for x in t.texts],
            )
            for t in found
        ],
    )


@router.post("/seen", response_model=SeenOut)
async def seen(request: Request, body: SeenIn) -> SeenOut:
    person_id = _person_id(_caller(request))
    settings = await asyncio.to_thread(tx.get_settings, person_id)
    if not settings.active:
        return SeenOut(enabled=False, received=0)
    await asyncio.to_thread(tx.browser_seen, person_id, paired=body.paired)
    fresh: list[int] = []
    if body.items:
        items = [
            tx.Incoming(
                ref=i.ref, conversation=i.conversation, title=i.title, sender=i.sender, text=i.text,
                at=i.at if i.at.tzinfo else i.at.replace(tzinfo=UTC), number=i.number, is_group=i.is_group,
                outgoing=i.outgoing, earlier=i.earlier,
            )
            for i in body.items
        ]
        fresh = await asyncio.to_thread(tx.record, person_id, items)
        if fresh:
            from openexecutive.delegation import texts_watch

            texts_watch.notice(person_id)
    return SeenOut(enabled=True, received=len(fresh))


@router.get("/next", response_model=NextOut)
async def next_send(request: Request, wait: float = Query(default=MAX_WAIT_S, ge=0)) -> NextOut:
    person_id = _person_id(_caller(request))
    settings = await asyncio.to_thread(tx.get_settings, person_id)
    if not settings.active:
        return NextOut(enabled=False, send=None)
    taken = await tx.next_send(person_id, wait_s=min(wait, MAX_WAIT_S), gone=request.is_disconnected)
    if taken is None:
        return NextOut(enabled=True, send=None)
    send, key = taken
    return NextOut(enabled=True, send=SendOut(id=send.id, conversation=key, number=send.to_number, text=send.text))


@router.post("/sends/{send_id}", response_model=SendResultOut)
async def send_result(request: Request, send_id: int, body: SendResultIn) -> SendResultOut:
    person_id = _person_id(_caller(request))
    settled = await asyncio.to_thread(
        tx.settle_send, person_id, send_id, status=body.status, error=body.error, conversation_key=body.conversation,
    )
    if settled is None:
        raise HTTPException(status_code=404, detail="not_found")
    return SendResultOut(id=settled.id, status=settled.status)
