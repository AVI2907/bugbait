"""Tests for language plugins (detection, file patterns, syntax checks)."""

import shutil
from pathlib import Path

import pytest

import languages
from projects import Project, resolve

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")


def write_go_module(path, extra_test=""):
    (path / "go.mod").write_text("module goproj\n\ngo 1.21\n")
    (path / "calc.go").write_text(
        "package goproj\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n")
    (path / "calc_test.go").write_text(
        "package goproj\n\nimport \"testing\"\n\n"
        "func TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fail()\n\t}\n}\n" + extra_test)


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


def test_finish_gradle_project_uses_wrapper(tmp_path, monkeypatch):
    import projects

    (tmp_path / "build.gradle").write_text("// gradle\n")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    monkeypatch.setattr(projects.shutil, "which", lambda c: None)
    p = projects._finish(projects.Project(name="j", root=tmp_path, source="local"), log=lambda m: None)
    assert p.build == "gradle" and p.gradle_cmd == ["./gradlew"] and p.gradle_tasks == ["test"]


def test_finish_gradle_without_runner_fails(tmp_path, monkeypatch):
    import projects

    (tmp_path / "build.gradle").write_text("// gradle\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    monkeypatch.setattr(projects.shutil, "which", lambda c: None)
    with pytest.raises(projects.ProjectError, match="gradlew wrapper"):
        projects._finish(projects.Project(name="j", root=tmp_path, source="local"), log=lambda m: None)


def test_finish_android_requires_sdk(tmp_path, monkeypatch):
    import projects

    (tmp_path / "build.gradle").write_text("id 'com.android.application'\n")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.setattr(projects.Path, "home", lambda: tmp_path)
    with pytest.raises(projects.ProjectError, match="ANDROID_HOME"):
        projects._finish(projects.Project(name="j", root=tmp_path, source="local"), log=lambda m: None)


def test_parse_gradle_results(tmp_path):
    from runner import parse_gradle_results

    d = tmp_path / "build/test-results/test"
    d.mkdir(parents=True)
    (d / "TEST-a.GoodTest.xml").write_text(
        '<testsuite name="a.GoodTest" tests="3" failures="0" errors="0" skipped="0"/>')
    (d / "TEST-a.BadTest.xml").write_text(
        '<testsuite name="a.BadTest" tests="2" failures="1" errors="1" skipped="0"/>')
    (d / "TEST-broken.xml").write_text("not xml {")
    total, failed, failed_c, passed_c = parse_gradle_results(tmp_path)
    assert (total, failed, failed_c, passed_c) == (5, 2, ["BadTest"], ["GoodTest"])


def test_gradle_command_construction(tmp_path, monkeypatch):
    from runner import Sandbox

    (tmp_path / "build.gradle").write_text("// gradle\n")
    p = Project(name="j", root=tmp_path, source="local", language=languages.JAVA,
                build="gradle", gradle_cmd=["./gradlew"], gradle_tasks=["test"])
    seen = []

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr("runner.subprocess.run", lambda cmd, **k: (seen.append(cmd), R())[1])
    with Sandbox(p) as sb:
        sb.run_tests()
        sb.run_tests("src/test/java/calc/CalcTest.java")
    assert seen[0][:3] == ["./gradlew", "test", "--console=plain"]
    assert seen[1][-2:] == ["--tests", "CalcTest"]
    p.included_gradle_tests = ["GoodTest"]
    with Sandbox(p) as sb:
        sb.run_tests()
    assert seen[2][-2:] == ["--tests", "GoodTest"]


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


def test_gradle_baseline_set_aside_flow(tmp_path, monkeypatch):
    import engine
    import runner

    (tmp_path / "build.gradle").write_text("// gradle\n")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    p = Project(name="g", root=tmp_path, source="local", language=languages.JAVA,
                build="gradle", gradle_cmd=["./gradlew"], gradle_tasks=["test"])
    fail_xml = '<testsuite name="a.BadTest" tests="2" failures="1" errors="0" skipped="0"/>'
    ok_xml = '<testsuite name="a.GoodTest" tests="3" failures="0" errors="0" skipped="0"/>'
    calls = {"n": 0}

    def fake_run(self, *a, **k):
        calls["n"] += 1
        d = self.path / "build/test-results/test"
        d.mkdir(parents=True, exist_ok=True)
        if calls["n"] == 1:
            (d / "TEST-a.BadTest.xml").write_text(fail_xml)
            (d / "TEST-a.GoodTest.xml").write_text(ok_xml)
            return False, "FAILED"
        (d / "TEST-a.GoodTest.xml").write_text(ok_xml)
        return True, "OK"

    monkeypatch.setattr(runner.Sandbox, "run_tests", fake_run)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 3
    assert p.included_gradle_tests == ["GoodTest"]
    assert events[-1][0] == "baseline" and events[-1][1]["passed"] is True


def test_gradle_baseline_without_tests_explains(tmp_path, monkeypatch):
    import engine
    import runner

    (tmp_path / "build.gradle").write_text("// gradle\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    p = Project(name="g", root=tmp_path, source="local", language=languages.JAVA,
                build="gradle", gradle_cmd=["./gradlew"], gradle_tasks=["test"])
    monkeypatch.setattr(runner.Sandbox, "run_tests", lambda self, *a, **k: (True, "OK"))
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) is None
    assert "No Java test files" in events[-1][1]["output"]


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


