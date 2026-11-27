"""What a chat turn did, step by step, for the "What I did" timeline.

The chat route passes every event the turn streams through ``TurnTimeline``.
Each tool round's ``activity`` becomes a step (a repeat of the step already
showing is the same step), ``progress`` gives the turn its task, names the
current step in the turn's own words and adds what it found to it, and an
``action_taken`` chip names the step that did something, or says it waits on
the person's OK. Each change is sent to the stream of the person who asked
as one ``timeline`` event carrying the whole picture, so their chat shows it
while the turn runs (someone else watching the turn live sees it once the
reply is saved); the last one is saved with the reply
(``chat_messages.steps``), so a reopened chat still shows it.

Nothing here decides anything: it only records what the turn reported.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openexecutive.orchestrator.action_chips import SIDE_EFFECTING_TOOLS
from openexecutive.orchestrator.activity_labels import FALLBACK_LABEL

# Kept for a turn: the newest steps, and a few findings on each.
MAX_STEPS = 30
MAX_FOUND_PER_STEP = 3
MAX_LABEL_CHARS = 120

# What a step was, for the timeline's groups: what it looked at, what it
# worked out, what it did.
LOOK = "look"
WORK = "work"
DO = "do"

# A done step reads in the past tense: "Searched your email", not
# "Searching your email…". Only the first word changes, and only these.
_PAST: dict[str, str] = {
    "Adding": "Added",
    "Asking": "Asked",
    "Booking": "Booked",
    "Building": "Built",
    "Cancelling": "Cancelled",
    "Checking": "Checked",
    "Comparing": "Compared",
    "Consulting": "Consulted",
    "Creating": "Created",
    "Drafting": "Drafted",
    "Editing": "Edited",
    "Finding": "Found",
    "Getting": "Got",
    "Listing": "Listed",
    "Looking": "Looked",
    "Making": "Made",
    "Messaging": "Messaged",
    "Moving": "Moved",
    "Opening": "Opened",
    "Planning": "Planned",
    "Preparing": "Prepared",
    "Reading": "Read",
    "Recalling": "Recalled",
    "Replying": "Replied",
    "Researching": "Researched",
    "Reviewing": "Reviewed",
    "Running": "Ran",
    "Saving": "Saved",
    "Scheduling": "Scheduled",
    "Searching": "Searched",
    "Sending": "Sent",
    "Setting": "Set",
    "Sharing": "Shared",
    "Summarizing": "Summarized",
    "Updating": "Updated",
    "Working": "Worked",
    "Writing": "Wrote",
}

# A round of thinking things through rather than reading or doing.
_WORK_WORDS = ("consult", "analy", "calculat", "forecast", "model", "plan", "committee", "figure")

_PLURALS = {"person": "people", "web search": "web searches"}
# Read once a turn, or not worth counting: say what they are.
_UNCOUNTED = {"calendar": "Your calendar", "connected app": "Connected apps"}


def _clean(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    text = " ".join(raw.split()).rstrip("…").rstrip(".").strip()
    if len(text) > MAX_LABEL_CHARS:
        text = text[: MAX_LABEL_CHARS - 1].rstrip() + "…"
    return text


def past(label: str) -> str:
    """``label`` as a step already done."""
    first, sep, rest = label.partition(" ")
    done = _PAST.get(first)
    return f"{done}{sep}{rest}" if done else label


# A round whose label starts with one of these changes something, whatever
# its tool is called (a connected app's or an extension's write, which the
# built-in list can't name): it reads as done only once a chip says so.
_WRITE_VERBS = frozenset({
    "Accepting", "Adding", "Archiving", "Booking", "Cancelling", "Creating",
    "Declining", "Deleting", "Drafting", "Editing", "Forwarding", "Inviting",
    "Messaging", "Moving", "Posting", "Removing", "Replying", "Saving",
    "Scheduling", "Sending", "Sharing", "Updating", "Uploading",
})


def kind_of(tool: str, label: str = "") -> str:
    """Which group a round's tool (and its label) puts its step in."""
    name = (tool or "").lower()
    if tool in SIDE_EFFECTING_TOOLS:
        return DO
    first = (label or "").split(" ", 1)[0]
    if first in _WRITE_VERBS:
        return DO
    if any(word in name for word in _WORK_WORDS):
        return WORK
    return LOOK


def read_chips(reads: dict[str, int]) -> list[str]:
    """What a turn read, counted by kind: "4 emails", "1 file", "Your calendar"."""
    out: list[str] = []
    for kind, n in reads.items():
        if n <= 0:
            continue
        if kind in _UNCOUNTED:
            out.append(_UNCOUNTED[kind])
        elif n == 1:
            out.append(f"1 {kind}")
        else:
            out.append(f"{n} {_PLURALS.get(kind, kind + 's')}")
    return out


