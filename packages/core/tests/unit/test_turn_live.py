"""A chat turn shows what a long tool is doing while it still runs.

A workflow started from chat runs all its steps inside one ``run_workflow``
call; what it posts to the turn's live feed (``orchestrator.turn_live``)
streams before the call ends, and nothing is posted outside a chat turn.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from openexecutive.orchestrator import turn_live
from openexecutive.orchestrator.executive import Executive

_RUN = "zz_test_run"


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, id_: str, name: str, input_: dict[str, Any]) -> None:
        self.id = id_
        self.name = name
        self.input = input_


class _FinalMsg:
    usage = None

    def __init__(self, content: list[Any], stop_reason: str) -> None:
        self.content = content
        self.stop_reason = stop_reason


class _FakeStream:
    def __init__(self, final_msg: _FinalMsg) -> None:
        self._final_msg = final_msg

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def get_final_message(self) -> _FinalMsg:
        return self._final_msg


class _ScriptedProvider:
    def __init__(self, final_msgs: list[_FinalMsg]) -> None:
        self._final_msgs = list(final_msgs)

    def messages_stream(self, **_kwargs: Any) -> _FakeStream:
        return _FakeStream(self._final_msgs.pop(0))


@pytest.fixture(autouse=True)
def _no_audit_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openexecutive.orchestrator.executive.audit_log", lambda *_a, **_k: None)


# Whether the tool below had finished when each item reached the chat.
_running: dict[str, bool] = {}


async def _workflow(*_a: Any, **_k: Any) -> str:
    _running["now"] = True
    turn_live.say("Finding sailings for two")
    await asyncio.sleep(0.05)
    turn_live.post({"type": "note", "text": "step two"})
    await asyncio.sleep(0.05)
    _running["now"] = False
    return json.dumps({"ok": True, "artifact": "Ship 7: $1,240"})


def _turn() -> list[Any]:
    provider = _ScriptedProvider([
        _FinalMsg([_ToolUseBlock("tu_1", _RUN, {})], "tool_use"),
        _FinalMsg([_TextBlock("It is $1,240 for two.")], "end_turn"),
    ])

    async def go() -> list[Any]:
        items: list[Any] = []
        with patch(
            "openexecutive.orchestrator.executive.get_provider", return_value=provider,
        ), patch.dict(
            "openexecutive.orchestrator.executive._ALL_SKILL_HANDLERS", {_RUN: _workflow},
        ):
            async for item in Executive()._stream_agent_loop(
                system_blocks=[], messages=[{"role": "user", "content": "price the cruise"}], model="claude-test",
            ):
                if isinstance(item, dict) and item.get("tool", "") == "" and item.get("type") in ("activity", "note"):
                    item = {**item, "while_running": _running.get("now", False)}
                items.append(item)
        return items

    return asyncio.run(go())


def test_a_long_tool_shows_what_it_is_doing_while_it_runs() -> None:
    live = [it for it in _turn() if isinstance(it, dict) and it.get("while_running")]
    assert [it["type"] for it in live] == ["activity", "note"]
    assert live[0]["label"] == "Finding sailings for two…"


def test_nothing_is_posted_outside_a_chat_turn() -> None:
    assert turn_live.watching() is False
    assert turn_live.say("Finding sailings") is False
    assert turn_live.post({"type": "note"}) is False
