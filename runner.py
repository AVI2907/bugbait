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
        if self.project.language.id == "javascript":
            cmd = ["node", "--test", *test_paths]
        elif self.project.language.id == "java":
            cmd = ["mvn", "-B", "test"]
            if test_paths:
                names = ",".join(Path(p).stem for p in test_paths)
                cmd += [f"-Dtest={names}", "-Dsurefire.failIfNoSpecifiedTests=false"]
        else:
            cmd = [self.project.python, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider",
                   "-p", "no:randomly", "-x" if stop_early else "-rfE", *self.project.pytest_args, *test_paths]
        try:
            result = subprocess.run(cmd, cwd=self.path, capture_output=True, text=True,
                                    timeout=timeout, env=self.project.env(self.path))
        except subprocess.TimeoutExpired:
            return False, "Tests timed out (treated as caught)"
        return result.returncode == 0, result.stdout + result.stderr