def _counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for kind, n in raw.items():
        if isinstance(kind, str) and isinstance(n, int) and not isinstance(n, bool) and n > 0:
            out[kind] = n
    return out


# Chip summaries for a step that waits on the person's OK without a card of
# its own (a meeting sent for approval, say).
_WAITING = ("Waiting", "Sent for approval")


@dataclass
class Step:
    label: str
    kind: str
    at: int
    reads: dict[str, int] = field(default_factory=dict)
    found: list[str] = field(default_factory=list)
    # Waiting on the person's OK, with the card's id when there is one.
    waits: bool = False
    card_id: int | None = None
    # Named by the turn itself (``show_progress``), not by its tool.
    named: bool = False
    # Opened by ``show_progress`` for the round about to run: the executive
    # sends it before that round's activity, which then fills this step in.
    awaits_round: bool = False
    # An ``action_taken`` already named this step.
    acted: bool = False
    # The latest line a running tool said about itself (a workflow's steps):
    # shown as the step's detail when it read nothing.
    note: str = ""

    @property
    def tried(self) -> bool:
        """A step that would change something, whose round is over with no
        chip saying it did (it failed, was refused or found nothing): it
        never reads as done."""
        return self.kind == DO and not self.acted and not self.waits

    def as_dict(self, *, current: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            # Finished steps read in the past tense, the turn's own words
            # too; an action's summary and a waiting step already say what
            # happened.
            "label": self.label if current or self.acted or self.waits or self.tried else past(self.label),
            "kind": self.kind,
            "at": self.at,
            "state": "current" if current else ("waits" if self.waits else ("tried" if self.tried else "done")),
        }
        detail = ", ".join(read_chips(self.reads)) or self.note
        if detail:
            out["detail"] = detail
        if self.found:
            out["found"] = list(self.found)
        if self.card_id is not None:
            out["card_id"] = self.card_id
        return out


