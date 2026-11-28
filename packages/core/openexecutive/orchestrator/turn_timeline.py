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

from openexecutive.orchestrator import step_icons
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
    "Clicking": "Clicked",
    "Comparing": "Compared",
    "Consulting": "Consulted",
    "Copying": "Copied",
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
    "Picking": "Picked",
    "Planning": "Planned",
    "Pointing": "Pointed",
    "Preparing": "Prepared",
    "Pressing": "Pressed",
    "Reading": "Read",
    "Recalling": "Recalled",
    "Replying": "Replied",
    "Researching": "Researched",
    "Reviewing": "Reviewed",
    "Running": "Ran",
    "Saving": "Saved",
    "Scheduling": "Scheduled",
    "Scrolling": "Scrolled",
    "Searching": "Searched",
    "Sending": "Sent",
    "Setting": "Set",
    "Sharing": "Shared",
    "Summarizing": "Summarized",
    "Switching": "Switched",
    "Typing": "Typed",
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


# A browser round's own words name no page; with the site known they do.
_PAGE_WORDS = {
    "Looking at the page": "Looking at {}",
    "Opening the page": "Opening {}",
    "Opening a tab": "Opening {}",
}


def _on_site(label: str, site: str) -> str:
    """``label`` naming ``site`` when it is a browser round's generic line."""
    words = _PAGE_WORDS.get(label)
    return words.format(site) if words and site else label


def past(label: str) -> str:
    """``label`` as a step already done."""
    first, sep, rest = label.partition(" ")
    done = _PAST.get(first)
    return f"{done}{sep}{rest}" if done else label


