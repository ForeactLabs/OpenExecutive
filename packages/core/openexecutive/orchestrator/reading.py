"""How much a turn reads into its prompt, and the rest of a long result.

Every tool result stays in the prompt for the rest of the turn. The sizes
come from the turn's model (providers.registry.reading_limits_for): one read
fits whole unless it is longer than a part, and then it comes back in parts,
each repeat of the same call bringing the next one without running the tool
again. Only when the turn has read all its model can hold does a result stop
short, and its note says so plainly. The Executive's loop and a workflow's
action step both read through here.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openexecutive.orchestrator import tool_groups
from openexecutive.providers.feature_gate import ReadingLimits
from openexecutive.workflows import step_script

logger = logging.getLogger(__name__)

# Smallest cap for which the never-exceeds-limit guarantee holds: the marker
# is reserved inside the limit, and it is well under this.
_MIN_USEFUL_CAP = 1_000

# Longest tool name echoed into a truncation marker. Real names are well
# under this (the longest in the MCP surface is ~40 chars); the bound exists
# so a model-supplied name cannot inflate the marker past its own budget.
_TOOL_NAME_MARKER_MAX = 80
# Once a turn's reads fill what its model can hold (ReadingLimits.per_turn),
# a further tool result still shows this much, so the model sees what it got.
_TURN_BUDGET_FLOOR = 3_000

# Results the turn's reading budget counts but never cuts (see
# _turn_result_limit).
_NEVER_CUT_BY_TURN_BUDGET = frozenset(
    {step_script.RUN_SCRIPT_TOOL, tool_groups.OPEN_TOOLS, "search_tools"}
)


def _safe_tool_name(tool_name: Any) -> str:
    # ``tool_name`` comes from the model's tool_use block, so it is
    # attacker-influenceable via prompt injection. Two consequences:
    #   * it is never passed through ``str.format`` — a name containing
    #     braces would otherwise be interpreted as a field reference,
    #     substituting our locals or raising and killing the turn;
    #   * it is length-bounded, so the marker cannot outgrow the budget
    #     it is supposed to fit inside.
    return tool_name[:_TOOL_NAME_MARKER_MAX] if isinstance(tool_name, str) else "?"


def _part_marker(name: str, *, start: int, end: int, total: int) -> str:
    """Said after a part of a result that goes on past ``end``."""
    return (
        f"\n\n[PART of a long result from `{name}`: this shows characters "
        f"{start + 1:,} to {end:,} of {total:,}. This is NOT the whole result. "
        "To read the next part, call the tool again with exactly the same "
        "input: that repeat does not run the tool again, it brings back the "
        "next part. Read on until you have what the request needs, and read "
        "every part before you edit, rewrite or replace anything from it.]"
    )


def _last_part_marker(name: str, *, start: int, total: int) -> str:
    return f"\n\n[LAST PART of the result from `{name}`: characters {start + 1:,} to {total:,} of {total:,}.]"


def _full_marker(name: str, *, shown: int, total: int) -> str:
    """Said after a result cut because the turn holds all it can."""
    return (
        f"\n\n[This question has read all it can hold at once: this shows the "
        f"first {shown:,} of {total:,} characters from `{name}`. This is NOT "
        "the whole result. Answer from what you have read, say plainly what "
        "you have not read yet, and offer to carry on with the rest in a new "
        "message. Never edit, rewrite or replace anything from this cut result.]"
    )


def _cap_tool_result(text: Any, *, tool_name: str, limit: int, budget_spent: bool = False) -> Any:
    """The first part of one tool result, sized to fit before it enters the
    prompt.

    ``limit`` comes from the model (providers.registry.reading_limits_for),
    so an ordinary result, a long document included, fits whole. A longer
    one comes back in parts: this is the first, and its marker says how to
    get the next (``TurnReadingBudget.replay``). ``budget_spent`` says the
    turn has read all its model can hold: the marker then says so plainly
    and offers to carry on, rather than another part.

    The marker is load-bearing. Truncating silently is worse than not
    truncating: the model reads the cut text as the whole answer and
    confabulates the rest.

    Non-``str`` results pass through untouched. The result never exceeds
    ``limit`` (at least ``_MIN_USEFUL_CAP``): the marker is reserved inside it.
    """
    if not isinstance(text, str):
        return text
    if len(text) <= limit:
        return text
    name = _safe_tool_name(tool_name)
    # Reserve against the widest marker: ``shown`` never exceeds ``limit``.
    widest = (
        _full_marker(name, shown=limit, total=len(text))
        if budget_spent
        else _part_marker(name, start=0, end=limit, total=len(text))
    )
    shown = max(0, limit - len(widest))
    logger.warning(
        "tool_result in parts tool=%s original_chars=%d shown_chars=%d limit=%d budget_spent=%s",
        name, len(text), shown, limit, budget_spent,
    )
    marker = (
        _full_marker(name, shown=shown, total=len(text))
        if budget_spent
        else _part_marker(name, start=0, end=shown, total=len(text))
    )
    return text[:shown] + marker


def _turn_result_limit(name: str, *, per_result: int, turn_budget: int, used: int) -> tuple[int, bool]:
    """The size of one tool result's first part, given what the turn has read.

    Every tool result stays in the prompt for each later call of the turn,
    so ``turn_budget`` (ReadingLimits.per_turn, a share of what the model
    holds) bounds what a turn's results add together: once ``used`` reaches
    it, a further result is cut to ``_TURN_BUDGET_FLOOR``. Returns (limit,
    whether the budget rather than ``per_result`` set it). A specialist's
    analysis is the answer itself, not a read, so it neither counts nor is
    cut by the budget. A built tool's result counts but is never cut below
    ``per_result``: it is already bounded (step_script), and it lists the
    writes that already ran, which a short cut would hide and the model would
    then repeat. Tool discovery (``open_tools``, ``search_tools``) is treated
    the same way: its result is the schema for the next call, and a cut one
    would leave the model unable to make it.
    """
    if turn_budget <= 0 or name == "consult_specialist":
        return per_result, False
    if name in _NEVER_CUT_BY_TURN_BUDGET:
        return per_result, False
    remaining = turn_budget - used
    if remaining >= per_result:
        return per_result, False
    if remaining >= _MIN_USEFUL_CAP:
        # Room is left, if less than a whole part: the next part is smaller.
        return remaining, False
    return _TURN_BUDGET_FLOOR, True


class TurnReadingBudget:
    """What one turn's reads have put in the prompt, and the rest of each
    result that came back in parts.

    Sizes come from the turn's model (providers.registry.reading_limits_for).
    A result longer than a part is kept here whole; when the model repeats
    the call, same tool and same input, the loop answers it with the next
    part (``replay``) and never runs the tool again. Replaying rather than
    re-running matters: any tool's result can be long, and a repeated write
    (an event, a message) would happen twice.
    """

    def __init__(self, limits: ReadingLimits) -> None:
        self.per_result = limits.per_read
        self.turn_budget = limits.per_turn
        self.used = 0
        # Each result that came back in parts, by call: its whole text and
        # where the next part starts.
        self._parts: dict[str, tuple[str, int]] = {}
        # tool_use ids answered by ``replay``: already sized, not cut again.
        self._replayed: set[str] = set()

    @staticmethod
    def _key(name: str, tool_input: Any) -> str:
        try:
            return name + "\x00" + json.dumps(tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return name + "\x00" + repr(tool_input)

    def replay(self, tool_use: dict[str, Any]) -> str | None:
        """The next part if ``tool_use`` repeats a call whose result came back
        in parts; the caller answers it with this text instead of running
        it. ``None`` otherwise."""
        key = self._key(tool_use["name"], tool_use["input"])
        held = self._parts.get(key)
        if held is None:
            return None
        text, start = held
        name = _safe_tool_name(tool_use["name"])
        limit, budget_spent = _turn_result_limit(
            tool_use["name"], per_result=self.per_result, turn_budget=self.turn_budget, used=self.used
        )
        if budget_spent:
            # Nothing more fits: say so, and keep the rest for no one.
            del self._parts[key]
            self._replayed.add(tool_use["id"])
            return (
                f"[This question has read all it can hold at once. The result from `{name}` "
                f"was read up to character {start:,} of {len(text):,}. Answer from what you "
                "have read, say plainly what you have not read yet, and offer to carry on "
                "with the rest in a new message.]"
            )
        last = _last_part_marker(name, start=start, total=len(text))
        if len(text) - start + len(last) <= limit:
            del self._parts[key]
            part = text[start:] + last
        else:
            room = max(1, limit - len(_part_marker(name, start=start, end=start + limit, total=len(text))))
            end = start + room
            self._parts[key] = (text, end)
            part = text[start:end] + _part_marker(name, start=start, end=end, total=len(text))
        self._replayed.add(tool_use["id"])
        return part

    def cap(self, name: str, tool_input: Any, text: Any, tool_use_id: str = "") -> Any:
        """``text`` sized for the prompt (its first part when it is long),
        counted against the turn's reading."""
        if tool_use_id and tool_use_id in self._replayed:
            content = text
        else:
            limit, budget_spent = _turn_result_limit(
                name, per_result=self.per_result, turn_budget=self.turn_budget, used=self.used
            )
            content = _cap_tool_result(text, tool_name=name, limit=limit, budget_spent=budget_spent)
            if not budget_spent and isinstance(text, str) and content is not text:
                # Where the next part starts: what the first part showed.
                self._parts[self._key(name, tool_input)] = (text, content.rindex("\n\n[PART"))
        if name != "consult_specialist" and isinstance(content, str):
            self.used += len(content)
        return content
