"""Sandbox runner: copies a project, plants a bug, and runs the project's tests.

Each trial runs in a fresh temporary folder, so the original project is never
modified and nothing is left behind.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from projects import SKIP_DIRS

ROOT = Path(__file__).parent


def _ignore(directory, names):
    return [n for n in names
            if n in SKIP_DIRS or n.endswith((".pyc", ".egg-info")) or n == ".DS_Store"]


def parse_junit_results(output):
    """(total, failed, failed_classes) from JUnitCore output (class-level set-aside)."""
    m = re.search(r"Tests run: (\d+),\s*Failures: (\d+)", output)
    if m:
        total, failed = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"OK \((\d+) tests?\)", output)
        total, failed = (int(m.group(1)), 0) if m else (0, 0)
    classes = {m.group(1) for m in re.finditer(r"^\d+\) \S+\(([\w.]+)\)", output, re.M)}
    return total, failed, sorted(classes)


def kotlin_test_class(path):
    """Fully qualified test class from a .kt file ("pkg.Class" or "Class"), or None."""
    try:
        text = Path(path).read_text()
    except (UnicodeDecodeError, OSError):
        return None
    pkg = re.search(r"(?m)^package\s+([\w.]+)", text)
    cls = re.search(r"(?m)^\s*(?:open\s+|final\s+|abstract\s+|public\s+|internal\s+|private\s+"
                      r"|protected\s+|data\s+|sealed\s+)*class\s+(\w+)", text)
    if not cls:
        return None
    return f"{pkg.group(1)}.{cls.group(1)}" if pkg else cls.group(1)


def parse_dotnet_results(output):
    """(total, failed, failed_tests) from `dotnet test` output (all frameworks summed)."""
    total = failed = 0
    for m in re.finditer(r"(?:Passed!|Failed!)\s+- Failed:\s*(\d+), Passed:\s*(\d+)", output):
        failed += int(m.group(1))
        total += int(m.group(1)) + int(m.group(2))
    names = re.findall(r"^  Failed ([A-Za-z_][\w.]*)\s*\[", output, re.M)
    return total, failed, sorted(set(names))


def parse_phpunit_results(output):
    """(total, failed, failed_tests) from PHPUnit output."""
    m = re.search(r"Tests: (\d+), Assertions: \d+(?:, Failures: (\d+))?(?:, Errors: (\d+))?", output)
    if m:
        total = int(m.group(1))
        failed = int(m.group(2) or 0) + int(m.group(3) or 0)
    else:
        m = re.search(r"OK \((\d+) tests?,", output)
        total, failed = (int(m.group(1)), 0) if m else (0, 0)
    names = re.findall(r"^\d+\) (\S+)", output, re.M)
    return total, failed, sorted(set(names))


def parse_phpunit_roster(output):
    """Test names from `phpunit --list-tests` output."""
    return sorted({line.strip()[2:] for line in output.splitlines() if line.startswith(" - ")})


def parse_minitest_results(output):
    """(total, failed, failed_tests, passed_tests) from minitest output (needs --verbose for names)."""
    import re
    m = re.search(r"(\d+) runs, (\d+) assertions, (\d+) failures, (\d+) errors", output)
    total = int(m.group(1)) if m else 0
    failed = (int(m.group(3)) + int(m.group(4))) if m else 0
    bad = set(re.findall(r"^\s*\d+\) (?:Failure|Error):\n(\S+)", output, re.M))
    roster = re.findall(r"^(\S+#\S+) = ", output, re.M)
    passed = sorted(set(roster) - bad)
    return total, failed, sorted(bad), passed


def parse_vitest_results(output):
    """(total, failed, failed_files) from `vitest run --reporter=json` output (jest format)."""
    import json
    try:
        doc = json.loads(output[output.index("{"):output.rindex("}") + 1])
    except ValueError:
        return 0, 0, []
    total = doc.get("numTotalTests", 0) or 0
    failed = doc.get("numFailedTests", 0) or 0
    names = []
    for r in doc.get("testResults", []) or []:
        asserts = r.get("assertionResults", []) or []
        if r.get("status") == "failed" or any(a.get("status") == "failed" for a in asserts):
            if r.get("name"):
                names.append(r["name"])
    return total, failed, sorted(set(names))


def parse_rspec_results(output):
    """(total, failed, passing_locations) from `rspec --format json` output."""
    import json
    try:
        doc = json.loads(output[output.index("{"):output.rindex("}") + 1])
    except ValueError:
        return 0, 0, []
    summary = doc.get("summary", {})
    locations = []
    for e in doc.get("examples", []):
        if e.get("status") != "failed" and e.get("file_path"):
            path = e["file_path"][2:] if e["file_path"].startswith("./") else e["file_path"]
            locations.append(f"{path}:{e['line_number']}")
    return summary.get("example_count", 0), summary.get("failure_count", 0), sorted(set(locations))


MINITEST_LOADER = ('Dir["test/**/test_*.rb", "test/**/*_test.rb", "tests/**/test_*.rb", '
                   '"tests/**/*_test.rb", "test_*.rb", "*_test.rb"].sort.each '
                   '{ |f| require File.expand_path(f) }')


def parse_cargo_results(output):
    """(total, failed, failed_tests) from `cargo test` output (all test binaries summed)."""
    import re
    total, failed = 0, 0
    for m in re.finditer(r"test result: \w+\. (\d+) passed; (\d+) failed", output):
        total += int(m.group(1)) + int(m.group(2))
        failed += int(m.group(2))
    names, in_failures = [], False
    for line in output.splitlines():
        if line == "failures:":
            in_failures = True
            continue
        if in_failures:
            if line.startswith("    ") and line.strip():
                names.append(line.strip())
            elif not line.startswith(" "):
                in_failures = False
    return total, failed, sorted(set(names))


def parse_go_results(output):
    """(total, failed, failed_tests, passed_tests) from `go test -json` output (top-level tests)."""
    import json
    verdicts = {}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("Action") not in ("pass", "fail"):
            continue
        test = event.get("Test", "")
        if not test or "/" in test:
            continue  # package lines and subtests: the top-level test carries the verdict
        if verdicts.get(test) != "fail":
            verdicts[test] = event["Action"]
    failed = sorted(t for t, v in verdicts.items() if v == "fail")
    passed = sorted(t for t, v in verdicts.items() if v == "pass")
    return len(verdicts), len(failed), failed, passed


def parse_gradle_results(root):
    """(total, failed, failed_classes, passed_classes) from JUnit XML under build/test-results."""
    import xml.etree.ElementTree as ET
    total, failed, failed_c, passed_c = 0, 0, [], []
    for xml in sorted(Path(root).glob("**/build/test-results/*/TEST-*.xml")):
        try:
            r = ET.parse(xml).getroot()
        except ET.ParseError:
            continue
        t = int(float(r.get("tests", 0)))
        bad = int(float(r.get("failures", 0))) + int(float(r.get("errors", 0)))
        total += t
        failed += bad
        name = (r.get("name") or xml.stem[5:]).split(".")[-1]
        (failed_c if bad else passed_c).append(name)
    return total, failed, sorted(set(failed_c)), sorted(set(passed_c))


class Sandbox:
    """A throwaway copy of a project. Use with `with Sandbox(project) as sb:`."""

    def __init__(self, project):
        self.project = project
        self.path = None

    def __enter__(self):
        tmp = Path(tempfile.mkdtemp(prefix="bugbait-"))
        self.path = tmp / "project"
        shutil.copytree(self.project.root, self.path, ignore=_ignore, symlinks=True)
        # Files the build step generates (like _version.py) that aren't in the source tree
        for rel, content in self.project.extra_files.items():
            target = self.path / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        if (self.project.language.id in ("javascript", "typescript")
                and self.project.js_framework == "vitest"):
            # node_modules is too big to copy: link the installed one instead (read-only use).
            src = self.project.root / "node_modules"
            dest = self.path / "node_modules"
            if src.is_dir() and not dest.exists():
                try:
                    dest.symlink_to(src, target_is_directory=True)
                except OSError:
                    pass
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.path.parent, ignore_errors=True)

    def apply_mutation(self, file, old, new):
        """Plant a bug by replacing one piece of code (first occurrence only)."""
        path = self.path / file
        code = path.read_text()
        if old not in code:
            raise ValueError(f"Could not find '{old}' in {file}")
        path.write_text(code.replace(old, new, 1))

    def add_file(self, name, content):
        """Add an extra file (e.g. a generated test) to the sandbox."""
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _run_vitest(self, test_paths, timeout):
        """Run vitest in the sandbox (deps symlinked from the installed project)."""
        binary = self.path / "node_modules" / "vitest" / "vitest.mjs"
        if not binary.is_file():
            return False, "vitest isn't installed for this project."
        node = shutil.which("node")
        if not node:
            return False, "Node.js isn't installed."
        if test_paths:
            args = [str(t) for t in test_paths]
        elif self.project.excluded_vitest_files:
            excluded = set(self.project.excluded_vitest_files)
            mine = [t.as_posix() for t in self.project.test_files()]
            args = [t for t in mine if t not in excluded]
            # Test files added only to this sandbox (e.g. generated tests) still need to run;
            # project files stay excluded.
            known = {t.as_posix() for t in self.project.code_files()}
            for p in sorted(self.path.rglob("*")):
                if not p.is_file() or "node_modules" in p.parts:
                    continue
                rel = p.relative_to(self.path)
                if rel.as_posix() not in known and self.project.language.is_test_file(rel):
                    args.append(rel.as_posix())
        else:
            args = []
        try:
            result = subprocess.run(
                [node, str(binary), "run", "--reporter=json", *args],
                cwd=self.path, capture_output=True, text=True,
                timeout=timeout, env=self.project.env(self.path))
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"Couldn't run vitest: {e}"
        return result.returncode == 0, result.stdout + result.stderr

    def _run_kotlin(self, test_paths, timeout):
        """Compile everything with kotlinc, then run test classes with JUnitCore."""
        libs = self.project.kotlin_libs
        cp = os.pathsep.join([libs["junit"], libs["hamcrest"], libs["stdlib"]])
        sources = sorted(str(p.relative_to(self.path)) for p in self.path.rglob("*.kt")
                         if "bugbait-classes" not in p.parts)
        out = self.path / "bugbait-classes"
        try:
            compiled = subprocess.run(
                [self.project.kotlin_cmd, *sources, "-cp", cp, "-d", str(out)],
                cwd=self.path, capture_output=True, text=True,
                timeout=timeout, env=self.project.env(self.path))
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"Couldn't run kotlinc: {e}"
        if compiled.returncode != 0:
            return False, compiled.stdout + compiled.stderr
        if test_paths:
            classes = [c for c in (kotlin_test_class(self.path / t) for t in test_paths) if c]
        else:
            classes = [c for c in (kotlin_test_class(p) for p in sorted(self.path.rglob("*.kt"))
                                   if self.project.language.is_test_file(p.relative_to(self.path)))
                       if c]
            classes = [c for c in classes if c not in set(self.project.excluded_kotlin_classes)]
        if not classes:
            return True, "OK (0 tests)"
        try:
            result = subprocess.run(
                [self.project.java_bin, "-cp",
                 os.pathsep.join([str(out), libs["junit"], libs["hamcrest"], libs["stdlib"]]),
                 "org.junit.runner.JUnitCore", *classes],
                cwd=self.path, capture_output=True, text=True,
                timeout=timeout, env=self.project.env(self.path))
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"Couldn't run the tests: {e}"
        return result.returncode == 0, result.stdout + result.stderr

    def list_phpunit_tests(self, timeout=300):
        """Test names from `phpunit --list-tests` (for set-aside); [] if it fails."""
        files = sorted(t.as_posix() for t in self.project.test_files())
        try:
            result = subprocess.run([*self.project.phpunit_cmd, "--list-tests", *files],
                                    cwd=self.path, capture_output=True, text=True,
                                    timeout=timeout, env=self.project.env(self.path))
        except (subprocess.SubprocessError, OSError):
            return []
        return parse_phpunit_roster(result.stdout + result.stderr)

    def run_tests(self, *test_paths, timeout=300, stop_early=True):
        """Run the project's tests in the sandbox (optionally only some test files). Returns (passed, output).

        stop_early: stop at the first failure (fast, used when checking a planted bug).
        """
        if timeout == 300:
            if self.project.language.id == "java":
                timeout = 900  # Maven builds (and first-run downloads) are slow
            elif self.project.language.id in ("rust", "csharp", "kotlin"):
                timeout = 600  # first-time compiles are slow
            elif (self.project.language.id in ("javascript", "typescript")
                    and self.project.js_framework == "vitest"):
                timeout = 600  # big suites + cold workers are slow
        if self.project.language.id in ("javascript", "typescript"):
            if self.project.js_framework == "vitest":
                return self._run_vitest(test_paths, timeout)
            cmd = ["node", "--test", *test_paths]
        elif self.project.language.id == "kotlin":
            return self._run_kotlin(test_paths, timeout)
        elif self.project.language.id == "csharp":
            cmd = ["dotnet", "test", self.project.dotnet_target]
            if test_paths:
                stem = Path(test_paths[0]).stem
                check_stem = Path(self.project.language.check_name).stem
                want = "BugbaitCheck" if stem == check_stem else "BugbaitGenerated"
                cmd += ["--filter", f"FullyQualifiedName~{want}"]
            elif self.project.excluded_dotnet_tests:
                cmd += ["--filter", "&".join(f"FullyQualifiedName!={t}"
                                             for t in sorted(set(self.project.excluded_dotnet_tests)))]
        elif self.project.language.id == "php":
            phpunit = self.project.phpunit_cmd
            if test_paths:
                cmd = [*phpunit, *test_paths]
            else:
                files = sorted(t.as_posix() for t in self.project.test_files())
                cmd = [*phpunit, *files]
                if self.project.included_phpunit_tests:
                    names = "|".join(re.escape(t).replace("/", r"\/")
                                     for t in sorted(set(self.project.included_phpunit_tests)))
                    cmd += ["--filter", f"/^({names})$/"]
        elif self.project.language.id == "ruby":
            if self.project.ruby_framework == "rspec":
                if test_paths:
                    cmd = ["rspec", *test_paths]
                elif self.project.rspec_locations:
                    cmd = ["rspec", "--format", "json", *sorted(set(self.project.rspec_locations))]
                else:
                    specs = [t.as_posix() for t in self.project.test_files()
                             if t.name.endswith("_spec.rb") or "spec" in t.parts[:-1]]
                    cmd = ["rspec", "--format", "json", *sorted(specs)]
            else:
                base = ["ruby", "-Ilib", "-Itest", "-Itests", "-rminitest/autorun"]
                if test_paths:
                    cmd = [*base, *test_paths, "--seed", "1"]
                else:
                    # -e keeps eating ruby options, so minitest flags go after --.
                    cmd = [*base, "-e", MINITEST_LOADER, "--", "--verbose", "--seed", "1"]
                    if self.project.included_minitest_tests:
                        names = "|".join(re.escape(t) for t in sorted(set(self.project.included_minitest_tests)))
                        cmd += ["-n", f"/^({names})$/"]
        elif self.project.language.id == "rust":
            # libtest has no stable fail-fast, so stop_early changes nothing here.
            cmd = ["cargo", "test"]
            if test_paths:
                cmd += ["--test", Path(test_paths[0]).stem]
            elif self.project.skipped_rust_tests:
                cmd += ["--", "--exact"]
                for name in sorted(set(self.project.skipped_rust_tests)):
                    cmd += ["--skip", name]
        elif self.project.language.id == "go":
            cmd = ["go", "test", "-count=1"]  # -count=1: never serve cached results for mutated code
            if test_paths:
                p = Path(test_paths[0])
                pkg = "." if p.parent.as_posix() in (".", "") else "./" + p.parent.as_posix()
                check_stem = Path(self.project.language.check_name).stem
                # The Fixer always names its check test TestBugbaitCheck; merged tests are TestBug*.
                cmd += ["-run", "^TestBugbaitCheck$" if p.stem == check_stem else "^TestBug", pkg]
            else:
                if stop_early:
                    cmd += ["-failfast"]
                if self.project.included_go_tests:
                    cmd += ["-run", "^(%s)$" % "|".join(sorted(set(self.project.included_go_tests)))]
                cmd += ["-json", "./..."]
        elif self.project.language.id == "java" and self.project.build == "gradle":
            for d in self.path.glob("**/build/test-results"):
                shutil.rmtree(d, ignore_errors=True)  # stale results would corrupt parsing
            cmd = [*self.project.gradle_cmd, *self.project.gradle_tasks, "--console=plain"]
            if test_paths:
                for p in test_paths:
                    cmd += ["--tests", Path(p).stem]
            elif self.project.included_gradle_tests:
                for name in sorted(set(self.project.included_gradle_tests)):
                    cmd += ["--tests", name]
        elif self.project.language.id == "java":
            cmd = ["mvn", "-B", "test"]
            if test_paths:
                names = ",".join(Path(p).stem for p in test_paths)
                cmd += [f"-Dtest={names}", "-Dsurefire.failIfNoSpecifiedTests=false"]
            elif self.project.excluded_java_tests:
                cmd += [f"-Dtest={','.join('!' + e for e in self.project.excluded_java_tests)}",
                        "-Dsurefire.failIfNoSpecifiedTests=false"]
        else:
            cmd = [self.project.python, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider",
                   "-p", "no:randomly", "-x" if stop_early else "-rfE", *self.project.pytest_args, *test_paths]
        try:
            result = subprocess.run(cmd, cwd=self.path, capture_output=True, text=True,
                                    timeout=timeout, env=self.project.env(self.path))
        except subprocess.TimeoutExpired:
            return False, "Tests timed out (treated as caught)"
        return result.returncode == 0, result.stdout + result.stderr
