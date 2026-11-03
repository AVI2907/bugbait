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
    ".idea", ".vscode", "htmlcov", ".ipynb_checkpoints", "target",
}


@dataclass(frozen=True)
class Language:
    id: str               # "python" | "javascript" | "java" | "go" | "typescript" | "rust" | "ruby"
    label: str            # "Python" | "JavaScript" | "Java" | "Go" | "TypeScript" | "Rust" | "Ruby"
    extensions: tuple     # source file extensions, e.g. (".py",)
    generated_name: str   # merged Fixer output, e.g. "test_bugbait_generated.py"
    check_name: str       # single-candidate check file the Fixer verifies
    test_framework: str   # what runs the tests, e.g. "pytest" (shown in messages)
    test_hint: str        # where tests live, e.g. "test_*.py, *_test.py and tests/ folders"

    def is_test_file(self, rel: Path) -> bool:
        raise NotImplementedError

    def is_source_file(self, rel: Path) -> bool:
        """True if an extension-matching file is worth attacking. Rarely overridden."""
        return True

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


class Java(Language):
    def is_test_file(self, rel: Path) -> bool:
        return (rel.name.endswith(("Test.java", "Tests.java", "TestCase.java"))
                or "src/test" in rel.as_posix())

    def syntax_ok(self, code: str) -> bool:
        # javac can't check one file without its dependencies, so every bug goes
        # to the sandbox: builds that break count as caught (a known over-count).
        return True


class TypeScript(Language):
    def is_test_file(self, rel: Path) -> bool:
        return (rel.name.endswith((".test.ts", ".spec.ts"))
                or any(p in ("__tests__", "tests", "test", "testing") for p in rel.parts[:-1]))

    def is_source_file(self, rel: Path) -> bool:
        return not rel.name.endswith(".d.ts")  # declarations have no runtime code to attack

    def syntax_ok(self, code: str) -> bool:
        # Node strips types without checking them, so a standalone parse buys nothing:
        # the sandbox run is the judge (syntax breaks count as caught, a known over-count).
        return True