def test_detect_go_project(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    (tmp_path / "calc.go").write_text("package x\n")
    assert languages.detect(tmp_path).id == "go"


def test_go_test_files():
    go = languages.GO
    assert go.is_test_file(Path("calc_test.go"))
    assert go.is_test_file(Path("sub/calc_test.go"))
    assert not go.is_test_file(Path("calc.go"))


def test_parse_go_results():
    from runner import parse_go_results

    output = (
        '{"Action":"run","Package":"p","Test":"TestAdd"}\n'
        '{"Action":"pass","Package":"p","Test":"TestAdd","Elapsed":0}\n'
        '{"Action":"run","Package":"p","Test":"TestSub"}\n'
        '{"Action":"pass","Package":"p","Test":"TestSub/inner","Elapsed":0}\n'
        '{"Action":"pass","Package":"p","Test":"TestSub","Elapsed":0}\n'
        '{"Action":"fail","Package":"p","Test":"TestBroken","Elapsed":0}\n'
        '{"Action":"fail","Package":"p","Elapsed":0.1}\n'
        'not json\n')
    assert parse_go_results(output) == (3, 1, ["TestBroken"], ["TestAdd", "TestSub"])
    assert parse_go_results("garbage\n") == (0, 0, [], [])


def test_go_readable_extracts_payloads():
    import engine

    output = ('{"Action":"output","Package":"p","Output":"calc.go:3: boom\\n"}\n'
              '{"Action":"fail","Package":"p"}\n')
    assert engine._go_readable(output) == "calc.go:3: boom"
    assert engine._go_readable("plain text") == "plain text"


@needs_go
def test_go_syntax_check():
    assert languages.GO.syntax_ok("package p\n\nfunc f() int {\n\treturn 1\n}\n")
    assert not languages.GO.syntax_ok("package p\n\nfunc f( {\n")


def test_go_project_discovers_go_files(tmp_path):
    (tmp_path / "calc.go").write_text("package x\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n")
    (tmp_path / "calc_test.go").write_text("package x\n")
    (tmp_path / "ignore.py").write_text("x = 1\n")
    p = Project(name="g", root=tmp_path, source="local", language=languages.GO)
    assert [f["path"] for f in p.source_files()] == ["calc.go"]
    assert [t.as_posix() for t in p.test_files()] == ["calc_test.go"]


@needs_go
def test_go_baseline_counts_passes(tmp_path):
    import engine

    write_go_module(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    assert p.language.id == "go"
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


@needs_go
def test_go_baseline_sets_aside_failing(tmp_path):
    import engine

    write_go_module(tmp_path, '\nfunc TestBroken(t *testing.T) {\n\tt.Error("boom")\n}\n')
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1
    assert p.included_go_tests == ["TestAdd"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_go
def test_go_sandbox_run(tmp_path):
    from runner import Sandbox

    write_go_module(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("calc.go", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_go
def test_go_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    write_go_module(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    bug = {"file": "calc.go", "old": "a + b", "new": "a - b"}
    good = ('package goproj\n\nimport "testing"\n\n'
            'func TestBugbaitCheck(t *testing.T) {\n\tif Add(1, 1) != 2 {\n\t\tt.Fail()\n\t}\n}\n')
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("Add(1, 1) != 2", "Add(0, 0) != 0")  # 0 passes under both + and -
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem
