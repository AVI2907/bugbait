"""Tests for language plugins (detection, file patterns, syntax checks)."""

import shutil
from pathlib import Path

import pytest

import languages
from projects import Project, resolve

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_detect_python_example():
    assert resolve("calculator", log=lambda m: None).language.id == "python"


def test_detect_javascript_project(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}\n')
    (tmp_path / "calc.js").write_text("export const add = (a, b) => a + b;\n")
    assert languages.detect(tmp_path).id == "javascript"


def test_detect_empty_defaults_to_python(tmp_path):
    assert languages.detect(tmp_path).id == "python"


def test_python_test_files():
    py = languages.PYTHON
    assert py.is_test_file(Path("test_x.py"))
    assert py.is_test_file(Path("x_test.py"))
    assert py.is_test_file(Path("tests/x.py"))
    assert not py.is_test_file(Path("x.py"))


def test_javascript_test_files():
    js = languages.JAVASCRIPT
    assert js.is_test_file(Path("calc.test.js"))
    assert js.is_test_file(Path("calc.spec.js"))
    assert js.is_test_file(Path("__tests__/calc.js"))
    assert js.is_test_file(Path("test/calc.js"))
    assert not js.is_test_file(Path("calc.js"))


def test_python_syntax_check():
    assert languages.PYTHON.syntax_ok("def f():\n    return 1\n")
    assert not languages.PYTHON.syntax_ok("def f(:\n")


@needs_node
def test_javascript_syntax_check():
    assert languages.JAVASCRIPT.syntax_ok("const add = (a, b) => a + b;\n")
    assert not languages.JAVASCRIPT.syntax_ok("const add = ( => ;\n")


def test_javascript_project_discovers_js_files(tmp_path):
    (tmp_path / "calc.js").write_text("export const add = (a, b) => a + b;\nexport const sub = (a, b) => a - b;\n")
    (tmp_path / "calc.test.js").write_text("import test from 'node:test';\n")
    (tmp_path / "ignore.py").write_text("x = 1\n")
    p = Project(name="js", root=tmp_path, source="local", language=languages.JAVASCRIPT)
    assert [f["path"] for f in p.source_files()] == ["calc.js"]
    assert [t.as_posix() for t in p.test_files()] == ["calc.test.js"]


@needs_node
def test_javascript_sandbox_run(tmp_path):
    from runner import Sandbox

    (tmp_path / "calc.js").write_text("export const add = (a, b) => a + b;\n")
    (tmp_path / "calc.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert';\n"
        "import { add } from './calc.js';\ntest('add', () => assert.equal(add(2, 3), 5));\n")
    p = Project(name="js", root=tmp_path, source="local", language=languages.JAVASCRIPT)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("calc.js", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed
