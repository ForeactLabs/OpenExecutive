"""Every refusal the person asking hears is said plainly and passed to no one.

A refusal that told the Executive "the owner needs to make this change" or
"tell whoever asked that the principal needs to do it" led it to message the
owner with the request, so one person's ask landed on someone else's list.
Refusals end with ``speaker.PASS_ON_NOTHING`` instead; this keeps the old
wording from coming back anywhere in the Executive's tools.
"""
from __future__ import annotations

import re
from pathlib import Path

import openexecutive

_HANDS_IT_ON = re.compile(
    r"(owner|principal) needs to (make|do|add|record|tell|change)", re.IGNORECASE
)


def test_no_refusal_tells_the_executive_someone_else_needs_to_do_it() -> None:
    root = Path(openexecutive.__file__).parent / "orchestrator"
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(root.glob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if _HANDS_IT_ON.search(line)
    ]
    assert offenders == []


def test_the_line_says_to_send_nothing() -> None:
    from openexecutive.orchestrator.speaker import PASS_ON_NOTHING

    assert PASS_ON_NOTHING.startswith("Send nothing on their behalf")
