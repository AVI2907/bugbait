"""Tests for language plugins (detection, file patterns, syntax checks)."""

import shutil
from pathlib import Path

import pytest

import languages
from projects import Project, resolve

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")
needs_cargo = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
needs_rustfmt = pytest.mark.skipif(shutil.which("rustfmt") is None, reason="rustfmt not installed")
needs_ruby = pytest.mark.skipif(shutil.which("ruby") is None, reason="ruby not installed")
needs_rspec = pytest.mark.skipif(shutil.which("rspec") is None, reason="rspec not installed")
needs_php = pytest.mark.skipif(shutil.which("php") is None, reason="php not installed")
needs_phpunit = pytest.mark.skipif(
    shutil.which("phpunit") is None and shutil.which("phpunit.phar") is None,
    reason="phpunit not installed")


def _dotnet_runs():
    import socket

    if shutil.which("dotnet") is None:
        return False
    try:  # vstest needs loopback sockets, which some sandboxes deny
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.close()
        return True
    except OSError:
        return False


needs_dotnet_runs = pytest.mark.skipif(not _dotnet_runs(), reason="dotnet test needs sockets")


def _kotlinc_works():
    import subprocess

    if shutil.which("kotlinc") is None or shutil.which("java") is None:
        return False
    try:  # some systems ship a java stub that can't run anything
        return subprocess.run(["java", "-version"], capture_output=True, timeout=60).returncode == 0
    except OSError:
        return False


needs_kotlinc = pytest.mark.skipif(not _kotlinc_works(), reason="kotlinc and java not installed")


def write_phpunit_project(path, extra=""):
    (path / "calc.php").write_text("<?php\n\nfunction add($a, $b) {\n    return $a + $b;\n}\n")
    (path / "tests").mkdir(exist_ok=True)
    (path / "tests" / "CalcTest.php").write_text(
        "<?php\n\nuse PHPUnit\\Framework\\TestCase;\n\nrequire_once __DIR__ . \"/../calc.php\";\n\n"
        "final class CalcTest extends TestCase {\n"
        "    public function testAdd(): void {\n        $this->assertSame(3, add(1, 2));\n    }\n"
        + extra + "}\n")


def _minitest_available():
    import subprocess

    try:
        return subprocess.run(["ruby", "-e", "require 'minitest/autorun'"],
                              capture_output=True, timeout=60).returncode == 0
    except OSError:
        return False


needs_minitest = pytest.mark.skipif(not _minitest_available(), reason="minitest not installed")


def write_minitest_project(path, extra=""):
    (path / "calc.rb").write_text("def add(a, b)\n  a + b\nend\n")
    (path / "test").mkdir(exist_ok=True)
    (path / "test" / "test_add.rb").write_text(
        'require "minitest/autorun"\nrequire_relative "../calc"\n\n'
        "class TestAdd < Minitest::Test\n"
        "  def test_add\n    assert_equal 3, add(1, 2)\n  end\n" + extra + "end\n")


def write_rspec_project(path, extra=""):
    (path / "calc.rb").write_text("def add(a, b)\n  a + b\nend\n")
    (path / "spec").mkdir(exist_ok=True)
    (path / "spec" / "calc_spec.rb").write_text(
        'require_relative "../calc"\n\nRSpec.describe "calc" do\n'
        '  it "adds" do\n    expect(add(1, 2)).to eq(3)\n  end\n' + extra + "end\n")


def write_rust_crate(path, extra_test=""):
    (path / "Cargo.toml").write_text('[package]\nname = "rsproj"\nversion = "0.1.0"\nedition = "2021"\n')
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "lib.rs").write_text(
        "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n\n"
        "#[cfg(test)]\nmod tests {\n    use super::*;\n\n"
        "    #[test]\n    fn adds() {\n        assert_eq!(add(1, 2), 3);\n    }\n" + extra_test + "}\n")


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