class TurnTimeline:
    """The steps of one turn, from the events it streams."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._t0 = clock()
        self.task = ""
        self.steps: list[Step] = []
        self.reads: dict[str, int] = {}
        self.finished = False
        self.seconds = 0
        self._found_seen: set[str] = set()

    def _now(self) -> int:
        return max(0, int(self._clock() - self._t0))

    def _add_step(self, step: Step) -> None:
        self.steps.append(step)
        if len(self.steps) > MAX_STEPS:
            del self.steps[0 : len(self.steps) - MAX_STEPS]

    def _activity(self, item: dict[str, Any]) -> bool:
        label = _clean(item.get("label"))
        reads = _counts(item.get("reads"))
        for kind, n in reads.items():
            self.reads[kind] = self.reads.get(kind, 0) + n
        last = self.steps[-1] if self.steps else None
        unnamed = not label or label == _clean(FALLBACK_LABEL)
        if last is not None and last.awaits_round:
            if unnamed and not reads:
                # A round that only called show_progress: the next one does
                # the work it named.
                return False
            # The round show_progress named: keep its words, take the tool's
            # kind and what it read.
            last.awaits_round = False
            if not unnamed:
                last.kind = kind_of(str(item.get("tool") or ""), label)
            for kind, n in reads.items():
                last.reads[kind] = last.reads.get(kind, 0) + n
            return True
        if not unnamed and not item.get("tool") and last is not None:
            # A line a running tool says about itself (a workflow's steps
            # while run_workflow runs): the step it belongs to, not a new one.
            if last.note == label:
                return False
            last.note = label
            return True
        if unnamed:
            # An unnamed round says nothing new, but what it read still counts.
            if reads and last is not None:
                for kind, n in reads.items():
                    last.reads[kind] = last.reads.get(kind, 0) + n
            return bool(reads)
        if last is not None and not last.named and last.label == label:
            for kind, n in reads.items():
                last.reads[kind] = last.reads.get(kind, 0) + n
            return bool(reads)
        self._add_step(Step(label=label, kind=kind_of(str(item.get("tool") or ""), label), at=self._now(), reads=reads))
        return True

    def _progress(self, item: dict[str, Any]) -> bool:
        # The executive sends show_progress before the activity of the round
        # it describes: ``found`` is what the rounds so far turned up, and
        # ``now`` names the round about to run.
        changed = False
        task = _clean(item.get("task"))
        if task and task != self.task:
            self.task = task
            changed = True
        last = self.steps[-1] if self.steps else None
        found = item.get("found")
        if isinstance(found, list):
            for line in found:
                text = _clean(line)
                if not text or text.casefold() in self._found_seen:
                    continue
                self._found_seen.add(text.casefold())
                if last is None:
                    last = Step(label=task or "Started", kind=LOOK, at=self._now(), named=True)
                    self._add_step(last)
                if len(last.found) < MAX_FOUND_PER_STEP:
                    last.found.append(text)
                    changed = True
        now = _clean(item.get("now"))
        if now and (last is None or last.label != now):
            if last is not None and last.awaits_round:
                last.label = now
            else:
                self._add_step(Step(label=now, kind=WORK, at=self._now(), named=True, awaits_round=True))
            changed = True
        return changed

    def _action(self, item: dict[str, Any]) -> bool:
        summary = _clean(item.get("summary"))
        if not summary:
            return False
        # The summary alone: a chip's target is often an id, not words.
        card = item.get("decision_id")
        card_id = card if isinstance(card, int) and not isinstance(card, bool) else None
        waits = card_id is not None or summary.startswith(_WAITING)
        last = self.steps[-1] if self.steps else None
        if last is not None and last.kind == DO and not last.acted and last.card_id is None and not last.found:
            # The round that did it: name it by what it did.
            step = last
        else:
            step = Step(label=summary, kind=DO, at=self._now())
            self._add_step(step)
        step.label = summary
        step.named = True
        step.acted = True
        step.awaits_round = False
        step.waits = waits
        step.card_id = card_id
        return True

    def observe(self, item: Any) -> dict[str, Any] | None:
        """Take one streamed event; the ``timeline`` event to send when it
        changed what the timeline shows, else None. Never raises."""
        if self.finished or not isinstance(item, dict):
            return None
        try:
            kind = item.get("type")
            if kind == "activity":
                changed = self._activity(item)
            elif kind == "progress":
                changed = self._progress(item)
            elif kind == "action_taken":
                changed = self._action(item)
            else:
                return None
        except Exception:
            return None
        return self.event() if changed else None

    def finish(self, held: tuple[str, ...] | list[str] = ()) -> dict[str, Any] | None:
        """Close the turn: steps held for the person's go are added as
        waiting, and nothing is current any more. The payload to save with
        the reply, or None when the turn took no steps."""
        texts = [t for t in (_clean(w) for w in held) if t]
        # A held step's round already showed (its activity came, no chip
        # followed): that step is the one waiting, not one that failed. The
        # held tools are the turn's latest tries, so match from the end.
        tries = [s for s in self.steps if s.tried]
        for text, step in zip(reversed(texts), reversed(tries), strict=False):
            step.label = text
            step.named = True
            step.waits = True
        for text in texts[: max(0, len(texts) - len(tries))]:
            self._add_step(Step(label=text, kind=DO, at=self._now(), waits=True, named=True))
        self.finished = True
        self.seconds = self._now()
        if not self.steps:
            return None
        return self.payload()

    def drop_last(self) -> dict[str, Any] | None:
        """Take back the last step, when what became of it is not known (an
        approved card's step still running as the turn moves on: its card
        says how it went). The timeline event to send, or None."""
        if self.finished or not self.steps:
            return None
        self.steps.pop()
        return {"type": "timeline", **self.payload()}

    def payload(self) -> dict[str, Any]:
        last = len(self.steps) - 1
        out: dict[str, Any] = {
            "task": self.task,
            "seconds": self.seconds if self.finished else self._now(),
            "done": self.finished,
            "steps": [s.as_dict(current=not self.finished and i == last) for i, s in enumerate(self.steps)],
        }
        chips = read_chips(self.reads)
        if chips:
            out["reads"] = chips
        return out

    def event(self) -> dict[str, Any]:
        return {"type": "timeline", **self.payload()}


_STEP_KEYS = {"label", "kind", "at", "state", "detail", "found", "card_id"}
_KINDS = {LOOK, WORK, DO}
_STATES = {"done", "current", "waits", "tried"}


def from_saved(raw: Any) -> dict[str, Any] | None:
    """A saved timeline read back, keeping only what the chat shows, or None."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        return None
    steps: list[dict[str, Any]] = []
    for step in raw["steps"]:
        if not isinstance(step, dict) or not isinstance(step.get("label"), str):
            continue
        kept = {k: v for k, v in step.items() if k in _STEP_KEYS}
        if kept.get("kind") not in _KINDS:
            kept["kind"] = LOOK
        if kept.get("state") not in _STATES or kept.get("state") == "current":
            kept["state"] = "done"
        steps.append(kept)
    if not steps:
        return None
    reads = raw.get("reads")
    return {
        "task": raw.get("task") if isinstance(raw.get("task"), str) else "",
        "seconds": raw.get("seconds") if isinstance(raw.get("seconds"), int) else 0,
        "done": True,
        "steps": steps,
        **({"reads": [r for r in reads if isinstance(r, str)]} if isinstance(reads, list) else {}),
    }
