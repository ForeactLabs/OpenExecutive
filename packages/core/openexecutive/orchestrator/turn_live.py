"""What a chat turn shows while one of its tools is still running.

A tool can take minutes: a workflow started from chat runs all its steps
inside one ``run_workflow`` call. Without this the chat says "Running a
workflow…" until the whole run ends. While a round's tools run, the agent
loop binds a feed (``Feed.bound``) and streams whatever is posted to it as it
arrives (``Feed.until``), so anything inside the turn, a workflow step
included, can say what it is doing now (``say``) or send another stream event
(``post``). Outside a chat turn (a scheduled run, the Workflows page) there
is no feed, and posting does nothing.

What is posted goes to the person whose turn it is, live, and is never saved
with the reply.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterator
from contextvars import ContextVar
from typing import Any

# One line in the chat: the same cap as a round's activity label.
MAX_LINE_CHARS = 90
# A run that posts faster than the chat reads keeps only the newest.
MAX_WAITING = 200

_feed: ContextVar[Feed | None] = ContextVar("turn_live_feed", default=None)


class Feed:
    """The events posted while one round's tools run, in order."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def put(self, event: dict[str, Any]) -> None:
        if self._queue.qsize() >= MAX_WAITING:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait(event)

    @contextlib.contextmanager
    def bound(self) -> Iterator[None]:
        """Bind this feed for what starts inside: a task copies the context
        it is created in, so create the round's tools' task in here."""
        token = _feed.set(self)
        try:
            yield
        finally:
            _feed.reset(token)

    async def until(self, task: asyncio.Future[Any]) -> AsyncIterator[dict[str, Any]]:
        """Each event posted until ``task`` is done, then any left over.
        Cancels ``task`` if the reader stops early (the turn was stopped)."""
        try:
            while not task.done():
                getter = asyncio.ensure_future(self._queue.get())
                try:
                    await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    if not getter.done():
                        getter.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await getter
                if getter.done() and not getter.cancelled():
                    yield getter.result()
            while not self._queue.empty():
                yield self._queue.get_nowait()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task


def watching() -> bool:
    """Whether a chat turn is following what runs here right now."""
    return _feed.get() is not None


def post(event: dict[str, Any]) -> bool:
    """Send ``event`` to the chat following this call, if one is. Returns
    whether one was."""
    feed = _feed.get()
    if feed is None or not isinstance(event, dict):
        return False
    feed.put(event)
    return True


def say(line: Any) -> bool:
    """Show ``line`` as what the turn is doing now: the chat's live line, as
    a round's ``activity`` would. Empty or not text: nothing."""
    if not isinstance(line, str):
        return False
    text = " ".join(line.split())
    if not text:
        return False
    if len(text) > MAX_LINE_CHARS:
        text = text[: MAX_LINE_CHARS - 1].rstrip() + "…"
    elif not text.endswith("…"):
        text = text.rstrip(".") + "…"
    return post({"type": "activity", "label": text, "tool": ""})
