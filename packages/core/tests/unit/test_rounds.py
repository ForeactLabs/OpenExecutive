"""The one loop a background pass runs its tool calls in (orchestrator.rounds).

No count of rounds: it ends when the model answers, and writes up (one more
call with no tools) when a call keeps coming back the same, when its reads
fill what the model holds, or when the pass says it has had enough. A long
result comes back in parts, as in a chat turn.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openexecutive.orchestrator import rounds
from openexecutive.orchestrator.reading import FULL_NOTE, REPEAT_LIMIT, REPEATING_NOTE
from openexecutive.providers import feature_gate, registry


def _use(name: str, args: dict[str, Any], id: str) -> Any:
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id)


def _calls(*uses: Any) -> Any:
    return SimpleNamespace(content=list(uses), stop_reason="tool_use")


def _answer(text: str) -> Any:
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


class _Model:
    """Answers each request with ``reply(request number, request)``; keeps a
    copy of every request."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.requests: list[dict[str, Any]] = []

    async def messages_create(self, **kw: Any) -> Any:
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.reply(len(self.requests), kw)


async def _run(model: _Model, run_call: Any, **kw: Any) -> rounds.Rounds:
    return await rounds.run_rounds(
        model, model="claude-opus-5", max_tokens=1000, system="s", tools=[],
        messages=[{"role": "user", "content": "go"}], run_call=run_call, **kw,
    )


@pytest.mark.asyncio
async def test_a_pass_keeps_going_until_its_work_is_done() -> None:
    ran: list[str] = []

    async def run_call(use: dict[str, Any]) -> str:
        ran.append(use["input"]["n"])
        return f"row {use['input']['n']}"

    model = _Model(lambda n, _kw: _calls(_use("read", {"n": n}, f"t{n}")) if n <= 40 else _answer("All 40 read."))
    done = await _run(model, run_call)
    assert len(ran) == 40 and done.text == "All 40 read." and done.ended == "done"
    assert done.rounds == 41
    assert all("tool_choice" not in r for r in model.requests)


@pytest.mark.asyncio
async def test_a_call_that_keeps_coming_back_the_same_writes_up() -> None:
    async def run_call(_use: dict[str, Any]) -> str:
        return '{"error": "busy", "request_id": "9f8e7d6c5b4a3210"}'

    def reply(n: int, kw: dict[str, Any]) -> Any:
        if kw.get("tool_choice") == {"type": "none"}:
            return _answer("It kept saying busy.")
        return _calls(_use("send", {"to": "a"}, f"t{n}"))

    model = _Model(reply)
    done = await _run(model, run_call)
    assert done.ended == "repeating" and done.text == "It kept saying busy."
    assert len(model.requests) == REPEAT_LIMIT + 1
    assert model.requests[-1]["messages"][-1]["content"][-1] == {"type": "text", "text": REPEATING_NOTE}


@pytest.mark.asyncio
async def test_a_long_result_comes_in_parts_without_running_the_tool_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A small model: one part is about 30,000 characters.
    monkeypatch.setattr(registry, "reading_limits_for", lambda _m="": feature_gate.reading_limits(50_000))
    long = "".join(f"line {i:06d}\n" for i in range(4_000))  # 48,000 characters
    ran: list[int] = []

    async def run_call(_use: dict[str, Any]) -> str:
        ran.append(1)
        return long

    def reply(n: int, kw: dict[str, Any]) -> Any:
        last = kw["messages"][-1]["content"]
        if n > 1 and "LAST PART" in str(last):
            return _answer("Read it all.")
        return _calls(_use("read_doc", {"id": "d"}, f"t{n}"))

    model = _Model(reply)
    done = await _run(model, run_call)
    assert ran == [1] and done.text == "Read it all."
    shown = "".join(str(r["messages"][-1]["content"][0]["content"]) for r in model.requests[1:])
    assert "line 000000" in shown and "line 003999" in shown


@pytest.mark.asyncio
async def test_reads_that_fill_the_model_write_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "reading_limits_for", lambda _m="": feature_gate.reading_limits(10_000))

    async def run_call(use: dict[str, Any]) -> str:
        return f"{use['input']['n']}:" + "z" * 8_000

    def reply(n: int, kw: dict[str, Any]) -> Any:
        if kw.get("tool_choice") == {"type": "none"}:
            return _answer("Here is what I read.")
        return _calls(_use("read", {"n": n}, f"t{n}"))

    model = _Model(reply)
    done = await _run(model, run_call)
    assert done.ended == "full" and done.text == "Here is what I read."
    assert model.requests[-1]["messages"][-1]["content"][-1] == {"type": "text", "text": FULL_NOTE}


@pytest.mark.asyncio
async def test_a_pass_that_has_had_enough_writes_up_or_stops() -> None:
    made: list[int] = []

    async def run_call(_use: dict[str, Any]) -> str:
        made.append(1)
        return "ok"

    def reply(n: int, kw: dict[str, Any]) -> Any:
        if kw.get("tool_choice") == {"type": "none"}:
            return _answer("Summary.")
        return _calls(_use("act", {"n": n}, f"t{n}"))

    model = _Model(reply)
    done = await _run(model, run_call, enough=lambda: "Write it up." if len(made) >= 2 else None)
    assert done.ended == "enough" and done.text == "Summary." and len(made) == 2
    assert model.requests[-1]["messages"][-1]["content"][-1] == {"type": "text", "text": "Write it up."}

    made.clear()
    model = _Model(reply)
    done = await _run(model, run_call, enough=lambda: "" if made else None)
    assert done.ended == "enough" and done.text == "" and len(model.requests) == 1


@pytest.mark.asyncio
async def test_each_response_is_seen_and_only_what_is_sent_goes() -> None:
    seen: list[int] = []

    async def run_call(_use: dict[str, Any]) -> str:
        return "ok"

    model = _Model(lambda n, _kw: _calls(_use("a", {"n": n}, f"t{n}")) if n < 3 else _answer("done"))
    messages: list[dict[str, Any]] = [{"role": "user", "content": "old"}, {"role": "user", "content": "go"}]
    await rounds.run_rounds(
        model, model="m", max_tokens=10, system="s", tools=[], messages=messages, run_call=run_call,
        on_response=lambda _r, i: seen.append(i), sent=lambda m: m[1:],
    )
    assert seen == [1, 2, 3]
    assert all(r["messages"][0]["content"] == "go" for r in model.requests)
    assert messages[0]["content"] == "old" and messages[-1]["role"] == "assistant"
