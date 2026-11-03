"""Sandbox runner: copies a project, plants a bug, and runs the project's tests.

Each trial runs in a fresh temporary folder, so the original project is never
modified and nothing is left behind.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from projects import SKIP_DIRS

ROOT = Path(__file__).parent


def _ignore(directory, names):
    return [n for n in names
            if n in SKIP_DIRS or n.endswith((".pyc", ".egg-info")) or n == ".DS_Store"]


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

    def run_tests(self, *test_paths, timeout=300, stop_early=True):
        """Run the project's tests in the sandbox (optionally only some test files). Returns (passed, output).

        stop_early: stop at the first failure (fast, used when checking a planted bug).
        """
        if timeout == 300 and self.project.language.id == "java":
            timeout = 900  # Maven builds (and first-run downloads) are slow
        if self.project.language.id in ("javascript", "typescript"):
            cmd = ["node", "--test", *test_paths]
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
