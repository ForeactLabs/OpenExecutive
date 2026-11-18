"""One loop for a model pass that calls tools until its work is done.

The passes that run with nobody watching (the morning review and Take the
lead, research's routing and watch passes, the chat review) drive the model
through here, as does any other chat with tools that isn't a turn of the
Executive's. Each round sends what the pass has so
far, runs the calls the model wrote and sends their results back, read the
way a chat turn reads them (orchestrator.reading): sizes come from the
model, and a long result comes back in parts, a repeat of the same call
bringing the next part without running the tool again.

There is no count of rounds. A pass ends when the model answers without a
call. It writes up from what it has, with one more call that offers no
tools, when the same call came back the same ``REPEAT_LIMIT`` times, when
its reads fill what its model holds, or when the pass itself says it has
had enough (``enough``).
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from openexecutive.orchestrator.reading import (
    FULL_NOTE,
    REPEATING_NOTE,
    TurnReadingBudget,
)

logger = logging.getLogger(__name__)

Ended = Literal["done", "repeating", "full", "enough"]


@dataclass
class Rounds:
    """How a pass went: the text of its closing answer (what the model wrote
    in the call that ended it; text beside its tool calls is it thinking
    out loud), how many model calls it made, and why it ended."""

    text: str
    rounds: int
    ended: Ended


def _block(block: Any) -> dict[str, Any] | None:
    """A response block as the next request sends it back, or None."""
    from openexecutive.providers.translator import reasoning_replay_block

    if isinstance(block, dict):
        return block
    kind = getattr(block, "type", "")
    if kind == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
        }
    # OpenRouter reasoning continuity across rounds.
    return reasoning_replay_block(block)


def text_of(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text")).strip()


async def run_rounds(
    llm: Any,
    *,
    model: str,
    max_tokens: int,
    system: Any,
    tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    run_call: Callable[[dict[str, Any]], Awaitable[Any]],
    actor: str,
    session_id: str | None = None,
    on_response: Callable[[Any, int], Any] | None = None,
    enough: Callable[[], str | None] | None = None,
    sent: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> Rounds:
    """Run the model on ``messages`` until it answers without a call.

    ``run_call`` runs one tool call (``{"id", "name", "input"}``) and returns
    its result; it is never called for a repeat that brings the next part of
    a long result. Each response is recorded as ``actor``'s usage
    (audit.usage), and ``on_response`` sees it with its round number. ``enough``, asked after each round, returns a note when
    the pass wants the model to write up now ("" to stop at once, with no
    write-up). ``sent`` picks what of ``messages`` each request sends (a chat
    that keeps only its newest turns). ``messages`` grows with each round. A
    provider error is raised to the caller.
    """
    from openexecutive.audit.usage import log_model_usage
    from openexecutive.providers.registry import reading_limits_for

    reading = TurnReadingBudget(reading_limits_for(model))
    ending: Ended | None = None
    rounds = 0
    while True:
        rounds += 1
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "tools": tools,
            "messages": sent(messages) if sent else messages,
        }
        if ending is not None:
            # Writing up: nothing more runs.
            kwargs["tool_choice"] = {"type": "none"}
        response = await llm.messages_create(**kwargs)
        log_model_usage(response, model=model, actor=actor, iteration=rounds, session_id=session_id)
        if on_response is not None:
            seen = on_response(response, rounds)
            if inspect.isawaitable(seen):
                await seen
        reading.saw(getattr(response, "usage", None))
        blocks = [b for b in (_block(x) for x in getattr(response, "content", None) or []) if b is not None]
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if ending is not None or not uses:
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            return Rounds(text=text_of(blocks), rounds=rounds, ended=ending or "done")
        messages.append({"role": "assistant", "content": blocks})
        replays = {str(u["id"]): r for u in uses if (r := reading.replay(u)) is not None}
        reading.ran(uses)
        results: list[dict[str, Any]] = []
        for use in uses:
            replayed = replays.get(str(use["id"]))
            if replayed is not None:
                content: Any = replayed
            else:
                out = await run_call(use)
                reading.came_back(str(use["name"]), use["input"], out)
                content = reading.cap(str(use["name"]), use["input"], str(out), str(use["id"]))
            results.append({"type": "tool_result", "tool_use_id": use["id"], "content": content})
        if getattr(response, "stop_reason", None) != "tool_use":
            # Calls beside a finished answer: they ran, and the answer stands.
            messages.append({"role": "user", "content": results})
            return Rounds(text=text_of(blocks), rounds=rounds, ended="done")
        note = enough() if enough is not None else None
        if note == "":
            messages.append({"role": "user", "content": results})
            return Rounds(text="", rounds=rounds, ended="enough")
        if note is not None:
            ending = "enough"
        elif reading.repeating:
            logger.warning("%s: a call came back the same several times; writing up", model)
            note, ending = REPEATING_NOTE, "repeating"
        elif reading.full:
            logger.info("%s: the pass has read all its model holds; writing up", model)
            note, ending = FULL_NOTE, "full"
        if note:
            results.append({"type": "text", "text": note})
        messages.append({"role": "user", "content": results})
