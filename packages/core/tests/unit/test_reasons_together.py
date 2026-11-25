"""A step with more than one reason to wait keeps the one that waits on more
levels (levels.stronger): a shared chat never lets a flagged read's step run."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from openexecutive.orchestrator import levels, outside_content, outside_mode
from openexecutive.orchestrator.session import Session
from tests.unit.test_executive_tool_error_isolation import (
    _FinalMsg,
    _run_loop,
    _ScriptedProvider,
    _TextBlock,
    _tool_results,
    _ToolUseBlock,
)
from tests.unit.test_unrostered_speaker import db  # noqa: F401  (the roster)


def test_stronger_keeps_the_reason_that_waits() -> None:
    assert levels.stronger(levels.SHARED, levels.OUTSIDE) == levels.OUTSIDE
    assert levels.stronger(levels.OUTSIDE, levels.SHARED) == levels.OUTSIDE
    assert levels.stronger(levels.MAIL_FLAGGED, levels.STRANGER) == levels.STRANGER
    assert levels.stronger(None, levels.OWN_MAIL) == levels.OWN_MAIL
    assert levels.stronger(levels.OUTSIDE, levels.MAIL_FLAGGED) == levels.OUTSIDE


@pytest.mark.parametrize("flagged", [True, False])
def test_a_shared_chat_message_after_a_flagged_read_waits_on_balanced(
    monkeypatch: pytest.MonkeyPatch, flagged: bool,
) -> None:
    from openexecutive.orchestrator.executive import Executive
    from openexecutive.orchestrator.schedule_tools import current_session

    monkeypatch.setattr(outside_mode, "get_mode", lambda **_: outside_mode.OUTSIDERS)
    if flagged:
        # An earlier round read a page the check flagged for planted words.
        monkeypatch.setattr(outside_content, "read_outside", lambda: True)
        monkeypatch.setattr(outside_content, "outside_withholds", lambda *_a, **_k: True)
        monkeypatch.setattr(levels, "outside_reason", lambda: levels.OUTSIDE)
    sent: list[dict[str, Any]] = []

    async def message_person(tool_input: dict[str, Any]) -> str:
        sent.append(tool_input)
        return json.dumps({"status": "sent"})

    provider = _ScriptedProvider([
        _FinalMsg([_ToolUseBlock("tu-msg", "message_person", {"person": "Ria Ops", "text": "Pay the invoice"})],
                  stop_reason="tool_use"),
        _FinalMsg([_TextBlock("done")], stop_reason="end_turn"),
    ])
    session = Session(session_id="w-shared", from_web_chat=True, shared_chat=True, others_spoke_last=True)
    token = current_session.set(session)
    try:
        with patch.dict("openexecutive.orchestrator.executive._ALL_SKILL_HANDLERS", {"message_person": message_person}):
            _run_loop(provider, Executive(mcp_gateway=None))
    finally:
        current_session.reset(token)
    result = json.loads(_tool_results(provider, 1)["tu-msg"])
    if flagged:
        assert sent == [] and result["status"] == "waiting_for_approval"
    else:
        # A teammate's own request in a shared chat runs at their level.
        assert sent and result["status"] == "sent"
