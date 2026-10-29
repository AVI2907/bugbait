"""Unit tests for BugBait itself.

Run from the repo root:  python -m pytest tests/ -q

Fast and hermetic: no LLM calls, no network. The two Sandbox tests run the
calculator example's real pytest suite in throwaway copies (a few seconds).
"""

import pytest

import engine
import llm
from projects import resolve


@pytest.fixture(scope="module")
def calc():
    return resolve("calculator", log=lambda m: None)


# ---------- engine.validate ----------

def test_validate_accepts_clean_bug(calc):
    bug = {"file": "calc.py", "old": "a + b", "new": "a - b", "realism": 5}
    assert engine.validate(bug, calc) is None


def test_validate_rejects_unknown_file(calc):
    bug = {"file": "nope.py", "old": "a + b", "new": "a - b"}
    assert engine.validate(bug, calc) == "file not found"


def test_validate_rejects_missing_text(calc):
    bug = {"file": "calc.py", "old": "a + b + c", "new": "a - b"}
    assert engine.validate(bug, calc) == "'old' text not found in file"


def test_validate_rejects_ambiguous_text(calc):
    bug = {"file": "calc.py", "old": "return", "new": "yield"}  # appears 4 times
    assert engine.validate(bug, calc) == "'old' text is ambiguous (appears more than once)"


def test_validate_rejects_no_change(calc):
    bug = {"file": "calc.py", "old": "a + b", "new": "a + b"}
    assert engine.validate(bug, calc) == "no change"


def test_validate_rejects_artificial_bug(calc):
    bug = {"file": "calc.py", "old": "a + b", "new": "a - b", "realism": 2}
    assert engine.validate(bug, calc, min_realism=3) == "too artificial (realism 2/5)"


def test_validate_rejects_syntax_breaking_bug(calc):
    bug = {"file": "calc.py", "old": "return a + b", "new": "return a +"}
    assert engine.validate(bug, calc) == "mutation is not valid Python"


# ---------- engine.merge_imports / build_test_file ----------

def test_merge_imports_combines_and_dedupes():
    lines = ["from x import b", "import os", "from x import a", "import os", "from y import c"]
    assert engine.merge_imports(lines) == ["import os", "from x import b, a", "from y import c"]


def test_build_test_file_renames_and_merges():
    bug1 = {"file": "calc.py", "old": "a + b", "new": "a - b"}
    bug2 = {"file": "calc.py", "old": "age >= 18", "new": "age > 18"}
    code1 = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    code2 = "from calc import is_adult\n\n\ndef test_adult():\n    assert is_adult(18)\n"
    out = engine.build_test_file([(1, bug1, code1), (2, bug2, code2)])
    assert "def test_bug1_add():" in out
    assert "def test_bug2_adult():" in out
    assert out.count("from calc import") == 1
    assert "from calc import add, is_adult" in out
    assert "Catches bug 1" in out and "Catches bug 2" in out


# ---------- engine.bug_context / enclosing_function / risk_map ----------

def test_bug_context_finds_line(calc):
    bug = {"file": "calc.py", "old": "age >= 18", "new": "age > 18"}
    ctx = engine.bug_context(bug, calc)
    assert ctx["line"] == 10
    assert any("age >= 18" in line for line in ctx["lines"])


def test_enclosing_function_names_method(calc):
    assert engine.enclosing_function({"file": "calc.py", "old": "age >= 18"}, calc) == "is_adult"
    assert engine.enclosing_function({"file": "calc.py", "old": "a + b"}, calc) == "add"


def test_risk_map_ranks_worst_first():
    bugs = [
        {"file": "a.py", "function": "good", "survived_original_tests": False, "context": {"line": 1}},
        {"file": "a.py", "function": "bad", "survived_original_tests": True, "context": {"line": 5}},
        {"file": "a.py", "function": "bad", "survived_original_tests": True, "context": {"line": 9}},
    ]
    risk = engine.risk_map(bugs)
    assert risk["functions"][0]["function"] == "bad"
    assert risk["functions"][0]["survived"] == 2
    assert risk["files"] == [{"file": "a.py", "planted": 3, "survived": 2}]


def test_generated_path_sits_next_to_tests(calc):
    assert engine.generated_path(calc) == engine.GENERATED_NAME  # calculator tests live at root


# ---------- projects ----------

def test_source_files_excludes_tests(calc):
    paths = [f["path"] for f in calc.source_files()]
    assert paths == ["calc.py"]


def test_test_dir_and_defaults(calc):
    assert calc.test_dir() == "."
    assert calc.default_files() == ["calc.py"]
    assert calc.is_test_file(__import__("pathlib").Path("tests/test_x.py"))
    assert not calc.is_test_file(__import__("pathlib").Path("calc.py"))


def test_relevant_tests_mentions_module_first(calc):
    text = calc.relevant_tests("calc.py")
    assert "test_calc.py" in text


# ---------- llm.extract_json ----------

def test_extract_json_from_fence():
    reply = 'Here you go:\n```json\n[{"old": "a", "new": "b"}]\n```'
    assert llm.extract_json(reply) == [{"old": "a", "new": "b"}]


def test_extract_json_ignores_reasoning_and_prose():
    reply = "<think>hmm</think>\nSome prose [1, 2] trailing"
    assert llm.extract_json(reply) == [1, 2]


# ---------- runner.Sandbox (integration, still local) ----------

def test_sandbox_baseline_passes(calc):
    from runner import Sandbox

    with Sandbox(calc) as sb:
        passed, output = sb.run_tests(stop_early=False)
    assert passed, output[-1000:]


def test_sandbox_mutation_is_caught_by_existing_tests(calc):
    from runner import Sandbox

    with Sandbox(calc) as sb:
        sb.apply_mutation("calc.py", "a + b", "a - b")
        passed, _ = sb.run_tests()
    assert not passed  # test_add catches it


def test_sandbox_mutation_survives_weak_tests(calc):
    from runner import Sandbox

    with Sandbox(calc) as sb:
        sb.apply_mutation("calc.py", "age >= 18", "age > 18")
        passed, _ = sb.run_tests()
    assert passed  # boundary never tested, so the bug gets through