# A round whose label starts with one of these changes something, whatever
# its tool is called (a connected app's or an extension's write, which the
# built-in list can't name): it goes under Did, and when its tool makes
# chips (chip_follows) it reads as done only once one says so.
_WRITE_VERBS = frozenset({
    "Accepting", "Adding", "Archiving", "Booking", "Cancelling", "Copying", "Creating",
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


def chip_follows(tool: str) -> bool:
    """Whether a round of ``tool`` that works always sends an action chip
    (action_chips.summarize_action): a built-in write, a connected app's
    tool (its activity names it ``server__tool``) or an extension tool with
    a chip."""
    if not tool:
        return False
    if tool in SIDE_EFFECTING_TOOLS or "__" in tool:
        return True
    from openexecutive.orchestrator import extensions

    return extensions.has_chip(tool)


def _is_cut_off(label: str) -> bool:
    from openexecutive.orchestrator.executive import CUT_OFF_LABEL

    return label == _clean(CUT_OFF_LABEL)


def _round_matches(step_tool: str, held_tool: str) -> bool:
    """Whether a round's tool is the held one (a connected app's comes cut
    short and cleaned in its activity)."""
    if not step_tool:
        return False
    return step_tool == held_tool or _same_app_tool(step_tool, held_tool)


def _same_app_tool(step_tool: str, chip_tool: str) -> bool:
    """A connected app's chip and the round it came from: the activity names
    the tool cut short and cleaned, the chip by its raw name."""
    return "__" in step_tool and chip_tool.startswith(step_tool[:40])


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
    # Its tool leaves an action chip whenever it works (a built-in write, a
    # connected app's tool, an extension tool with a chip): with no chip,
    # it didn't go through. A tool that never makes a chip (send_text,
    # save_file) can't say either way, so its step reads as done.
    confirms: bool = False
    # The round's tool, as its activity named it.
    tool: str = ""
    # The turn was stopped (or timed out) while this step ran: it never
    # finished, so it neither reads as done nor as failed.
    stopped: bool = False
    # The website it was on (``step_icons.site_of``), for the site's icon.
    site: str = ""
    # A browser line's own words before it named its site ("Looking at the
    # page"), so the next round on that page is the same step.
    plain: str = ""

    @property
    def tried(self) -> bool:
        """A step that would change something, whose round is over with no
        chip saying it did (it failed, was refused or found nothing): it
        never reads as done."""
        return self.kind == DO and self.confirms and not self.acted and not self.waits

    def _state(self, current: bool) -> str:
        if current:
            return "current"
        if self.waits:
            return "waits"
        if self.stopped:
            return "stopped"
        return "tried" if self.tried else "done"

    def as_dict(self, *, current: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            # Finished steps read in the past tense, the turn's own words
            # too; an action's summary and a waiting step already say what
            # happened.
            "label": (
                self.label
                if current or self.acted or self.waits or self.stopped or self.tried
                else past(self.label)
            ),
            "kind": self.kind,
            "at": self.at,
            "state": self._state(current),
        }
        detail = ", ".join(read_chips(self.reads)) or self.note
        if detail:
            out["detail"] = detail
        if self.found:
            out["found"] = list(self.found)
        if self.card_id is not None:
            out["card_id"] = self.card_id
        icon = step_icons.icon_for(self.tool, self.kind)
        if icon:
            out["icon"] = icon
        # Only a step that ran shows the site it was on.
        if self.site and out["state"] in ("done", "current"):
            out["site"] = self.site
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
        # The reply has been streaming since the last step: no step is
        # running any more.
        self.writing = False
        self._found_seen: set[str] = set()
        # The site of the page the turn last opened in the browser: the
        # rounds after it (looking, clicking) are on that page.
        self._site = ""

    def _now(self) -> int:
        return max(0, int(self._clock() - self._t0))

    def wrote(self) -> None:
        """Reply text streamed: the last step's round is over."""
        self.writing = True

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
        if not unnamed and not item.get("tool") and _is_cut_off(label):
            # The executive's own line for a round cut short: no step's.
            return False
        if last is not None and last.awaits_round:
            if unnamed and not reads:
                # A round that only called show_progress: the next one does
                # the work it named.
                return False
            # The round show_progress named: keep its words, take the tool's
            # kind and what it read.
            last.awaits_round = False
            if not unnamed:
                tool = str(item.get("tool") or "")
                last.kind = kind_of(tool, label)
                last.confirms = chip_follows(tool)
                last.tool = tool
                last.site = self._page_site(tool, item)
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
        tool = str(item.get("tool") or "")
        # The line names the site only once the browser says this step ran
        # there (``_step_site``), never from the page an earlier step was on.
        site = self._page_site(tool, item)
        # A step whose line now names its site still takes the same round on
        # that page again ("Looking at the page" after "Looking at
        # papajohns.com"); a round that opens an address never merges into it.
        again = last is not None and (
            last.label == label or (bool(last.plain) and last.plain == label and not step_icons.opens_page(tool))
        )
        if last is not None and not last.named and again:
            for kind, n in reads.items():
                last.reads[kind] = last.reads.get(kind, 0) + n
            return bool(reads)
        self._add_step(
            Step(
                label=label,
                kind=kind_of(tool, label),
                at=self._now(),
                reads=reads,
                confirms=chip_follows(tool),
                tool=tool,
                site=site,
            )
        )
        return True

    def _page_site(self, tool: str, item: dict[str, Any]) -> str:
        """The site a round of ``tool`` is on: the page the browser last
        reported (``step_site``) when the round acts on the open page. A
        round that opens a new address gets its site only once the browser
        says it went there, never from what the model asked for."""
        if step_icons.leaves_page(tool) or step_icons.opens_page(tool):
            self._site = ""
            return ""
        return self._site if step_icons.on_page(tool) else ""

    def _step_site(self, item: dict[str, Any]) -> bool:
        """The browser ran a step on ``site``: the page the turn is on now,
        and the site of the step that ran it, which its line then names."""
        site = item.get("site")
        if not (isinstance(site, str) and step_icons.valid_site(site)):
            return False
        self._site = site
        last = self.steps[-1] if self.steps else None
        if last is None or not step_icons.on_page(last.tool):
            return False
        if last.plain and last.site and last.site != site:
            # A merged round ran on another page (it moved on its own): it
            # is a step of its own, named for where it ran.
            self._add_step(
                Step(
                    label=_on_site(last.plain, site),
                    kind=last.kind,
                    at=self._now(),
                    confirms=last.confirms,
                    tool=last.tool,
                    site=site,
                    plain=last.plain,
                )
            )
            return True
        label = _on_site(last.label, site)
        if last.site == site and last.label == label:
            return False
        if label != last.label:
            last.plain = last.label
        last.site = site
        last.label = label
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
        if last is not None and not last.acted and last.card_id is None and not last.found and (
            last.kind == DO or _same_app_tool(last.tool, str(item.get("tool") or ""))
        ):
            # The round that did it: name it by what it did. A connected
            # app's read keeps its group (its chip says what it looked at).
            step = last
        else:
            step = Step(label=summary, kind=DO, at=self._now(), tool=str(item.get("tool") or ""))
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
                self.writing = False
                changed = self._activity(item)
            elif kind == "progress":
                self.writing = False
                changed = self._progress(item)
            elif kind == "action_taken":
                changed = self._action(item)
            elif kind == "step_site":
                changed = self._step_site(item)
            else:
                return None
        except Exception:
            return None
        return self.event() if changed else None

    def finish(
        self,
        held: tuple[str, ...] | list[str] = (),
        *,
        held_tools: tuple[str, ...] | list[str] = (),
        interrupted: bool = False,
    ) -> dict[str, Any] | None:
        """Close the turn: steps held for the person's go are added as
        waiting, and nothing is current any more. On a stopped or timed-out
        turn the step that was running is saved as stopped, not done. The
        payload to save with the reply, or None when the turn took no steps."""
        if interrupted and not self.writing and not self.finished and self.steps:
            last = self.steps[-1]
            if not (last.acted or last.waits):
                last.stopped = True
        texts = [t for t in (_clean(w) for w in held) if t]
        # A held step's round already showed (its activity came, no chip
        # followed): that round is the one waiting, not one that failed.
        # ``held_tools`` names the held tools; each round of one, still
        # without a chip, waits, but only after the turn's first round that
        # could read outside content: a step is held only once something
        # from outside was read, so a round of the same tool before that
        # went through.
        from openexecutive.orchestrator.outside_content import may_read_outside, step_words

        first_read = next(
            (i for i, s in enumerate(self.steps) if may_read_outside(s.tool)), len(self.steps)
        )
        covered: set[str] = set()
        for tool in dict.fromkeys(str(t) for t in held_tools if t):
            for step in self.steps[first_read + 1 :]:
                if (
                    _round_matches(step.tool, tool)
                    and step.tool != "answer_card"
                    and not (step.acted or step.waits or step.stopped)
                ):
                    # Its round's own words ("Messaging Dana") say more
                    # than the held text, the tool's generic name. A held
                    # tool is a write, whatever its label says.
                    step.kind = DO
                    step.named = True
                    step.waits = True
                    covered.add(_clean(step_words(tool)))
        extra = [t for t in texts if t not in covered]
        for text in extra:
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


_STEP_KEYS = {"label", "kind", "at", "state", "detail", "found", "card_id", "icon", "site"}
_KINDS = {LOOK, WORK, DO}
_STATES = {"done", "current", "waits", "tried", "stopped"}


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
        if "icon" in kept and kept["icon"] not in step_icons.ICONS:
            del kept["icon"]
        if "site" in kept and not step_icons.valid_site(kept["site"]):
            del kept["site"]
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
