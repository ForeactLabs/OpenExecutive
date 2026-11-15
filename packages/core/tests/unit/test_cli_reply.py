"""The terminal marks a reply that was written again, rather than showing two."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from openexecutive import cli
from openexecutive.orchestrator.executive import Executive


async def _stream(*chunks: str | dict[str, Any]) -> AsyncIterator[str | dict[str, Any]]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_a_rewritten_reply_is_marked_and_returned_alone(capsys: pytest.CaptureFixture[str]) -> None:
    final = await cli.print_reply(
        _stream(Executive._THINKING, "I saved the doc.", {"type": "rewrite"}, "I couldn't save the doc.")
    )
    out = capsys.readouterr().out
    assert final == "I couldn't save the doc."
    assert "Corrected reply (replaces the one above)" in out
    assert out.index("I saved the doc.") < out.index("Corrected") < out.index("I couldn't save")


@pytest.mark.asyncio
async def test_a_plain_reply_prints_as_is(capsys: pytest.CaptureFixture[str]) -> None:
    final = await cli.print_reply(_stream("Hello", {"type": "status", "text": "x"}, " there"))
    assert final == "Hello there"
    assert "Corrected" not in capsys.readouterr().out