def test_detect_rust_project(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    (tmp_path / "lib.rs").write_text("pub fn f() {}\n")
    assert languages.detect(tmp_path).id == "rust"


def test_detect_ruby_project(tmp_path):
    (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\n')
    (tmp_path / "calc.rb").write_text("def add(a, b)\n  a + b\nend\n")
    assert languages.detect(tmp_path).id == "ruby"


def test_detect_php_project(tmp_path):
    (tmp_path / "composer.json").write_text('{"name": "x/y"}\n')
    (tmp_path / "calc.php").write_text("<?php\nfunction add($a, $b) {\n  return $a + $b;\n}\n")
    assert languages.detect(tmp_path).id == "php"


def test_detect_csharp_project(tmp_path):
    (tmp_path / "app.sln").write_text("sln\n")
    (tmp_path / "Calc.cs").write_text("public static class Calc {}\n")
    assert languages.detect(tmp_path).id == "csharp"


def test_detect_kotlin_project(tmp_path):
    (tmp_path / "Calc.kt").write_text("object Calc {}\n")
    assert languages.detect(tmp_path).id == "kotlin"


def test_detect_kotlin_in_gradle_stays_java(tmp_path):
    (tmp_path / "build.gradle.kts").write_text("plugins {}\n")
    (tmp_path / "Main.kt").write_text("fun main() {}\n")
    assert languages.detect(tmp_path).id == "java"


def test_detect_typescript_project(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}\n")
    (tmp_path / "calc.ts").write_text("export const add = (a: number, b: number): number => a + b;\n")
    (tmp_path / "calc.test.ts").write_text("import test from 'node:test';\n")
    assert languages.detect(tmp_path).id == "typescript"


def test_detect_ts_majority_beats_js(tmp_path):
    (tmp_path / "a.ts").write_text("export const x: number = 1;\n")
    (tmp_path / "b.ts").write_text("export const y: number = 2;\n")
    (tmp_path / "c.js").write_text("export const z = 3;\n")
    assert languages.detect(tmp_path).id == "typescript"


def test_detect_tsx_counts_as_typescript(tmp_path):
    (tmp_path / "App.tsx").write_text("export const App = () => null;\n")
    assert languages.detect(tmp_path).id == "typescript"


def test_detect_java_and_jvm_projects(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
    assert languages.detect(tmp_path).id == "java"


def test_detect_kotlin_java_mix_stays_java(tmp_path):
    # Standalone .kt is now Kotlin (see test_detect_kotlin_project); mixed with
    # Java sources it still flows to the Java runner.
    (tmp_path / "App.kt").write_text("fun main() {}\n")
    (tmp_path / "Main.java").write_text("class Main {}\n")
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


def test_typescript_test_files():
    ts = languages.TYPESCRIPT
    assert ts.is_test_file(Path("calc.test.ts"))
    assert ts.is_test_file(Path("calc.spec.ts"))
    assert ts.is_test_file(Path("__tests__/calc.ts"))
    assert not ts.is_test_file(Path("calc.ts"))


def test_typescript_d_ts_not_source():
    ts = languages.TYPESCRIPT
    assert not ts.is_source_file(Path("calc.d.ts"))
    assert ts.is_source_file(Path("calc.ts"))


def test_typescript_project_discovers_ts_files(tmp_path):
    (tmp_path / "calc.ts").write_text(
        "export const add = (a: number, b: number): number => a + b;\n"
        "export const sub = (a: number, b: number): number => a - b;\n")
    (tmp_path / "calc.d.ts").write_text("export declare const add: (a: number, b: number) => number;\n")
    (tmp_path / "calc.test.ts").write_text("import test from 'node:test';\n")
    p = Project(name="ts", root=tmp_path, source="local", language=languages.TYPESCRIPT)
    assert [f["path"] for f in p.source_files()] == ["calc.ts"]
    assert [t.as_posix() for t in p.test_files()] == ["calc.test.ts"]


def test_node_strips_types_versions(tmp_path):
    from projects import _node_strips_types

    bindir = tmp_path / "bin"
    bindir.mkdir()
    node = bindir / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    cases = [("v20.0.0", False), ("v22.17.9", False), ("v22.18.0", True),
             ("v23.5.0", False), ("v23.6.0", True), ("v24.1.0", True)]
    for version, expected in cases:
        node.write_text(f"#!/bin/sh\necho {version}\n")
        assert _node_strips_types(str(node)) is expected, version


@needs_node
def test_typescript_baseline_counts_passes(tmp_path):
    import engine

    (tmp_path / "calc.ts").write_text("export const add = (a: number, b: number): number => a + b;\n")
    (tmp_path / "calc.test.ts").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert';\n"
        "import { add } from './calc.ts';\ntest('add', () => assert.equal(add(2, 3), 5));\n")
    p = Project(name="ts", root=tmp_path, source="local", language=languages.TYPESCRIPT)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


@needs_node
def test_typescript_sandbox_run(tmp_path):
    from runner import Sandbox

    (tmp_path / "calc.ts").write_text("export const add = (a: number, b: number): number => a + b;\n")
    (tmp_path / "calc.test.ts").write_text(
        "import test from 'node:test';\nimport assert from 'node:assert';\n"
        "import { add } from './calc.ts';\ntest('add', () => assert.equal(add(2, 3), 5));\n")
    p = Project(name="ts", root=tmp_path, source="local", language=languages.TYPESCRIPT)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("calc.ts", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


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


def test_rust_test_files():
    rust = languages.RUST
    assert rust.is_test_file(Path("tests/api.rs"))
    assert rust.is_test_file(Path("benches/speed.rs"))
    assert not rust.is_test_file(Path("src/lib.rs"))


def test_parse_cargo_results():
    from runner import parse_cargo_results

    output = (
        "running 3 tests\ntest tests::adds ... ok\ntest tests::broken ... FAILED\n"
        "failures:\n    tests::broken\n\n"
        "test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out\n"
        "   Doc-tests rsproj\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured\n")
    assert parse_cargo_results(output) == (4, 1, ["tests::broken"])
    assert parse_cargo_results("error: could not compile\n") == (0, 0, [])


@needs_rustfmt
def test_rust_syntax_check():
    assert languages.RUST.syntax_ok("pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n")
    assert not languages.RUST.syntax_ok("pub fn add( {\n")


def test_rust_project_discovers_rs_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "api.rs").write_text("#[test]\nfn x() {}\n")
    p = Project(name="r", root=tmp_path, source="local", language=languages.RUST)
    assert [f["path"] for f in p.source_files()] == ["src/lib.rs"]
    assert [t.as_posix() for t in p.test_files()] == ["tests/api.rs"]
    assert p.test_dir() == "tests"


@needs_cargo
def test_rust_baseline_counts_passes(tmp_path):
    import engine

    write_rust_crate(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    assert p.language.id == "rust"
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


@needs_cargo
def test_rust_baseline_sets_aside_failing(tmp_path):
    import engine

    write_rust_crate(tmp_path, "\n    #[test]\n    fn broken() {\n        panic!(\"boom\");\n    }\n")
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1
    assert p.skipped_rust_tests == ["tests::broken"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_cargo
def test_rust_sandbox_run(tmp_path):
    from runner import Sandbox

    write_rust_crate(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("src/lib.rs", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_cargo
def test_rust_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    write_rust_crate(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    bug = {"file": "src/lib.rs", "old": "a + b", "new": "a - b"}
    good = ('use rsproj::add;\n\n#[test]\nfn checks_add() {\n    assert_eq!(add(1, 1), 2);\n}\n')
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("add(1, 1), 2", "add(0, 0), 0")  # 0 passes under both + and -
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem


def test_ruby_test_files():
    rb = languages.RUBY
    assert rb.is_test_file(Path("test_add.rb"))
    assert rb.is_test_file(Path("add_test.rb"))
    assert rb.is_test_file(Path("calc_spec.rb"))
    assert rb.is_test_file(Path("test/add.rb"))
    assert rb.is_test_file(Path("spec/add.rb"))
    assert not rb.is_test_file(Path("calc.rb"))


def test_detect_framework():
    rb = languages.RUBY
    assert rb.detect_framework([Path("spec/a_spec.rb")]) == "rspec"
    assert rb.detect_framework([Path("test/test_a.rb")]) == "minitest"
    assert rb.detect_framework([Path("spec/a_spec.rb"), Path("test/test_a.rb")]) == "minitest"
    assert rb.detect_framework([]) == "minitest"


def test_parse_minitest_results():
    from runner import parse_minitest_results

    output = ("TestAdd#test_add = 0.00 s = .\nTestAdd#test_broken = 0.00 s = F\n\n"
              "  1) Failure:\nTestAdd#test_broken [test/test_add.rb:8]:\nExpected: 3\n\n"
              "2 runs, 2 assertions, 1 failures, 0 errors, 0 skips\n")
    assert parse_minitest_results(output) == (2, 1, ["TestAdd#test_broken"], ["TestAdd#test_add"])
    assert parse_minitest_results("cannot load such file\n") == (0, 0, [], [])


def test_parse_rspec_results():
    from runner import parse_rspec_results

    body = ('{"summary": {"example_count": 2, "failure_count": 1}, "examples": ['
            '{"status": "passed", "file_path": "spec/a_spec.rb", "line_number": 4}, '
            '{"status": "failed", "file_path": "spec/a_spec.rb", "line_number": 8}]}')
    assert parse_rspec_results("warning: x\n" + body + "\nCoverage done\n") == (2, 1, ["spec/a_spec.rb:4"])
    assert parse_rspec_results("An error occurred\n") == (0, 0, [])


@needs_ruby
def test_ruby_syntax_check():
    assert languages.RUBY.syntax_ok("def add(a, b)\n  a + b\nend\n")
    assert not languages.RUBY.syntax_ok("def add(\n")


def test_ruby_project_discovers_rb_files(tmp_path):
    (tmp_path / "calc.rb").write_text("def add(a, b)\n  a + b\nend\n\ndef adult?(age)\n  age >= 18\nend\n")
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "test_add.rb").write_text('require "minitest/autorun"\n')
    p = Project(name="rb", root=tmp_path, source="local", language=languages.RUBY)
    assert [f["path"] for f in p.source_files()] == ["calc.rb"]
    assert [t.as_posix() for t in p.test_files()] == ["test/test_add.rb"]


@needs_minitest
def test_minitest_baseline_counts_passes(tmp_path):
    import engine

    write_minitest_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    assert (p.language.id, p.ruby_framework) == ("ruby", "minitest")
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


@needs_minitest
def test_minitest_baseline_sets_aside_failing(tmp_path):
    import engine

    write_minitest_project(tmp_path, "  def test_broken\n    assert_equal 3, add(1, 1)\n  end\n")
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1
    assert p.included_minitest_tests == ["TestAdd#test_add"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_minitest
def test_minitest_sandbox_run(tmp_path):
    from runner import Sandbox

    write_minitest_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("calc.rb", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_minitest
def test_minitest_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    write_minitest_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    bug = {"file": "calc.rb", "old": "a + b", "new": "a - b"}
    good = ('require "minitest/autorun"\nrequire_relative "../calc"\n\n'
            "class TestCheck < Minitest::Test\n"
            "  def test_check\n    assert_equal 2, add(1, 1)\n  end\nend\n")
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("assert_equal 2, add(1, 1)", "assert_equal 0, add(0, 0)")
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem


@needs_rspec
def test_rspec_baseline_counts_passes(tmp_path):
    import engine

    write_rspec_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    assert (p.language.id, p.ruby_framework) == ("ruby", "rspec")
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


@needs_rspec
def test_rspec_baseline_sets_aside_failing(tmp_path):
    import engine

    write_rspec_project(tmp_path, '  it "is broken" do\n    expect(add(1, 1)).to eq(3)\n  end\n')
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1
    assert p.rspec_locations == ["spec/calc_spec.rb:4"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_rspec
def test_rspec_sandbox_run(tmp_path):
    from runner import Sandbox

    write_rspec_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("calc.rb", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_rspec
def test_rspec_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    write_rspec_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    bug = {"file": "calc.rb", "old": "a + b", "new": "a - b"}
    good = ('require_relative "../calc"\n\nRSpec.describe "check" do\n'
            '  it "adds" do\n    expect(add(1, 1)).to eq(2)\n  end\nend\n')
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("expect(add(1, 1)).to eq(2)", "expect(add(0, 0)).to eq(0)")
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem


def test_php_test_files():
    php = languages.PHP
    assert php.is_test_file(Path("CalcTest.php"))
    assert php.is_test_file(Path("calc_test.php"))
    assert php.is_test_file(Path("tests/Calc.php"))
    assert not php.is_test_file(Path("calc.php"))


def test_parse_phpunit_results():
    from runner import parse_phpunit_results

    failing = ("\n1) CalcTest::testBroken\nFailed asserting that 2 is identical to 3.\n\n"
               "FAILURES!\nTests: 2, Assertions: 2, Failures: 1.\n")
    assert parse_phpunit_results(failing) == (2, 1, ["CalcTest::testBroken"])
    assert parse_phpunit_results("OK (3 tests, 5 assertions)\n") == (3, 0, [])
    assert parse_phpunit_results("OK (1 test, 1 assertion)\n") == (1, 0, [])
    assert parse_phpunit_results("Tests: 2, Assertions: 1, Errors: 2.\n") == (2, 2, [])
    assert parse_phpunit_results("No tests executed!\n") == (0, 0, [])


def test_parse_phpunit_roster():
    from runner import parse_phpunit_roster

    output = "Available tests:\n - CalcTest::testAdd\n - CalcTest::testBroken\n"
    assert parse_phpunit_roster(output) == ["CalcTest::testAdd", "CalcTest::testBroken"]


def test_find_phpunit_prefers_project_copy(tmp_path, monkeypatch):
    from projects import _find_phpunit

    vendor = tmp_path / "vendor" / "bin"
    vendor.mkdir(parents=True)
    (vendor / "phpunit").write_text("#!/bin/sh\n")
    assert _find_phpunit(tmp_path) == [str(vendor / "phpunit")]
    (vendor / "phpunit").unlink()
    (tmp_path / "phpunit.phar").write_text("phar")
    assert _find_phpunit(tmp_path) == ["php", str(tmp_path / "phpunit.phar")]
    (tmp_path / "phpunit.phar").unlink()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "phpunit").write_text("#!/bin/sh\n")
    (bindir / "phpunit").chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert _find_phpunit(tmp_path) == [str(bindir / "phpunit")]


@needs_php
def test_php_syntax_check():
    assert languages.PHP.syntax_ok("<?php\nfunction add($a, $b) {\n    return $a + $b;\n}\n")
    assert not languages.PHP.syntax_ok("<?php\nfunction add(\n")


def test_php_project_discovers_php_files(tmp_path):
    (tmp_path / "calc.php").write_text("<?php\nfunction add($a, $b) {\n    return $a + $b;\n}\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "CalcTest.php").write_text("<?php\nclass CalcTest {}\n")
    p = Project(name="ph", root=tmp_path, source="local", language=languages.PHP)
    assert [f["path"] for f in p.source_files()] == ["calc.php"]
    assert [t.as_posix() for t in p.test_files()] == ["tests/CalcTest.php"]


@needs_phpunit
def test_phpunit_baseline_counts_passes(tmp_path):
    import engine

    write_phpunit_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    assert p.language.id == "php"
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1


@needs_phpunit
def test_phpunit_baseline_sets_aside_failing(tmp_path):
    import engine

    write_phpunit_project(
        tmp_path, "    public function testBroken(): void {\n"
                  "        $this->assertSame(3, add(1, 1));\n    }\n")
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 1
    assert p.included_phpunit_tests == ["CalcTest::testAdd"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_phpunit
def test_phpunit_sandbox_run(tmp_path):
    from runner import Sandbox

    write_phpunit_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("calc.php", "$a + $b", "$a - $b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_phpunit
def test_phpunit_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    write_phpunit_project(tmp_path)
    p = resolve(str(tmp_path), log=lambda m: None)
    bug = {"file": "calc.php", "old": "$a + $b", "new": "$a - $b"}
    good = ("<?php\n\nuse PHPUnit\\Framework\\TestCase;\n\nrequire_once __DIR__ . \"/../calc.php\";\n\n"
            "final class BugbaitCheckTest extends TestCase {\n"
            "    public function testCheck(): void {\n"
            "        $this->assertSame(2, add(1, 1));\n    }\n}\n")
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("assertSame(2, add(1, 1))", "assertSame(0, add(0, 0))")
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem


def _copy_csharp_demo(path):
    import shutil as _shutil

    demo = Path(__file__).resolve().parent.parent / "targets" / "csharpcalc"
    for src in [demo / "csharpcalc.sln",
                demo / "src" / "CalcLib" / "CalcLib.csproj",
                demo / "src" / "CalcLib" / "Calc.cs",
                demo / "tests" / "CalcLib.Tests" / "CalcLib.Tests.csproj",
                demo / "tests" / "CalcLib.Tests" / "CalcTests.cs"]:
        dest = path / src.relative_to(demo)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _shutil.copy(src, dest)
    return path


def test_csharp_test_files():
    cs = languages.CSHARP
    assert cs.is_test_file(Path("CalcTests.cs"))
    assert cs.is_test_file(Path("CalcTest.cs"))
    assert cs.is_test_file(Path("tests/Calc.cs"))
    assert not cs.is_test_file(Path("Calc.cs"))


def test_dotnet_target_selection(tmp_path):
    from projects import _dotnet_target

    assert _dotnet_target(tmp_path) == ""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Lib.csproj").write_text("<Project/>\n")
    assert _dotnet_target(tmp_path) == "src/Lib.csproj"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "Lib.Tests.csproj").write_text("<Project/>\n")
    assert _dotnet_target(tmp_path) == "tests/Lib.Tests.csproj"
    (tmp_path / "app.sln").write_text("sln\n")
    assert _dotnet_target(tmp_path) == "app.sln"


def test_parse_dotnet_results():
    from runner import parse_dotnet_results

    failing = ("  Failed CalcLib.Tests.CalcTests.Broken [4 ms]\n"
               "Failed!  - Failed:     1, Passed:     3, Skipped:     0, Total:     4, Duration: 5 ms\n")
    assert parse_dotnet_results(failing) == (4, 1, ["CalcLib.Tests.CalcTests.Broken"])
    assert parse_dotnet_results("Passed!  - Failed: 0, Passed: 4, Skipped: 0, Total: 4\n") == (4, 0, [])
    assert parse_dotnet_results("Build FAILED.\n") == (0, 0, [])


@needs_dotnet_runs
def test_dotnet_baseline_counts_passes(tmp_path):
    import engine

    p = resolve(str(_copy_csharp_demo(tmp_path)), log=lambda m: None)
    assert p.language.id == "csharp" and p.dotnet_target == "csharpcalc.sln"
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 3


@needs_dotnet_runs
def test_dotnet_baseline_sets_aside_failing(tmp_path):
    import engine

    _copy_csharp_demo(tmp_path)
    broken = tmp_path / "tests" / "CalcLib.Tests" / "CalcTests.cs"
    text = broken.read_text().rstrip()
    assert text.endswith("}")
    broken.write_text(text[:-1] + "    [Fact]\n"
                      "    public void Broken() => Assert.Equal(3, Calc.Add(1, 1));\n}\n")
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 3
    assert p.excluded_dotnet_tests == ["CalcLib.Tests.CalcTests.Broken"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_dotnet_runs
def test_dotnet_sandbox_run(tmp_path):
    from runner import Sandbox

    p = resolve(str(_copy_csharp_demo(tmp_path)), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("src/CalcLib/Calc.cs", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_dotnet_runs
def test_dotnet_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    p = resolve(str(_copy_csharp_demo(tmp_path)), log=lambda m: None)
    bug = {"file": "src/CalcLib/Calc.cs", "old": "a + b", "new": "a - b"}
    good = ("using CalcLib;\n\nnamespace CalcLib.Tests;\n\npublic class BugbaitCheck\n{\n"
            "    [Fact]\n    public void Check() => Assert.Equal(2, Calc.Add(1, 1));\n}\n")
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("Assert.Equal(2, Calc.Add(1, 1))", "Assert.Equal(0, Calc.Add(0, 0))")
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem


def _copy_kotlin_demo(path):
    import shutil as _shutil

    demo = Path(__file__).resolve().parent.parent / "targets" / "kotlincalc"
    for src in [demo / "Calc.kt", demo / "tests" / "CalcTest.kt"]:
        dest = path / src.relative_to(demo)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _shutil.copy(src, dest)
    return path


def test_kotlin_test_files():
    kt = languages.KOTLIN
    assert kt.is_test_file(Path("CalcTest.kt"))
    assert kt.is_test_file(Path("CalcTests.kt"))
    assert kt.is_test_file(Path("tests/Calc.kt"))
    assert not kt.is_test_file(Path("Calc.kt"))


def test_parse_junit_results():
    from runner import parse_junit_results

    failing = ("1) addsNumbers(CalcTest)\n"
               "Tests run: 3,  Failures: 1\n")
    assert parse_junit_results(failing) == (3, 1, ["CalcTest"])
    assert parse_junit_results("OK (3 tests)\n") == (3, 0, [])
    assert parse_junit_results("error: unresolved reference\n") == (0, 0, [])


def test_kotlin_test_class(tmp_path):
    from runner import kotlin_test_class

    f = tmp_path / "CalcTest.kt"
    f.write_text("package calc\n\nimport org.junit.Test\n\ninternal class CalcTest {\n}\n")
    assert kotlin_test_class(f) == "calc.CalcTest"
    f.write_text("object Calc {\n}\n")
    assert kotlin_test_class(f) is None


@needs_kotlinc
def test_kotlin_baseline_counts_passes(tmp_path):
    import engine

    p = resolve(str(_copy_kotlin_demo(tmp_path)), log=lambda m: None)
    assert p.language.id == "kotlin"
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 3


@needs_kotlinc
def test_kotlin_baseline_sets_aside_failing(tmp_path):
    import engine

    _copy_kotlin_demo(tmp_path)
    (tmp_path / "tests" / "BrokenTest.kt").write_text(
        "import org.junit.Test\nimport org.junit.Assert.*\n\nclass BrokenTest {\n"
        "    @Test\n    fun broken() {\n        assertEquals(3, Calc.add(1, 1))\n    }\n}\n")
    p = resolve(str(tmp_path), log=lambda m: None)
    events = []
    assert engine.baseline(p, lambda e, **d: events.append((e, d))) == 3
    assert p.excluded_kotlin_classes == ["BrokenTest"]
    assert any(e == "setup" and "set them aside" in d["message"] for e, d in events)


@needs_kotlinc
def test_kotlin_sandbox_run(tmp_path):
    from runner import Sandbox

    p = resolve(str(_copy_kotlin_demo(tmp_path)), log=lambda m: None)
    with Sandbox(p) as sb:
        passed, _ = sb.run_tests(stop_early=False)
        assert passed
    with Sandbox(p) as sb:
        sb.apply_mutation("Calc.kt", "a + b", "a - b")
        passed, _ = sb.run_tests()
        assert not passed


@needs_kotlinc
def test_kotlin_fixer_verify(tmp_path):
    from agents.fixer import verify_test

    p = resolve(str(_copy_kotlin_demo(tmp_path)), log=lambda m: None)
    bug = {"file": "Calc.kt", "old": "a + b", "new": "a - b"}
    good = ("import org.junit.Test\nimport org.junit.Assert.*\n\nclass BugbaitCheck {\n"
            "    @Test\n    fun checkAdd() {\n        assertEquals(4, Calc.add(2, 2))\n    }\n}\n")
    assert verify_test(p, bug, good) == (True, None)
    weak = good.replace("assertEquals(4, Calc.add(2, 2))", "assertEquals(0, Calc.add(0, 0))")
    ok, problem = verify_test(p, bug, weak)
    assert ok is False and "BUGGY" in problem
