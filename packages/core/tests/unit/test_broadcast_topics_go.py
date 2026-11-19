"""What a broadcast is about never stops it (CLAUDE.md, The Rules: privacy
hides, it never stops work). Whether a step waits is the one gate's call
(``take_the_lead.gate``), not the broadcast tool's."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from openexecutive.audit import logger as audit_logger
from openexecutive.audit.logger import AuditLogger
from openexecutive.orchestrator.broadcast_tools import (
    handle_send_company_broadcast,
    handle_send_department_message,
)


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit rows go to a per-test DB (CLAUDE.md, "Audit-log test pollution")."""
    monkeypatch.setattr(audit_logger, "_default_logger", AuditLogger(db_path=tmp_path / "audit.db"))


@pytest.mark.parametrize("text", [
    "heads up: layoffs next week",
    "the board deck is ready",
    "updated compensation bands",
    "the term sheet arrived",
])
def test_a_company_broadcast_is_not_refused_for_its_topic(text: str) -> None:
    out = json.loads(asyncio.run(handle_send_company_broadcast({"integration": "slack", "text": text})))
    # It goes on to the channel lookup (none set up here), not a refusal.
    assert "channel" in out["error"].lower()
    assert "privacy" not in out["error"].lower()


def test_a_department_message_is_not_refused_for_its_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    import openexecutive.departments.store as dept_store

    monkeypatch.setattr(dept_store, "get_department", lambda slug: None)
    out = json.loads(asyncio.run(handle_send_department_message(
        {"department_slug": "marketing", "integration": "slack", "text": "the compensation bands changed"}
    )))
    # It goes on to the department lookup (none here), not a refusal.
    assert out == {"error": "unknown department 'marketing'"}
