"""Tests for scripts/one_gate.py: the whole-code check that only the one gate
decides whether a step runs or waits, and its pr_checks.py wiring."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


one_gate = _load("one_gate")
pr_checks = _load("pr_checks")
PKG = one_gate.PKG


def _kinds(source: str, path: str = PKG + "delegation/x.py") -> list[tuple[str, str]]:
    return [(h.kind, h.what) for h in one_gate.scan_source(path, source)]


def test_finds_a_word_list_hold_by_its_shape_not_its_words() -> None:
    """The As me word list held replies with no "only the owner" or 403 in
    sight: what gives it away is a function that says it holds."""
    src = '''
SENSITIVE = ("medical", "press", "board")
def content_hold(text):
    if any(w in text for w in SENSITIVE):
        return "sensitive"
    return None
'''
    assert ("named", "def content_hold") in _kinds(src)


def test_finds_a_feature_reading_the_level_itself() -> None:
    src = '''
from openexecutive.orchestrator import outside_mode
from openexecutive.orchestrator.outside_mode import TRUST
def hands_off():
    return outside_mode.get_mode() == outside_mode.TRUST
def also(level):
    return level in ("outsiders", "trust")
'''
    kinds = _kinds(src)
    assert ("level", "outside_mode.TRUST") in kinds
    assert ("level", "compares with 'trust'") in kinds
    assert ("level", "compares with 'outsiders'") in kinds
    # Showing the level is not choosing by it.
    assert _kinds("def shown():\n    return WORDS[outside_mode.get_mode()]\n") == []


def test_finds_its_own_training_rules_cards_and_waiting_results() -> None:
    src = '''
def f(person_id, rules, text):
    if training.in_training(person_id):
        return 1
    if take_the_lead.rule_waits(rules, text, []):
        return 2
    take_the_lead.check("send", {}, lead=None, rules=rules)
    create_decision_instance(decision_class="x")
    department_gate.hold(1, "t", {}, None)
    return json.dumps({"status": "refused", "detail": "no"})
'''
    kinds = {k for k, _ in _kinds(src)}
    assert kinds == {"training", "rules", "card", "waiting"}


def test_reading_a_waiting_result_is_fine() -> None:
    src = 'def f(r):\n    return r.get("status") == "waiting_for_approval"\n'
    assert _kinds(src) == []


def test_names_that_hold_refuse_withhold_or_gate() -> None:
    for name in ("approvals_hold", "_refusal", "count_refusal", "_meeting_gate", "read_only_withholds",
                 "refuse_principal_only_run", "owner_change_blocked", "_denied"):
        assert _kinds(f"def {name}():\n    pass\n") == [("named", f"def {name}")], name
    # A prompt block, a placeholder or a threshold is no hold.
    for name in ("context_block", "build_system_blocks", "placeholder", "threshold", "household"):
        assert _kinds(f"def {name}():\n    pass\n") == [], name


def test_gate_files_are_where_it_belongs(tmp_path: Path) -> None:
    gate = tmp_path / PKG / "orchestrator" / "levels.py"
    gate.parent.mkdir(parents=True)
    gate.write_text("def hold():\n    return outside_mode.TRUST\n")
    other = tmp_path / PKG / "orchestrator" / "feature.py"
    other.write_text("def _refusal():\n    pass\n")
    hits = one_gate.scan_tree(str(tmp_path))
    assert [h.key for h in hits] == [PKG + "orchestrator/feature.py | named | def _refusal"]


def test_compare_counts_each_entry_once() -> None:
    hits = [one_gate.Hit("p.py", 1, "named", "def _refusal"), one_gate.Hit("p.py", 9, "named", "def _refusal")]
    known = one_gate.parse_known("# header\np.py | named | def _refusal | remove\n")
    new, gone = one_gate.compare(hits, known)
    assert [h.line for h in new] == [9]
    assert gone == []
    new, gone = one_gate.compare([], known)
    assert new == [] and gone == ["p.py | named | def _refusal"]


def _tree(tmp_path: Path, source: str, known: str) -> str:
    f = tmp_path / PKG / "delegation" / "x.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(source)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / one_gate.KNOWN).write_text(known)
    return str(tmp_path)


def _check(**kw) -> str:  # type: ignore[no-untyped-def]
    kw.setdefault("changed", set())
    return pr_checks.check_one_gate_code(pr_checks.Change(**kw)).level


def test_code_check_fails_on_a_hold_not_on_the_list(tmp_path: Path) -> None:
    root = _tree(tmp_path, "def content_hold():\n    pass\n", "")
    assert _check(root=root) == "FAIL"
    # Already listed: it passes until it is taken out.
    root = _tree(tmp_path, "def content_hold():\n    pass\n", f"{PKG}delegation/x.py | named | def content_hold | remove\n")
    assert _check(root=root) == "PASS"


def test_code_check_fails_when_a_listed_hold_is_gone_but_still_listed(tmp_path: Path) -> None:
    root = _tree(tmp_path, "def fine():\n    pass\n", f"{PKG}delegation/x.py | named | def content_hold | remove\n")
    assert _check(root=root) == "FAIL"


def test_the_list_only_shrinks_and_has_no_waiver() -> None:
    entry = f"{PKG}delegation/x.py | named | def content_hold | remove"
    added = {one_gate.KNOWN: [entry]}
    assert _check(changed={one_gate.KNOWN}, added_lines=added) == "FAIL"
    assert _check(
        changed={one_gate.KNOWN}, added_lines=added, waiver_text="Gates: n/a - it is fine",
    ) == "FAIL"
    # A reworded note is the same entry.
    assert _check(
        changed={one_gate.KNOWN}, added_lines={one_gate.KNOWN: [entry + " now"]},
        removed_lines={one_gate.KNOWN: [entry]},
    ) == "PASS"
    # Taking one off is fine.
    assert _check(changed={one_gate.KNOWN}, removed_lines={one_gate.KNOWN: [entry]}) == "PASS"


def test_the_repo_matches_its_list() -> None:
    """The code as it stands and scripts/one_gate_known.txt agree."""
    root = str(_SCRIPTS.parent)
    new, gone = one_gate.compare(one_gate.scan_tree(root), one_gate.load_known(root))
    assert new == [], [str(h) for h in new[:10]]
    assert gone == [], gone[:10]


def test_finds_a_word_list_by_what_it_is_called() -> None:
    """A list of words that holds is a gate, whatever the function using it
    is called (the group alerts' _violates_privacy_invariant)."""
    src = '_PRIVACY_SENSITIVE_TAGS = frozenset({"board", "comp", "legal", "hr", "layoffs"})\n'
    assert _kinds(src) == [("words", "_PRIVACY_SENSITIVE_TAGS")]
    assert _kinds('SHARED_CHAT_WITHHELD_TOOLS = ("a", "b", "c", "d", "e")\n') == [
        ("words", "SHARED_CHAT_WITHHELD_TOOLS")
    ]
    # Stop words, a short list, or a list of other things are not.
    assert _kinds('_STOPWORDS = {"a", "the", "of", "and", "to"}\n') == []
    assert _kinds('_SENSITIVE = ("a", "b")\n') == []
    assert _kinds('_BLOCK_TAGS = ("p", "div", "li", "ul", "ol")\n') == []


def test_other_ways_of_writing_a_held_result() -> None:
    for src in (
        'def f():\n    return Result(status="held")\n',
        'def f(r):\n    r["status"] = "refused"\n',
        'def f():\n    return dict(status="waiting_for_approval")\n',
    ):
        assert [k for k, _ in _kinds(src)] == ["waiting"], src


def test_an_import_alias_or_dotted_path_still_reads_the_level() -> None:
    src = (
        "from openexecutive.orchestrator import outside_mode as om\n"
        "def f():\n    return om.get_mode() == om.TRUST\n"
    )
    assert ("level", "outside_mode.TRUST") in _kinds(src)
    src = "import openexecutive.orchestrator.outside_mode\ndef f(m):\n    return m == openexecutive.orchestrator.outside_mode.ASK\n"
    assert ("level", "outside_mode.ASK") in _kinds(src)


def test_moving_a_listed_hold_into_a_renamed_file_is_the_same_entry() -> None:
    old = f"{PKG}delegation/inbox.py | named | def _refusal | remove"
    new = f"{PKG}delegation/inbox_watch.py | named | def _refusal | remove"
    moved = {
        "changed": {one_gate.KNOWN, PKG + "delegation/inbox_watch.py"},
        "renamed": {PKG + "delegation/inbox_watch.py"},
        "added_lines": {one_gate.KNOWN: [new]},
        "removed_lines": {one_gate.KNOWN: [old]},
    }
    assert _check(**moved) == "PASS"
    # Into a file that was already there and the change edits, too.
    assert _check(**{**moved, "renamed": set()}) == "PASS"
    # Into a file the change doesn't touch, it is a new entry.
    assert _check(**{**moved, "renamed": set(), "changed": {one_gate.KNOWN}}) == "FAIL"
    # Nor does a move make room for a second copy.
    assert _check(**{**moved, "added_lines": {one_gate.KNOWN: [new, new]}}) == "FAIL"