class Rust(Language):
    def is_test_file(self, rel: Path) -> bool:
        return any(p in ("tests", "benches", "examples") for p in rel.parts[:-1])

    def syntax_ok(self, code: str) -> bool:
        rustfmt = shutil.which("rustfmt")
        if not rustfmt:
            return True  # can't check: let the sandbox run be the judge
        with tempfile.NamedTemporaryFile("w", suffix=".rs", delete=True) as f:
            f.write(code)
            f.flush()
            # --emit stdout only parses (never rewrites); try both modern editions.
            for edition in ("2021", "2024"):
                r = subprocess.run([rustfmt, "--edition", edition, "--emit", "stdout", f.name],
                                   capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
        return False


class Ruby(Language):
    rspec_generated_name = "bugbait_generated_spec.rb"
    rspec_check_name = "bugbait_check_spec.rb"

    def is_test_file(self, rel: Path) -> bool:
        return (rel.name.endswith("_spec.rb") or rel.name.startswith("test_")
                or rel.name.endswith("_test.rb")
                or any(p in ("spec", "test", "tests") for p in rel.parts[:-1]))

    def detect_framework(self, test_files):
        """Which style the test files use: "rspec" or "minitest"."""
        specs = units = 0
        for t in test_files:
            if t.name.endswith("_spec.rb") or "spec" in t.parts[:-1]:
                specs += 1
            elif (t.name.startswith("test_") or t.name.endswith("_test.rb")
                    or any(p in ("test", "tests") for p in t.parts[:-1])):
                units += 1
        return "rspec" if specs > units else "minitest"

    def syntax_ok(self, code: str) -> bool:
        ruby = shutil.which("ruby")
        if not ruby:
            return True  # can't check: let the sandbox run be the judge
        with tempfile.NamedTemporaryFile("w", suffix=".rb", delete=True) as f:
            f.write(code)
            f.flush()
            r = subprocess.run([ruby, "-c", f.name], capture_output=True, timeout=30)
        return r.returncode == 0


class Go(Language):
    def is_test_file(self, rel: Path) -> bool:
        return rel.name.endswith("_test.go")

    def syntax_ok(self, code: str) -> bool:
        gofmt = shutil.which("gofmt")
        if not gofmt and shutil.which("go"):
            sib = Path(shutil.which("go")).parent / "gofmt"
            gofmt = str(sib) if sib.is_file() else None
        if not gofmt:
            return True  # can't check: let the sandbox run be the judge
        with tempfile.NamedTemporaryFile("w", suffix=".go", delete=True) as f:
            f.write(code)
            f.flush()
            r = subprocess.run([gofmt, "-e", f.name], capture_output=True, timeout=30)
        return r.returncode == 0


PYTHON = Python(id="python", label="Python", extensions=(".py",),
                generated_name="test_bugbait_generated.py", check_name="test_bugbait_check.py",
                test_framework="pytest", test_hint="test_*.py, *_test.py and tests/ folders")
JAVASCRIPT = JavaScript(id="javascript", label="JavaScript", extensions=(".js", ".mjs", ".cjs"),
                        generated_name="bugbait.generated.test.js", check_name="bugbait.check.test.js",
                        test_framework="node --test", test_hint="*.test.js, *.spec.js and __tests__/ folders")
JAVA = Java(id="java", label="Java", extensions=(".java",),
            generated_name="BugbaitGeneratedTest.java", check_name="BugbaitCheckTest.java",
            test_framework="mvn test", test_hint="*Test.java files under src/test/")
GO = Go(id="go", label="Go", extensions=(".go",),
        generated_name="bugbait_generated_test.go", check_name="bugbait_check_test.go",
        test_framework="go test", test_hint="*_test.go files")
TYPESCRIPT = TypeScript(id="typescript", label="TypeScript", extensions=(".ts",),
                        generated_name="bugbait.generated.test.ts", check_name="bugbait.check.test.ts",
                        test_framework="node --test", test_hint="*.test.ts, *.spec.ts and __tests__/ folders")
RUST = Rust(id="rust", label="Rust", extensions=(".rs",),
            generated_name="bugbait_generated.rs", check_name="bugbait_check.rs",
            test_framework="cargo test", test_hint="tests/ folders and #[test] functions")
RUBY = Ruby(id="ruby", label="Ruby", extensions=(".rb",),
            generated_name="test_bugbait_generated.rb", check_name="test_bugbait_check.rb",
            test_framework="minitest or RSpec",
            test_hint="test_*.rb, *_test.rb, *_spec.rb and test/ or spec/ folders")

ALL = (PYTHON, JAVASCRIPT, JAVA, GO, TYPESCRIPT, RUST, RUBY)

# Extra extensions and build files that count toward a language for detection only
DETECT_HINTS = {JAVASCRIPT: (".jsx",), JAVA: (".kt", ".kts", ".scala"), TYPESCRIPT: (".tsx",),
                RUBY: (".rake", ".gemspec")}


def detect(root: Path) -> Language:
    """Pick the language with the most code files (build files break ties)."""
    counts = {lang: 0 for lang in ALL}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for f in filenames:
            for lang in ALL:
                if f.endswith(lang.extensions) or f.endswith(DETECT_HINTS.get(lang, ())):
                    counts[lang] += 1
    if (root / "package.json").is_file():
        counts[JAVASCRIPT] += 1
    if (root / "pom.xml").is_file():
        counts[JAVA] += 1
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        counts[JAVA] += 1
    if (root / "go.mod").is_file():
        counts[GO] += 1
    if (root / "tsconfig.json").is_file():
        counts[TYPESCRIPT] += 1
    if (root / "Cargo.toml").is_file():
        counts[RUST] += 1
    if (root / "Gemfile").is_file() or (root / "Rakefile").is_file():
        counts[RUBY] += 1
    best = max(ALL, key=lambda lang: counts[lang])
    return best if counts[best] else PYTHON
