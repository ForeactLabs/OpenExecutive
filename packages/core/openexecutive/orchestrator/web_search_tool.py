"""Anthropic native server-side web search tool.

The `web_search_20250305` tool is executed by the Anthropic API itself — no
client-side handler is needed. The model issues `server_tool_use` blocks
during a generation and the API returns `web_search_tool_result` blocks
inline; both must be preserved in the assistant turn echoed back on the
next iteration so the model retains context for what it searched.

Because the block carries a `type` field instead of an `input_schema`, it
cannot be cached the way client-side tools are. The Executive keeps it
separate from the alphabetically-sorted client-tool list so the
`cache_control` marker stays on a real client tool.
"""
from __future__ import annotations

from typing import Any

from openexecutive.config import get_settings

WEB_SEARCH_TOOL_NAME = "web_search"


def build_web_search_tool(*, max_uses: int | None = None) -> dict[str, Any] | None:
    """Return the Anthropic web_search tool dict, or None when disabled.

    Read at tool-list assembly time (per turn) so toggling the env var
    without a restart takes effect on the next turn. ``max_uses`` overrides
    the shared ``WEB_SEARCH_MAX_USES`` knob for callers with their own
    (the research fan-out uses ``RESEARCH_WEB_SEARCH_MAX_USES``).
    """
    settings = get_settings()
    if not settings.enable_web_search:
        return None

    tool: dict[str, Any] = {
        "type": "web_search_20250305",
        "name": WEB_SEARCH_TOOL_NAME,
        "max_uses": max_uses if max_uses is not None else settings.web_search_max_uses,
    }
    if settings.web_search_allowed_domains:
        tool["allowed_domains"] = settings.web_search_allowed_domains
    if settings.web_search_blocked_domains:
        tool["blocked_domains"] = settings.web_search_blocked_domains
    return tool


def _result_error(block: dict[str, Any]) -> str | None:
    """The error code of a ``web_search_tool_result`` block, or None when it
    holds results."""
    content = block.get("content")
    if isinstance(content, dict) and content.get("type") == "web_search_tool_result_error":
        return str(content.get("error_code") or "error")
    return None


def searches_run(blocks: list[dict[str, Any]]) -> int:
    """How many web searches in this round's blocks ran: every search but
    one the limit stopped (``max_uses_exceeded``), which never ran."""
    stopped = {
        str(b.get("tool_use_id")) for b in blocks
        if b.get("type") == "web_search_tool_result" and _result_error(b) == "max_uses_exceeded"
    }
    return sum(
        1 for b in blocks
        if b.get("type") == "server_tool_use" and b.get("name") == WEB_SEARCH_TOOL_NAME
        and str(b.get("id")) not in stopped
    )


def turn_search_tool() -> dict[str, Any] | None:
    """The web_search tool for every round of a chat turn: one budget for
    the whole turn (``WEB_SEARCH_TURN_BUDGET``), so an ask with many facts is
    looked up to the end, not cut off after a round's first few searches.
    The same tool every round, since the tools lead the cached prompt and a
    changed ``max_uses`` would miss the cache on every later round; once the
    turn has spent its budget the round's results say so instead
    (``SEARCHES_SPENT_NOTE``). None when search is off."""
    return build_web_search_tool(max_uses=get_settings().web_search_turn_budget)


# Added to a round's results once the turn has run its searches.
SEARCHES_SPENT_NOTE = (
    "This turn has used its web searches. Don't search again: finish the answer from what the searches"
    " found and what the company knows, and say plainly what is still unconfirmed."
)


def spent(used: int) -> bool:
    """Whether a turn that has run ``used`` searches has spent its budget."""
    return used >= get_settings().web_search_turn_budget


def searched(blocks: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Each web search in this round's blocks as (call, result) for the
    after-reply checks (``claim_check`` / ``reply_check``): a search is a
    look, like any read."""
    results = {
        str(b.get("tool_use_id")): b for b in blocks if b.get("type") == "web_search_tool_result"
    }
    calls: list[tuple[str, str]] = []
    for b in blocks:
        if b.get("type") != "server_tool_use" or b.get("name") != WEB_SEARCH_TOOL_NAME:
            continue
        query = (b.get("input") or {}).get("query", "")
        result = results.get(str(b.get("id")))
        if result is None:
            outcome = "no result"
        elif (error := _result_error(result)) is not None:
            outcome = f"error: {error}"
        else:
            found = result.get("content")
            titles = [
                str(r.get("title", "")) for r in (found if isinstance(found, list) else []) if isinstance(r, dict)
            ]
            outcome = f"{len(titles)} results: " + "; ".join(t for t in titles[:5] if t)
        calls.append((f"{WEB_SEARCH_TOOL_NAME} {query}".strip(), outcome))
    return calls
