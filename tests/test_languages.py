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


def test_detect_java_and_jvm_projects(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    assert languages.detect(tmp_path).id == "java"


def test_detect_kotlin_counts_as_java(tmp_path):
    (tmp_path / "App.kt").write_text("fun main() {}\n")
    assert languages.detect(tmp_path).id == "java"


def test_java_test_files():
    java = languages.JAVA
    assert java.is_test_file(Path("src/test/java/calc/CalcTest.java"))
    assert java.is_test_file(Path("CalcTests.java"))
    assert java.is_test_file(Path("src/test/java/calc/Helper.java"))
    assert not java.is_test_file(Path("src/main/java/calc/Calc.java"))
    assert java.generated_name == "BugbaitGeneratedTest.java"
    assert java.check_name == "BugbaitCheckTest.java"


def test_finish_java_requires_maven(tmp_path, monkeypatch):
    import projects

    (tmp_path / "pom.xml").write_text("<project/>\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    monkeypatch.setattr(projects.shutil, "which", lambda c: None)
    with pytest.raises(projects.ProjectError, match="Maven"):
        projects._finish(projects.Project(name="j", root=tmp_path, source="local"), log=lambda m: None)


def test_finish_java_guides_without_root_pom(tmp_path, monkeypatch):
    import projects

    (tmp_path / "svc-a" / "pom.xml").parent.mkdir(parents=True)
    (tmp_path / "svc-a" / "pom.xml").write_text("<project/>\n")
    (tmp_path / "svc-a" / "Main.java").write_text("class Main {}\n")
    monkeypatch.setattr(projects.shutil, "which", lambda c: "/bin/mvn")
    with pytest.raises(projects.ProjectError, match="svc-a"):
        projects._finish(projects.Project(name="j", root=tmp_path, source="local"), log=lambda m: None)


def test_finish_gradle_project_says_coming_soon(tmp_path, monkeypatch):
    import projects

    (tmp_path / "build.gradle").write_text("// gradle\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    monkeypatch.setattr(projects.shutil, "which", lambda c: "/bin/mvn")
    with pytest.raises(projects.ProjectError, match="Gradle"):
        projects._finish(projects.Project(name="j", root=tmp_path, source="local"), log=lambda m: None)


def test_pom_java_version(tmp_path):
    import projects

    pom = tmp_path / "pom.xml"
    pom.write_text("<project><properties><java.version>21</java.version></properties></project>")
    assert projects._pom_java_version(pom) == 21
    pom.write_text("<project><maven.compiler.release>1.8</maven.compiler.release></project>")
    assert projects._pom_java_version(pom) == 8
    pom.write_text("<project></project>")
    assert projects._pom_java_version(pom) is None


def test_select_jdk_exact_match(tmp_path, monkeypatch):
    import projects

    (tmp_path / "pom.xml").write_text("<project><java.version>21</java.version></project>")
    monkeypatch.setattr(projects, "_available_jdks", lambda: {17: "/jdk17", 21: "/jdk21"})
    p = Project(name="j", root=tmp_path, source="local")
    assert projects.select_jdk(p, log=lambda m: None) == "/jdk21"


def test_select_jdk_falls_back_to_newer_with_warning(tmp_path, monkeypatch):
    import projects

    (tmp_path / "pom.xml").write_text("<project><java.version>21</java.version></project>")
    monkeypatch.setattr(projects, "_available_jdks", lambda: {23: "/jdk23"})
    logs = []
    p = Project(name="j", root=tmp_path, source="local")
    assert projects.select_jdk(p, log=logs.append) == "/jdk23"
    assert any("wants Java 21" in m for m in logs)


def test_select_jdk_missing_raises_install_hint(tmp_path, monkeypatch):
    import projects

    (tmp_path / "pom.xml").write_text("<project><java.version>21</java.version></project>")
    monkeypatch.setattr(projects, "_available_jdks", lambda: {17: "/jdk17"})
    p = Project(name="j", root=tmp_path, source="local")
    with pytest.raises(projects.ProjectError, match="brew install openjdk@21"):
        projects.select_jdk(p, log=lambda m: None)


def test_env_sets_java_home(tmp_path):
    p = Project(name="j", root=tmp_path, source="local", java_home="/jdk21")
    env = p.env(tmp_path)
    assert env["JAVA_HOME"] == "/jdk21"
    assert env["PATH"].startswith("/jdk21/bin")


def test_java_baseline_set_aside_then_passes(tmp_path, monkeypatch):
    import engine
    import runner

    (tmp_path / "pom.xml").write_text("<project/>\n")
    (tmp_path / "A.java").write_text("class A {}\n")
    p = Project(name="j", root=tmp_path, source="local", language=languages.JAVA)
    fail_out = "[ERROR] Tests run: 5, Failures: 0, Errors: 5 <<< FAILURE! -- in com.x.BrokenTest\n"
    ok_out = "[INFO] Tests run: 8, Failures: 0, Errors: 0, Skipped: 0\n"
    calls = {"n": 0}

    def fake_run(self, *a, **k):
        calls["n"] += 1
        return (False, fail_out) if calls["n"] == 1 else (True, ok_out)

    monkeypatch.setattr(runner.Sandbox, "run_tests", fake_run)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 8
    assert p.excluded_java_tests == ["BrokenTest"]
    assert events[-1][0] == "baseline" and events[-1][1]["passed"] is True


def test_maven_command_construction(tmp_path, monkeypatch):
    from runner import Sandbox

    (tmp_path / "pom.xml").write_text("<project/>\n")
    p = Project(name="j", root=tmp_path, source="local", language=languages.JAVA)
    seen = []

    class R:
        returncode = 0
        stdout = "Tests run: 1"
        stderr = ""

    monkeypatch.setattr("runner.subprocess.run", lambda cmd, **k: (seen.append(cmd), R())[1])
    with Sandbox(p) as sb:
        sb.run_tests()
        sb.run_tests("src/test/java/calc/CalcTest.java")
    assert seen[0][:3] == ["mvn", "-B", "test"]
    assert "-Dtest=CalcTest" in seen[1]
    p.excluded_java_tests = ["BrokenTest", "FlakyTest"]
    with Sandbox(p) as sb:
        sb.run_tests()
    assert "-Dtest=!BrokenTest,!FlakyTest" in seen[2]


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
def test_javascript_baseline_counts_passes(tmp_path):
    import engine

    (tmp_path / "calc.js").write_text("export const add = (a, b) => a + b;\n")
    (tmp_path / "calc.test.js").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert';\n"
        "import { add } from './calc.js';\ntest('add', () => assert.equal(add(2, 3), 5));\n")
    p = Project(name="js", root=tmp_path, source="local", language=languages.JAVASCRIPT)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


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
