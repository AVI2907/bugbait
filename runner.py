"""Sandbox runner: copies a target project, plants bugs, and runs its tests.

Each trial runs in a fresh temporary folder outside the repo, so the
original target is never modified and nothing is left behind.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent


class Sandbox:
    """A throwaway copy of a target project. Use with `with Sandbox(...) as sb:`."""

    def __init__(self, target_name):
        self.src = ROOT / "targets" / target_name
        if not self.src.is_dir():
            raise FileNotFoundError(f"No target named '{target_name}' in targets/")
        self.path = None

    def __enter__(self):
        tmp = Path(tempfile.mkdtemp(prefix="bugbait-"))
        self.path = tmp / "project"
        shutil.copytree(
            self.src, self.path,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
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

    def run_tests(self, timeout=60):
        """Run pytest in the sandbox. Returns (passed, output)."""
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=self.path, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return False, "Tests timed out (treated as caught)"
        return result.returncode == 0, result.stdout + result.stderr
