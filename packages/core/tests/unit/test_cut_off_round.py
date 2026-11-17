"""A round cut off at the output limit (stop_reason "max_tokens") must not
end the turn: the half-written tool call never runs, the model is told to
make it again in a shorter form, and the step is named as writing, not as
the read before it. A card's work otherwise stopped with "couldn't finish"
while writing a long spreadsheet."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from openexecutive.orchestrator.executive import (
    CUT_OFF_LABEL,
    CUT_OFF_NOTE,
    CUT_OFF_RESULT,
    MAX_OUTPUT_TOKENS,
    Executive,
)
from tests.unit.test_tool_result_cap import _FinalMsg, _ScriptedProvider, _TextBlock, _ToolUseBlock


@pytest.fixture(autouse=True)
def _no_audit_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openexecutive.orchestrator.executive.audit_log", lambda *_a, **_k: None)


async def _run(provider: _ScriptedProvider, handlers: dict[str, Any] | None = None) -> list[Any]:
    items: list[Any] = []
    with patch("openexecutive.orchestrator.executive.get_provider", return_value=provider), patch.dict(
        "openexecutive.orchestrator.executive._ALL_SKILL_HANDLERS", handlers or {},
    ):
        async for item in Executive()._stream_agent_loop(
            system_blocks=[], messages=[{"role": "user", "content": "make the sheet"}], model="claude-test",
        ):
            items.append(item)
    return items


def test_every_round_may_write_more_than_8192() -> None:
    provider = _ScriptedProvider([_FinalMsg([_TextBlock("Done.")], "end_turn")])
    asyncio.run(_run(provider))
    assert provider.calls[0]["max_tokens"] == MAX_OUTPUT_TOKENS > 8192


def test_cut_off_tool_call_never_runs_and_the_turn_carries_on() -> None:
    ran: list[dict[str, Any]] = []

    async def _draft(tool_input: dict[str, Any], *_a: Any, **_k: Any) -> str:
        ran.append(tool_input)
        return "published"

    provider = _ScriptedProvider(
        [
            # The round ran out of room while writing the spreadsheet.
            _FinalMsg([_ToolUseBlock("big", "draft_artifact", {"title": "Tax bills", "sheets": []})], "max_tokens"),
            _FinalMsg([_TextBlock("Done.")], "end_turn"),
        ]
    )
    items = asyncio.run(_run(provider, {"draft_artifact": _draft}))

    assert ran == [], "a half-written call must not run"
    assert len(provider.calls) == 2
    results = provider.calls[1]["messages"][-1]["content"]
    answered = {b["tool_use_id"]: b["content"] for b in results if b.get("type") == "tool_result"}
    assert answered["big"] == CUT_OFF_RESULT
    labels = [i["label"] for i in items if isinstance(i, dict) and i.get("type") == "activity"]
    assert labels == [CUT_OFF_LABEL]


def test_cut_off_reply_text_carries_on() -> None:
    provider = _ScriptedProvider(
        [
            _FinalMsg([_TextBlock("Here is the first half")], "max_tokens"),
            _FinalMsg([_TextBlock(" and the rest.")], "end_turn"),
        ]
    )
    asyncio.run(_run(provider))

    assert len(provider.calls) == 2
    sent = provider.calls[1]["messages"]
    assert sent[-2]["role"] == "assistant"
    assert sent[-1] == {"role": "user", "content": [{"type": "text", "text": CUT_OFF_NOTE}]}
    assert sent[-2]["content"] == [{"type": "text", "text": "Here is the first half"}]


def test_cut_off_on_the_last_round_ends_as_written() -> None:
    provider = _ScriptedProvider([_FinalMsg([_TextBlock("All I could write")], "max_tokens")])

    async def _go() -> None:
        with patch("openexecutive.orchestrator.executive.get_provider", return_value=provider):
            async for _ in Executive()._stream_agent_loop(
                system_blocks=[], messages=[{"role": "user", "content": "x"}], model="claude-test",
                max_iterations=1,
            ):
                pass

    asyncio.run(_go())
    assert len(provider.calls) == 1


def test_cut_off_call_dropped_by_the_provider_is_named_as_writing() -> None:
    provider = _ScriptedProvider(
        [
            _FinalMsg([], "max_tokens"),
            _FinalMsg([_TextBlock("Done.")], "end_turn"),
        ]
    )
    items = asyncio.run(_run(provider))

    sent = provider.calls[1]["messages"]
    assert all(m["content"] for m in sent), "no message may go out empty"
    assert sent[-1]["content"][0]["text"] == CUT_OFF_NOTE
    labels = [i["label"] for i in items if isinstance(i, dict) and i.get("type") == "activity"]
    assert labels == [CUT_OFF_LABEL]


def test_cut_off_reply_text_runs_on_without_a_paragraph_break() -> None:
    from tests.unit._agent_loop_fakes import FinalMsg, TextBlock
    from tests.unit.test_reply_rounds import _reply

    text = _reply([
        FinalMsg([TextBlock("Revenue grew 12% in Q")], "max_tokens"),
        FinalMsg([TextBlock("3, driven by renewals.")], "end_turn"),
    ])
    assert text == "Revenue grew 12% in Q3, driven by renewals."


def test_a_call_that_closed_before_the_cut_still_runs() -> None:
    ran: list[dict[str, Any]] = []

    async def _draft(tool_input: dict[str, Any], *_a: Any, **_k: Any) -> str:
        ran.append(tool_input)
        return "published"

    provider = _ScriptedProvider(
        [
            # The call finished; the round was cut in the text after it.
            _FinalMsg([_ToolUseBlock("ok", "draft_artifact", {"title": "Tax bills"}), _TextBlock("Now the")],
                      "max_tokens"),
            _FinalMsg([_TextBlock("Done.")], "end_turn"),
        ]
    )
    asyncio.run(_run(provider, {"draft_artifact": _draft}))

    assert len(ran) == 1
    results = provider.calls[1]["messages"][-1]["content"]
    answered = {b["tool_use_id"]: b["content"] for b in results if b.get("type") == "tool_result"}
    assert answered["ok"] != CUT_OFF_RESULT
    # The cut text after it is carried on, and the model is told so.
    assert {"type": "text", "text": CUT_OFF_NOTE} in results


def test_a_cut_that_wrote_nothing_keeps_the_break_after_earlier_text() -> None:
    from tests.unit._agent_loop_fakes import FinalMsg, TextBlock, ToolUseBlock
    from tests.unit.test_reply_rounds import _reply

    text = _reply([
        FinalMsg([TextBlock("Let me pull the numbers."), ToolUseBlock("t1", "nothing_here", {})], "tool_use"),
        FinalMsg([], "max_tokens"),
        FinalMsg([TextBlock("Revenue grew 12%.")], "end_turn"),
    ])
    assert text == "Let me pull the numbers.\n\nRevenue grew 12%."


def test_a_cut_before_the_last_round_says_no_tools_are_left() -> None:
    from openexecutive.orchestrator.executive import LAST_ROUND_NOTE

    provider = _ScriptedProvider(
        [
            _FinalMsg([], "max_tokens"),
            _FinalMsg([_TextBlock("Here is what I have.")], "end_turn"),
        ]
    )

    async def _go() -> None:
        with patch("openexecutive.orchestrator.executive.get_provider", return_value=provider):
            async for _ in Executive()._stream_agent_loop(
                system_blocks=[], messages=[{"role": "user", "content": "x"}], model="claude-test",
                max_iterations=2,
            ):
                pass

    asyncio.run(_go())
    texts = [b["text"] for b in provider.calls[1]["messages"][-1]["content"]]
    assert texts == [CUT_OFF_NOTE, LAST_ROUND_NOTE]


def test_a_cut_call_before_a_trailing_reasoning_block_never_runs() -> None:
    from types import SimpleNamespace

    ran: list[dict[str, Any]] = []

    async def _draft(tool_input: dict[str, Any], *_a: Any, **_k: Any) -> str:
        ran.append(tool_input)
        return "published"

    # A translated provider (OpenRouter) puts its reasoning block last.
    reasoning = SimpleNamespace(type="openrouter_reasoning", reasoning_details=[{"type": "reasoning.text", "text": "…"}])
    provider = _ScriptedProvider(
        [
            _FinalMsg([_TextBlock("Building it."), _ToolUseBlock("cut", "draft_artifact", {}), reasoning],
                      "max_tokens"),
            _FinalMsg([_TextBlock("Done.")], "end_turn"),
        ]
    )
    asyncio.run(_run(provider, {"draft_artifact": _draft}))

    assert ran == []
    results = provider.calls[1]["messages"][-1]["content"]
    answered = {b["tool_use_id"]: b["content"] for b in results if b.get("type") == "tool_result"}
    assert answered["cut"] == CUT_OFF_RESULT


def test_the_claim_check_reads_the_cut_text_with_the_rest_of_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read: list[str] = []

    async def _claim(_asked: str, reply: str, _calls: Any) -> None:
        read.append(reply)
        return None

    monkeypatch.setattr("openexecutive.orchestrator.executive.claim_check.unbacked_claim", _claim)
    provider = _ScriptedProvider(
        [
            _FinalMsg([_TextBlock("I've added the meeting to your calendar and")], "max_tokens"),
            _FinalMsg([_TextBlock(" sent the invite.")], "end_turn"),
        ]
    )
    asyncio.run(_run(provider))

    assert read == ["I've added the meeting to your calendar and sent the invite."]


def test_the_cut_call_input_is_not_sent_again() -> None:
    provider = _ScriptedProvider(
        [
            _FinalMsg([_ToolUseBlock("big", "draft_artifact", {"title": "Tax bills", "rows": ["x"] * 50})],
                      "max_tokens"),
            _FinalMsg([_TextBlock("Done.")], "end_turn"),
        ]
    )
    asyncio.run(_run(provider, {"draft_artifact": lambda *_a, **_k: "published"}))

    sent = [
        b for m in provider.calls[1]["messages"] if m["role"] == "assistant" and isinstance(m["content"], list)
        for b in m["content"] if b.get("type") == "tool_use" and b.get("id") == "big"
    ]
    assert sent and sent[0]["input"] == {}


def test_only_the_round_right_after_a_cut_runs_on() -> None:
    from tests.unit._agent_loop_fakes import FinalMsg, TextBlock, ToolUseBlock
    from tests.unit.test_reply_rounds import _reply

    text = _reply([
        FinalMsg([TextBlock("Here is the first half.")], "max_tokens"),
        FinalMsg([ToolUseBlock("t1", "nothing_here", {})], "tool_use"),
        FinalMsg([TextBlock("Final answer.")], "end_turn"),
    ])
    assert text == "Here is the first half.\n\nFinal answer."


def test_the_cut_call_is_not_counted_as_a_call_made() -> None:
    calls: list[tuple[str, str]] = []
    provider = _ScriptedProvider(
        [
            _FinalMsg([_ToolUseBlock("cut", "draft_artifact", {})], "max_tokens"),
            _FinalMsg([_TextBlock("Done.")], "end_turn"),
        ]
    )

    async def _go() -> None:
        with patch("openexecutive.orchestrator.executive.get_provider", return_value=provider), patch.dict(
            "openexecutive.orchestrator.executive._ALL_SKILL_HANDLERS", {"draft_artifact": lambda *_a, **_k: "x"},
        ):
            async for _ in Executive()._stream_agent_loop(
                system_blocks=[], messages=[{"role": "user", "content": "make the sheet"}], model="claude-test",
                calls_out=calls,
            ):
                pass

    asyncio.run(_go())

    assert calls == []
    results = provider.calls[1]["messages"][-1]["content"]
    assert [(b["tool_use_id"], b["content"]) for b in results if b.get("type") == "tool_result"] == [
        ("cut", CUT_OFF_RESULT)
    ]


def test_a_turn_that_keeps_being_cut_ends_after_max_cut_rounds() -> None:
    from openexecutive.orchestrator.executive import MAX_CUT_ROUNDS

    provider = _ScriptedProvider(
        [_FinalMsg([_ToolUseBlock(f"c{i}", "draft_artifact", {})], "max_tokens") for i in range(MAX_CUT_ROUNDS + 1)]
        + [_FinalMsg([_TextBlock("never reached")], "end_turn")]
    )
    asyncio.run(_run(provider, {"draft_artifact": lambda *_a, **_k: "published"}))

    assert len(provider.calls) == MAX_CUT_ROUNDS + 1


def test_text_cut_after_a_closed_call_runs_on() -> None:
    from tests.unit._agent_loop_fakes import FinalMsg, TextBlock, ToolUseBlock
    from tests.unit.test_reply_rounds import _reply

    text = _reply([
        FinalMsg([ToolUseBlock("t1", "nothing_here", {}), TextBlock("Revenue grew 12% in Q")], "max_tokens"),
        FinalMsg([TextBlock("3, driven by renewals.")], "end_turn"),
    ])
    assert text == "Revenue grew 12% in Q3, driven by renewals."


def test_the_claim_check_reads_text_cut_after_a_closed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    read: list[str] = []

    async def _claim(_asked: str, reply: str, _calls: Any) -> None:
        read.append(reply)
        return None

    async def _read(*_a: Any, **_k: Any) -> str:
        return "rows"

    monkeypatch.setattr("openexecutive.orchestrator.executive.claim_check.unbacked_claim", _claim)
    provider = _ScriptedProvider(
        [
            _FinalMsg([_ToolUseBlock("r", "read_sheet", {}), _TextBlock("I've emailed the summary to Dana and")],
                      "max_tokens"),
            _FinalMsg([_TextBlock(" filed it.")], "end_turn"),
        ]
    )
    asyncio.run(_run(provider, {"read_sheet": _read}))

    assert read == ["I've emailed the summary to Dana and filed it."]
