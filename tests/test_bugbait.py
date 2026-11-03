"""Unit tests for BugBait itself.

Run from the repo root:  python -m pytest tests/ -q

Fast and hermetic: no LLM calls, no network. The two Sandbox tests run the
calculator example's real pytest suite in throwaway copies (a few seconds).
"""

import pytest

import engine
import languages
import llm
from projects import Project, resolve


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


def test_build_test_file_renames_and_merges(tmp_path):
    bug1 = {"file": "calc.py", "old": "a + b", "new": "a - b"}
    bug2 = {"file": "calc.py", "old": "age >= 18", "new": "age > 18"}
    code1 = "from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    code2 = "from calc import is_adult\n\n\ndef test_adult():\n    assert is_adult(18)\n"
    p = Project(name="x", root=tmp_path, source="local")
    out = engine.build_test_file([(1, bug1, code1), (2, bug2, code2)], p)
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
    assert engine.generated_path(calc) == "test_bugbait_generated.py"  # calculator tests live at root


def test_project_info_counts_test_files(calc):
    assert engine.project_info(calc)["test_files"] == 1


def test_no_tests_message_without_test_files(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    p = Project(name="notests", root=tmp_path, source="local")
    msg = engine.no_tests_message(p)
    assert "No Python test files" in msg and "pytest" in msg


def test_no_tests_message_for_foreign_project(tmp_path):
    (tmp_path / "Main.java").write_text("class Main {}\n")
    p = Project(name="java", root=tmp_path, source="local")
    assert "more languages are coming" in engine.no_tests_message(p)


def test_no_tests_message_javascript(tmp_path):
    (tmp_path / "calc.js").write_text("export const add = (a, b) => a + b;\n")
    p = Project(name="js", root=tmp_path, source="local", language=languages.JAVASCRIPT)
    msg = engine.no_tests_message(p)
    assert "No JavaScript test files" in msg and "node --test" in msg


def test_no_tests_message_with_uncollectable_tests(calc):
    assert "collected no tests" in engine.no_tests_message(calc)


def test_python_baseline_sets_aside_failing_tests(tmp_path):
    import sys

    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "test_a.py").write_text("from a import f\ndef test_ok():\n    assert f() == 1\n"
                                        "def test_bad():\n    assert f() == 2\n")
    p = Project(name="p", root=tmp_path, source="local", python=sys.executable)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1
    assert p.pytest_args == ["--deselect", "test_a.py::test_bad"]


def test_baseline_without_tests_explains_itself(tmp_path):
    import sys

    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    p = Project(name="notests", root=tmp_path, source="local", python=sys.executable)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) is None
    name, data = events[-1]
    assert name == "baseline" and data["passed"] is False
    assert "No Python test files" in data["output"]


def test_build_js_test_file_renames_and_merges(tmp_path):
    bug1 = {"file": "calc.js", "old": "a + b", "new": "a - b"}
    code1 = ("const test = require('node:test');\nconst assert = require('node:assert');\n"
             "const { add } = require('./calc.js');\ntest('adds', () => assert.equal(add(1, 1), 2));\n")
    p = Project(name="x", root=tmp_path, source="local", language=languages.JAVASCRIPT)
    out = engine.build_test_file([(1, bug1, code1)], p)
    assert "test('bug1: adds'" in out
    assert out.count("require('node:test')") == 1
    assert out.startswith("// Tests written by the BugBait Fixer")


def test_enclosing_js_function(tmp_path):
    (tmp_path / "calc.js").write_text(
        "export function isAdult(age) {\n  return age >= 18;\n}\n"
        "class P {\n  discount(p) {\n    return p * 2;\n  }\n}\n")
    p = Project(name="js", root=tmp_path, source="local", language=languages.JAVASCRIPT)
    assert engine.enclosing_function({"file": "calc.js", "old": "age >= 18"}, p) == "isAdult"
    assert engine.enclosing_function({"file": "calc.js", "old": "p * 2"}, p) == "P.discount"


def test_fixer_extracts_js_test():
    from agents.fixer import _extract_code

    code, reason = _extract_code("```js\nconst test = require('node:test');\ntest('x', () => {});\n```",
                                 "JavaScript")
    assert reason is None and "test('x'" in code
    _, reason = _extract_code("```js\nconst x = 1;\n```", "JavaScript")
    assert reason == "model did not return a test function"


