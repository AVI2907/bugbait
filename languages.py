"""Language plugins: how BugBait discovers, checks and tests each language.

Adding a language means adding one Language below (file patterns, test
detection, syntax check, generated file names) plus its test command in
runner.py. Everything else (Saboteur, sandboxing, Fixer, scoring) is generic.
"""

import ast
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Folders that are never copied into a sandbox or scanned for source files
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    "node_modules", "venv", ".venv", "env", ".env", "build", "dist", ".eggs", "site-packages",
    ".idea", ".vscode", "htmlcov", ".ipynb_checkpoints",
}


@dataclass(frozen=True)
class Language:
    id: str               # "python" | "javascript"
    label: str            # "Python" | "JavaScript"
    extensions: tuple     # source file extensions, e.g. (".py",)
    generated_name: str   # merged Fixer output, e.g. "test_bugbait_generated.py"
    check_name: str       # single-candidate check file the Fixer verifies
    test_framework: str   # what runs the tests, e.g. "pytest" (shown in messages)
    test_hint: str        # where tests live, e.g. "test_*.py, *_test.py and tests/ folders"

    def is_test_file(self, rel: Path) -> bool:
        raise NotImplementedError

    def syntax_ok(self, code: str) -> bool:
        """True if the code parses. Used to reject bugs that break syntax."""
        raise NotImplementedError


class Python(Language):
    def is_test_file(self, rel: Path) -> bool:
        return (rel.name.startswith("test_") or rel.name.endswith("_test.py") or rel.name in ("test.py", "tests.py")
                or any(p in ("tests", "test", "testing") for p in rel.parts[:-1]))

    def syntax_ok(self, code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False


class JavaScript(Language):
    def is_test_file(self, rel: Path) -> bool:
        return (rel.name.endswith((".test.js", ".spec.js", ".test.mjs", ".test.cjs"))
                or any(p in ("__tests__", "tests", "test", "testing") for p in rel.parts[:-1]))

    def syntax_ok(self, code: str) -> bool:
        node = shutil.which("node")
        if not node:
            return True  # can't check: let the sandbox run be the judge
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=True) as f:
            f.write(code)
            f.flush()
            r = subprocess.run([node, "--check", f.name], capture_output=True, timeout=30)
        return r.returncode == 0


PYTHON = Python(id="python", label="Python", extensions=(".py",),
                generated_name="test_bugbait_generated.py", check_name="test_bugbait_check.py",
                test_framework="pytest", test_hint="test_*.py, *_test.py and tests/ folders")
JAVASCRIPT = JavaScript(id="javascript", label="JavaScript", extensions=(".js", ".mjs", ".cjs"),
                        generated_name="bugbait.generated.test.js", check_name="bugbait.check.test.js",
                        test_framework="node --test", test_hint="*.test.js, *.spec.js and __tests__/ folders")

ALL = (PYTHON, JAVASCRIPT)


def detect(root: Path) -> Language:
    """Pick the language with the most code files (a package.json breaks ties toward JS)."""
    counts = {lang: 0 for lang in ALL}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for f in filenames:
            for lang in ALL:
                if f.endswith(lang.extensions):
                    counts[lang] += 1
    if (root / "package.json").is_file():
        counts[JAVASCRIPT] += 1
    best = max(ALL, key=lambda lang: counts[lang])
    return best if counts[best] else PYTHON
