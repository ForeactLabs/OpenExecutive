#!/usr/bin/env python3
"""The one gate, checked over the whole code.

THE RULES (CLAUDE.md, "The Rules - No New Gates"): role says what someone
can do, the Approvals level says whether a step runs or waits, and only Your
rules and In training cap the level. Nothing else may hold or refuse a step.

So exactly one place decides run-or-wait: ``GATE_FILES`` below (the level
in ``orchestrator/levels.py``, Your rules and In training in
``take_the_lead.gate``). Everything else may only *notice* a reason and pass
it to ``levels``. This reads every Python file under
``packages/core/openexecutive/``, not just a diff (a hold that was already
there is as wrong as a new one), and finds any of these outside the gate:

- ``card``      makes a waiting card itself (``create_decision_instance``,
                ``department_gate.hold``)
- ``level``     reads the Approvals level to choose for itself
                (``outside_mode.ASK``/``OUTSIDERS``/``TRUST``/``for_turn``,
                ``levels.turn_level``/``levels.level``,
                ``approvals_lead.level``, or a comparison with the level's
                own words ``"outsiders"``/``"trust"``)
- ``training``  applies In training itself (``in_training``,
                ``all_allowed``, ``find_allowed``, ``training_hold``)
- ``rules``     applies Your rules itself (``rule_waits``, ``rule_goes``,
                ``goes_rules``, ``lets_go``, ``take_the_lead.check``)
- ``waiting``   writes a waiting outcome of its own (a ``"status"`` of
                ``waiting_for_approval``, ``held``, ``blocked``,
                ``refused``, ``denied`` or ``waiting``)
- ``named``     defines a function whose name says it holds, refuses,
                withholds or gates (``content_hold``, ``_refusal``,
                ``_meeting_gate``, ``read_only_withholds`` ...)
- ``words``     keeps a module-level list of five or more words or tools
                whose name says what it holds back (``SENSITIVE``,
                ``_PRIVACY_SENSITIVE_TAGS``, ``PRIVATE_TURN_WITHHELD_TOOLS``,
                ``_BLOCKED_WORDS``, ``CHAT_TOOL_DENY_PATTERNS`` ...): a word
                list that holds is a gate whatever its function is called

``KNOWN`` (``scripts/one_gate_known.txt``) lists what the code holds today,
each with what happens to it. ``pr_checks.py``'s ``one-gate`` fails a change
that leaves anything here not on that list, and a change that adds a line to
the list: it can only shrink. There is no waiver.

    python3 scripts/one_gate.py            # every hit, and how many
    python3 scripts/one_gate.py --new      # only hits not on the list
    python3 scripts/one_gate.py --write    # rewrite the list (keeps notes)

Stdlib only, so it runs without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import warnings
from collections import Counter
from dataclasses import dataclass

PKG = "packages/core/openexecutive/"
KNOWN = "scripts/one_gate_known.txt"

# Where run-or-wait is decided, and nowhere else. Adding a file here is
# adding a gate: it needs the owner's yes (CODEOWNERS reviews this script).
GATE_FILES: frozenset[str] = frozenset({
    # The level, its setting, and what each level notices.
    PKG + "orchestrator/levels.py",
    PKG + "orchestrator/outside_mode.py",
    PKG + "orchestrator/outside_content.py",
    # Your rules, In training and the gate that reads them.
    PKG + "orchestrator/take_the_lead.py",
    # The card a held step becomes, and where it is kept.
    PKG + "orchestrator/department_gate.py",
    PKG + "memory/decision_ledger.py",
})

CARD_CALLS = frozenset({"create_decision_instance"})
CARD_ATTRS = frozenset({("department_gate", "hold")})
LEVEL_NAMES = {
    "outside_mode": frozenset({"ASK", "OUTSIDERS", "TRUST", "for_turn"}),
    "levels": frozenset({"turn_level", "level"}),
    "approvals_lead": frozenset({"level"}),
}
# "ask" is too common a word to tell from the level's.
LEVEL_WORDS = frozenset({"outsiders", "trust"})
TRAINING_CALLS = frozenset({"in_training", "all_allowed", "find_allowed", "training_hold"})
RULE_CALLS = frozenset({"rule_waits", "rule_goes", "goes_rules", "lets_go", "_rule_hit"})
RULE_ATTRS = frozenset({("take_the_lead", "check")})
HELD_STATUSES = frozenset({"waiting_for_approval", "held", "blocked", "refused", "denied", "waiting"})
NAMED_RE = re.compile(
    r"(?:^|_)(?:hold|holds|held|refusal|refuse|refused|refuses|blocked|deny|denied|"
    r"forbid|forbidden|gate|gated|withhold|withholds|withheld)(?:_|$)"
)

WORDS_RE = re.compile(
    r"(?:^|_)(?:SENSITIVE|PRIVACY|PRIVATE|BLOCKED|DENY|DENIED|BANNED|RISKY|CREDENTIALS|FORBIDDEN|"
    r"WITHHELD|HELD|HOLD|HOLDS|REFUSED)(?:_|$)"
)
WORDS_MIN = 5


def _word_list(value: ast.AST | None) -> bool:
    if isinstance(value, ast.Call) and value.args:
        value = value.args[0]
    if not isinstance(value, (ast.Set, ast.Tuple, ast.List)):
        return False
    return sum(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts) >= WORDS_MIN


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    kind: str
    what: str

    @property
    def key(self) -> str:
        return f"{self.path} | {self.kind} | {self.what}"

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.kind}: {self.what}"


def _pair(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    return None


def _called(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def scan_source(path: str, source: str) -> list[Hit]:
    """Every hit in one file's source (its path as the repo names it)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []
    hits: list[Hit] = []

    def add(node: ast.AST, kind: str, what: str) -> None:
        hits.append(Hit(path, getattr(node, "lineno", 0), kind, what))

    for top in tree.body:
        targets = top.targets if isinstance(top, ast.Assign) else [top.target] if isinstance(top, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Name) and WORDS_RE.search(target.id.upper()) and _word_list(top.value):
                add(top, "words", target.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name, pair = _called(node), _pair(node.func)
            if name in CARD_CALLS or pair in CARD_ATTRS:
                add(node, "card", name if pair is None else ".".join(pair))
            elif name in TRAINING_CALLS:
                add(node, "training", name)
            elif name in RULE_CALLS or pair in RULE_ATTRS:
                add(node, "rules", name if pair is None else ".".join(pair))
        elif isinstance(node, ast.Attribute):
            pair = _pair(node)
            if pair is not None and pair[1] in LEVEL_NAMES.get(pair[0], ()):
                add(node, "level", ".".join(pair))
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").rsplit(".", 1)[-1]
            for alias in node.names:
                if alias.name in LEVEL_NAMES.get(module, ()):
                    add(node, "level", f"{module}.{alias.name}")
        elif isinstance(node, ast.Compare):
            for side in (node.left, *node.comparators):
                values = side.elts if isinstance(side, (ast.Tuple, ast.List, ast.Set)) else [side]
                for v in values:
                    if isinstance(v, ast.Constant) and v.value in LEVEL_WORDS:
                        add(node, "level", f"compares with {v.value!r}")
        elif isinstance(node, ast.Dict):
            # Writing a waiting outcome; reading one to pass it on is fine.
            for key, value in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(key, ast.Constant) and key.value == "status"
                    and isinstance(value, ast.Constant) and value.value in HELD_STATUSES
                ):
                    add(node, "waiting", f"status {value.value}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if NAMED_RE.search(node.name.lower()):
                add(node, "named", f"def {node.name}")
    return hits


def scan_tree(root: str = ".") -> list[Hit]:
    hits: list[Hit] = []
    for dirpath, dirs, files in os.walk(os.path.join(root, PKG)):
        dirs.sort()
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in GATE_FILES:
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                hits.extend(scan_source(rel, fh.read()))
    return sorted(hits, key=lambda h: (h.path, h.line, h.kind, h.what))


def parse_known(text: str) -> list[tuple[str, str]]:
    """``(key, note)`` for each entry: ``path | kind | what | note``."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        out.append((" | ".join(parts[:3]), " | ".join(parts[3:])))
    return out


def load_known(root: str = ".") -> list[tuple[str, str]]:
    try:
        with open(os.path.join(root, KNOWN), encoding="utf-8") as fh:
            return parse_known(fh.read())
    except OSError:
        return []


def compare(hits: list[Hit], known: list[tuple[str, str]]) -> tuple[list[Hit], list[str]]:
    """``(new, gone)``: hits the list doesn't hold, and entries no hit has."""
    left = Counter(k for k, _ in known)
    new: list[Hit] = []
    for h in hits:
        if left[h.key] > 0:
            left[h.key] -= 1
        else:
            new.append(h)
    gone = sorted(k for k, n in left.items() for _ in range(n))
    return new, gone


HEADER = """\
# What the code holds, refuses or decides outside the one gate today
# (scripts/one_gate.py). CI fails on anything in the code not listed here,
# and on a line added here: this list only shrinks. There is no waiver.
#
# path | kind | what | what happens to it
"""


def write_known(hits: list[Hit], root: str = ".") -> None:
    notes: dict[str, list[str]] = {}
    for key, note in load_known(root):
        notes.setdefault(key, []).append(note)
    lines = [HEADER]
    for h in hits:
        kept = notes.get(h.key) or [""]
        note = kept.pop(0) if len(kept) > 1 else kept[0]
        lines.append((f"{h.key} | {note}" if note else h.key) + "\n")
    with open(os.path.join(root, KNOWN), "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--new", action="store_true", help="only hits not on the list")
    ap.add_argument("--write", action="store_true", help="rewrite the list from the code")
    args = ap.parse_args(argv)
    hits = scan_tree(".")
    if args.write:
        write_known(hits)
        print(f"one-gate: wrote {len(hits)} entries to {KNOWN}")
        return 0
    new, gone = compare(hits, load_known("."))
    for h in new if args.new else hits:
        print(h)
    for k in gone:
        print(f"gone (take it off {KNOWN}): {k}")
    print(
        f"one-gate: {len(hits)} hit(s) in {len({h.path for h in hits})} file(s), "
        f"{len(new)} not on the list, {len(gone)} listed but gone; {dict(Counter(h.kind for h in hits))}"
    )
    return 1 if new or gone else 0


if __name__ == "__main__":
    sys.exit(main())