def test_enclosing_java_function(tmp_path):
    (tmp_path / "Calc.java").write_text(
        "package calc;\npublic class Calc {\n    public static int add(int a, int b) {\n"
        "        return a + b;\n    }\n}\n")
    p = Project(name="j", root=tmp_path, source="local", language=languages.JAVA)
    assert engine.enclosing_function({"file": "Calc.java", "old": "a + b"}, p) == "Calc.add"


def test_build_java_test_file_merges_methods(tmp_path):
    bug1 = {"file": "Calc.java", "old": "a + b", "new": "a - b"}
    code1 = ("package calc;\nimport org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.*;\n"
             "@Test\npublic void adds() {\n    assertEquals(5, Calc.add(2, 3));\n}\n")
    p = Project(name="x", root=tmp_path, source="local", language=languages.JAVA)
    out = engine.build_test_file([(1, bug1, code1)], p)
    assert "package calc;" in out
    assert out.count("import org.junit.jupiter.api.Test;") == 1
    assert "public class BugbaitGeneratedTest" in out
    assert "void bug1_adds()" in out


def test_fixer_extracts_java_test():
    from agents.fixer import _extract_code

    code, reason = _extract_code("```java\nimport org.junit.jupiter.api.Test;\n@Test\nvoid x() {}\n```", "Java")
    assert reason is None and "@Test" in code
    _, reason = _extract_code("```java\nclass X {}\n```", "Java")
    assert reason == "model did not return a test function"


def test_enclosing_go_function(tmp_path):
    (tmp_path / "calc.go").write_text(
        "package calc\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n\n"
        "func (c *Calc) total() int {\n\treturn c.n * 2\n}\n")
    p = Project(name="g", root=tmp_path, source="local", language=languages.GO)
    assert engine.enclosing_function({"file": "calc.go", "old": "a + b"}, p) == "Add"
    assert engine.enclosing_function({"file": "calc.go", "old": "c.n * 2"}, p) == "Calc.total"


def test_build_go_test_file_merges_and_renames(tmp_path):
    (tmp_path / "calc_test.go").write_text("package calc\n")
    bug1 = {"file": "calc.go", "old": "a + b", "new": "a - b"}
    bug2 = {"file": "calc.go", "old": "age >= 18", "new": "age > 18"}
    code1 = ('package calc\n\nimport "testing"\n\n'
             'func TestBugbaitCheck(t *testing.T) {\n\tif Add(1, 1) != 2 {\n\t\tt.Fail()\n\t}\n}\n')
    code2 = ('package calc\n\nimport (\n\t"testing"\n)\n\n'
             'func TestBugbaitCheck(t *testing.T) {\n\tif !IsAdult(18) {\n\t\tt.Fail()\n\t}\n}\n')
    p = Project(name="g", root=tmp_path, source="local", language=languages.GO)
    out = engine.build_test_file([(1, bug1, code1), (2, bug2, code2)], p)
    assert "package calc" in out
    assert out.count('"testing"') == 1
    assert "func TestBug1BugbaitCheck(t *testing.T)" in out
    assert "func TestBug2BugbaitCheck(t *testing.T)" in out
    assert "Catches bug 1" in out and "Catches bug 2" in out


def test_fixer_extracts_go_test():
    from agents.fixer import _extract_code

    code, reason = _extract_code("```go\npackage calc\nfunc TestBugbaitCheck(t *testing.T) {}\n```", "Go")
    assert reason is None and "func TestBugbaitCheck" in code
    _, reason = _extract_code("```go\npackage calc\nfunc helper() {}\n```", "Go")
    assert reason == "model did not return a test function"


def test_enclosing_ts_function(tmp_path):
    (tmp_path / "calc.ts").write_text(
        "export function isAdult(age: number): boolean {\n  return age >= 18;\n}\n"
        "export function first<T>(items: T[]): T {\n  return items[0];\n}\n")
    p = Project(name="ts", root=tmp_path, source="local", language=languages.TYPESCRIPT)
    assert engine.enclosing_function({"file": "calc.ts", "old": "age >= 18"}, p) == "isAdult"
    assert engine.enclosing_function({"file": "calc.ts", "old": "items[0]"}, p) == "first"


def test_build_ts_test_file_keeps_types(tmp_path):
    bug1 = {"file": "calc.ts", "old": "a + b", "new": "a - b"}
    code1 = ("import test from 'node:test';\nimport assert from 'node:assert';\n"
             "import { add } from './calc.ts';\n"
             "test('adds', () => assert.equal(add(1, 1), 2));\n")
    p = Project(name="ts", root=tmp_path, source="local", language=languages.TYPESCRIPT)
    out = engine.build_test_file([(1, bug1, code1)], p)
    assert "test('bug1: adds'" in out
    assert "import { add } from './calc.ts';" in out


def test_fixer_extracts_ts_test():
    from agents.fixer import _extract_code

    code, reason = _extract_code("```ts\nimport test from 'node:test';\ntest('x', () => {});\n```",
                                 "TypeScript")
    assert reason is None and "test('x'" in code
    _, reason = _extract_code("```ts\nconst x: number = 1;\n```", "TypeScript")
    assert reason == "model did not return a test function"


def test_enclosing_rust_function(tmp_path):
    (tmp_path / "lib.rs").write_text(
        "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n\n"
        "pub struct Calc;\nimpl Calc {\n"
        "    pub fn double(&self, n: i32) -> i32 {\n        n * 2\n    }\n}\n\n"
        "pub fn free_after(x: i32) -> i32 {\n    x + 1\n}\n\n"
        "impl<T> Wrapper<T> {\n    pub fn get(&self) -> &T {\n        &self.v\n    }\n}\n")
    p = Project(name="r", root=tmp_path, source="local", language=languages.RUST)
    assert engine.enclosing_function({"file": "lib.rs", "old": "a + b"}, p) == "add"
    assert engine.enclosing_function({"file": "lib.rs", "old": "n * 2"}, p) == "Calc.double"
    assert engine.enclosing_function({"file": "lib.rs", "old": "x + 1"}, p) == "free_after"
    assert engine.enclosing_function({"file": "lib.rs", "old": "&self.v"}, p) == "Wrapper.get"


def test_build_rust_test_file_merges_and_renames(tmp_path):
    bug1 = {"file": "src/lib.rs", "old": "a + b", "new": "a - b"}
    bug2 = {"file": "src/lib.rs", "old": "age >= 18", "new": "age > 18"}
    code1 = ("use rustcalc::add;\n\n#[test]\nfn checks_add() {\n"
             "    assert_eq!(add(1, 1), 2);\n}\n")
    code2 = ("use rustcalc::{add, is_adult};\n\n#[test]\nfn checks_adult() {\n"
             "    assert!(is_adult(18));\n}\n")
    p = Project(name="r", root=tmp_path, source="local", language=languages.RUST)
    out = engine.build_test_file([(1, bug1, code1), (2, bug2, code2)], p)
    assert out.count("use rustcalc::add;") == 1
    assert "use rustcalc::{add, is_adult};" in out
    assert "fn bug1_checks_add() {" in out
    assert "fn bug2_checks_adult() {" in out
    assert "Catches bug 1" in out and "Catches bug 2" in out


def test_fixer_extracts_rust_test():
    from agents.fixer import _extract_code

    code, reason = _extract_code("```rust\nuse c::add;\n#[test]\nfn x() {}\n```", "Rust")
    assert reason is None and "#[test]" in code
    _, reason = _extract_code("```rust\nfn helper() {}\n```", "Rust")
    assert reason == "model did not return a test function"


def test_saboteur_uses_language_fence(monkeypatch):
    import agents.saboteur as sab

    seen = {}

    def fake_chat(messages, agent, **kw):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return '[{"old": "a", "new": "b"}]'

    monkeypatch.setattr(sab, "chat", fake_chat)
    sab.generate_bugs("c.js", "a", "t", 1, language="JavaScript")
    assert "valid JavaScript" in seen["system"] and "```js" in seen["user"]
    sab.generate_bugs("C.java", "a", "t", 1, language="Java")
    assert "valid Java" in seen["system"] and "```java" in seen["user"]
    sab.generate_bugs("c.py", "a", "t", 1)
    assert "valid Python" in seen["system"] and "```python" in seen["user"]
    sab.generate_bugs("c.go", "a", "t", 1, language="Go")
    assert "valid Go" in seen["system"] and "```go" in seen["user"]
    sab.generate_bugs("c.ts", "a", "t", 1, language="TypeScript")
    assert "valid TypeScript" in seen["system"] and "```ts" in seen["user"]
    sab.generate_bugs("l.rs", "a", "t", 1, language="Rust")
    assert "valid Rust" in seen["system"] and "```rust" in seen["user"]


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
