"""Where BugBait's projects come from, and how to run their tests.

A project can be:
  - a built-in example in targets/            e.g.  "inflection"
  - any folder on this computer               e.g.  "/Users/me/code/myapp"
  - a GitHub repository                       e.g.  "https://github.com/owner/repo" or "owner/repo"

BugBait never edits the original project. Every trial runs on a temporary copy.
"""

import base64
import functools
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent
# Cloned repos and per-project environments (git-ignored). Override with BUGBAIT_WORKSPACE.
WORKSPACE = Path(os.environ.get("BUGBAIT_WORKSPACE") or ROOT / "workspace").expanduser()

from languages import PYTHON, SKIP_DIRS, Language, detect  # noqa: E402  (SKIP_DIRS re-exported for runner)
NOT_SOURCE_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "benchmarks", "scripts",
                   "tools", "bin", "migrations"}
NOT_SOURCE_FILES = {"setup.py", "conftest.py", "noxfile.py", "fabfile.py", "manage.py", "__main__.py"}

GITHUB_RE = re.compile(r"^(?:https?://github\.com/|git@github\.com:)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class ProjectError(RuntimeError):
    pass


@dataclass
class Project:
    name: str               # short display name, also used for results/<name>/
    root: Path              # the original project folder (never modified)
    source: str             # "example" | "local" | "github"
    url: str | None = None  # GitHub URL when source == "github"
    language: Language = PYTHON  # detected properly by _finish
    java_home: str | None = None  # selected JDK for Java projects (also set by _finish)
    excluded_java_tests: list = field(default_factory=list)  # test classes that already fail (Maven)
    build: str = "maven"  # Java build tool: "maven" | "gradle"
    gradle_cmd: list = field(default_factory=list)  # e.g. ["./gradlew"] or ["gradle"]
    gradle_tasks: list = field(default_factory=list)  # e.g. ["test"] or ["testDebugUnitTest"]
    included_gradle_tests: list = field(default_factory=list)  # passing classes (baseline set-aside)
    included_go_tests: list = field(default_factory=list)  # passing tests (baseline set-aside)
    skipped_rust_tests: list = field(default_factory=list)  # failing tests (baseline set-aside)
    ruby_framework: str = "minitest"  # "minitest" | "rspec"
    included_minitest_tests: list = field(default_factory=list)  # passing tests (baseline set-aside)
    rspec_locations: list = field(default_factory=list)  # passing file:line (baseline set-aside)
    phpunit_cmd: list = field(default_factory=list)  # e.g. ["vendor/bin/phpunit"] or ["phpunit"]
    included_phpunit_tests: list = field(default_factory=list)  # passing tests (baseline set-aside)
    dotnet_target: str = ""  # .sln or test .csproj that `dotnet test` runs
    excluded_dotnet_tests: list = field(default_factory=list)  # failing tests (baseline set-aside)
    dotnet_extra_env: dict = field(default_factory=dict)  # NUGET_PACKAGES fallback (restricted HOME)
    kotlin_cmd: str = ""  # kotlinc binary
    java_bin: str = ""  # java binary (Kotlin test runs)
    kotlin_libs: dict = field(default_factory=dict)  # junit/hamcrest/stdlib jars (downloaded once)
    excluded_kotlin_classes: list = field(default_factory=list)  # failing classes (baseline set-aside)
    go_extra_env: dict = field(default_factory=dict)  # GOCACHE/GOPATH fallbacks (restricted HOME)
    python: str = sys.executable
    pythonpath: list = field(default_factory=list)   # relative paths to put first on sys.path
    pytest_args: list = field(default_factory=list)  # e.g. --deselect for tests that already fail
    extra_files: dict = field(default_factory=dict)  # build-generated files (e.g. _version.py) to add to sandboxes

    # ----- files -----
    def is_test_file(self, rel: Path) -> bool:
        return self.language.is_test_file(rel)

    def code_files(self):
        """All source files in the project's language (relative paths), skipping venvs and caches."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for f in sorted(filenames):
                if f.endswith(self.language.extensions):
                    out.append((Path(dirpath) / f).relative_to(self.root))
        return out

    def test_files(self):
        return [p for p in self.code_files() if self.is_test_file(p)]

    def source_files(self):
        """Files worth attacking: real code, not tests, docs, examples or empty __init__ files."""
        out = []
        for rel in self.code_files():
            if self.is_test_file(rel) or rel.name in NOT_SOURCE_FILES:
                continue
            if not self.language.is_source_file(rel):
                continue
            if any(p in NOT_SOURCE_DIRS for p in rel.parts[:-1]):
                continue
            path = self.root / rel
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            if len(text.strip()) < 40:   # empty or trivial file
                continue
            out.append({"path": rel.as_posix(), "lines": text.count("\n") + 1})
        return out

    def default_files(self, limit=3):
        """If the user didn't choose, attack the biggest few files that aren't huge."""
        files = sorted(self.source_files(), key=lambda f: f["lines"], reverse=True)
        fitting = [f for f in files if f["lines"] <= 1500] or files
        return [f["path"] for f in fitting[:limit]]

    def test_dir(self) -> str:
        """Folder where generated tests should live (next to the existing tests)."""
        if self.language.id == "rust":
            return "tests"  # cargo auto-discovers tests/*.rs; inline unit tests have no file to join
        tests = self.test_files()
        if not tests:
            return "."
        parents = [t.parent.as_posix() for t in tests]
        return max(set(parents), key=parents.count)

    def relevant_tests(self, source_rel: str, max_chars=60_000):
        """Existing test code to show the AI: tests that mention this module first, within a size budget."""
        module = Path(source_rel).stem
        if module == "__init__":
            module = Path(source_rel).parent.name
        tests = self.test_files()
        scored = []
        for t in tests:
            try:
                text = (self.root / t).read_text()
            except (UnicodeDecodeError, OSError):
                continue
            score = (3 if module in t.name else 0) + (2 if re.search(rf"\b{re.escape(module)}\b", text) else 0)
            scored.append((score, t, text))
        scored.sort(key=lambda s: -s[0])
        parts, used = [], 0
        for score, t, text in scored:
            if used and score == 0:
                break
            chunk = f"# {t.as_posix()}\n{text}"
            if used + len(chunk) > max_chars:
                chunk = chunk[: max(0, max_chars - used)] + "\n# ... (truncated)"
            parts.append(chunk)
            used += len(chunk)
            if used >= max_chars:
                break
        return "\n\n".join(parts) or "# (no existing tests found)"

    # ----- running -----
    def env(self, sandbox_root: Path):
        """Environment for running tests in a sandbox: the sandbox's code must win over any installed copy."""
        paths = [str(sandbox_root / p) for p in self.pythonpath] + [str(sandbox_root)]
        old = os.environ.get("PYTHONPATH")
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": os.pathsep.join(paths + ([old] if old else []))}
        if self.java_home:
            env["JAVA_HOME"] = self.java_home
            env["PATH"] = str(Path(self.java_home) / "bin") + os.pathsep + env.get("PATH", "")
        env.update(self.go_extra_env)
        env.update(self.dotnet_extra_env)
        return env


# ---------- finding / creating projects ----------

def list_examples():
    return sorted(p.name for p in (ROOT / "targets").iterdir() if p.is_dir())


def is_hosted_safe_spec(spec: str) -> bool:
    """True for example names and GitHub links (all the hosted demo accepts)."""
    spec = (spec or "").strip().strip('"').strip("'")
    if spec in list_examples():
        return True
    m = GITHUB_RE.match(spec)
    if not m or ("github.com" not in spec and spec.count("/") != 1):
        return False
    return not m.group(1).startswith(".") and ".." not in spec  # no ../ Owner tricks


def resolve(spec: str, log=print, refresh=False) -> Project:
    """Turn what the user typed (example name, folder path or GitHub link) into a Project."""
    spec = (spec or "").strip().strip('"').strip("'")
    if not spec:
        raise ProjectError("Choose a project: an example name, a folder path, or a GitHub link.")

    if spec in list_examples():
        return _finish(Project(name=spec, root=ROOT / "targets" / spec, source="example"), log)

    path = Path(os.path.expanduser(spec))
    if path.is_dir():
        path = path.resolve()
        return _finish(Project(name=path.name, root=path, source="local"), log)

    m = GITHUB_RE.match(spec)
    if m and ("github.com" in spec or spec.count("/") == 1):
        owner, repo = m.group(1), m.group(2)
        return _finish(_clone(owner, repo, log, refresh), log)

    if "/" in spec or "\\" in spec or spec.startswith("~"):
        raise ProjectError(f"Folder not found: {spec}")
    raise ProjectError(f"'{spec}' isn't an example, a folder, or a GitHub link. "
                       f"Examples: {', '.join(list_examples())}")


def _github_token():
    try:
        from github_pr import get_token_optional
        return get_token_optional()
    except Exception:
        return None


def _git_clone(url, dest, token):
    cmd = ["git", "clone", "--depth", "1", url + ".git", str(dest)]
    if token:
        # Basic auth works with every token kind (gh OAuth, classic + fine-grained PATs);
        # Bearer fails for gh's OAuth tokens on git operations. A header keeps the token
        # out of the URL and error output.
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd = ["git", "-c", f"http.extraHeader=Authorization: Basic {basic}", *cmd[1:]]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})  # never hang asking for a password


def _looks_like_auth_failure(stderr):
    return "Authentication failed" in (stderr or "") or "401" in (stderr or "")


def _clone(owner, repo, log, refresh):
    url = f"https://github.com/{owner}/{repo}"
    dest = WORKSPACE / "repos" / f"{owner}__{repo}"
    if dest.is_dir() and not refresh:
        log(f"Using the copy of {owner}/{repo} downloaded earlier")
    else:
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        log(f"Downloading {owner}/{repo} from GitHub...")
        token = _github_token()
        r = _git_clone(url, dest, token)
        if r.returncode != 0 and token and _looks_like_auth_failure(r.stderr):
            # The token was rejected (expired? revoked?). Public repos don't need it,
            # so retry anonymously before giving up.
            log("GitHub rejected the stored token. Retrying without it…")
            if dest.exists():
                shutil.rmtree(dest)
            r = _git_clone(url, dest, None)
            if r.returncode == 0:
                log("Downloaded without the token. Reconnect GitHub (Options → Settings) "
                    "to test private repos again.")
        if r.returncode != 0:
            msg = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unknown error"
            if "not found" in msg.lower() or "could not read" in msg.lower():
                msg = "Repository not found. Check the link, and that the repo is public."
            elif _looks_like_auth_failure(r.stderr):
                msg = ("GitHub rejected the login. Reconnect in the dashboard "
                       "(Options → Settings → Disconnect, then Connect again).")
            raise ProjectError(f"Couldn't download {owner}/{repo}: {msg}")
    return Project(name=f"{owner}-{repo}", root=dest, source="github", url=url)


_jdk_cache = None


def _pom_java_version(pom):
    try:
        text = pom.read_text()
    except OSError:
        return None
    for tag in ("java.version", "maven.compiler.release", "maven.compiler.source", "maven.compiler.target"):
        m = re.search(rf"<{tag}>\s*(?:1\.)?(\d+)\s*</{tag}>", text)
        if m:
            return int(m.group(1))
    return None


def _available_jdks():
    """{major version: JAVA_HOME} for usable JDKs, cached per process."""
    global _jdk_cache
    if _jdk_cache is not None:
        return _jdk_cache
    found = {}
    roots = [os.environ["JAVA_HOME"]] if os.environ.get("JAVA_HOME") else []
    for pattern in ("/opt/homebrew/opt/openjdk*", "/opt/homebrew/Cellar/openjdk*/*",
                    "/Library/Java/JavaVirtualMachines/*.jdk", "/usr/lib/jvm/*"):
        roots.extend(glob.glob(pattern))
    for root in sorted(set(roots)):
        home = Path(root) / "Contents/Home" if str(root).endswith(".jdk") else Path(root)
        java = home / "bin/java"
        if not java.exists():
            continue
        try:
            out = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=30)
        except OSError:
            continue
        m = re.search(r'version "(\d+)', out.stderr + out.stdout)
        if m:
            found.setdefault(int(m.group(1)), str(home))
    _jdk_cache = found
    return found


def select_jdk(project, log):
    """JAVA_HOME for a Maven project: exact match, else lowest newer with a warning."""
    required = _pom_java_version(project.root / "pom.xml")
    jdks = _available_jdks()
    if required is None or required in jdks:
        return jdks.get(required) if required else None
    newer = sorted(m for m in jdks if m > required)
    if newer:
        log(f"This project wants Java {required}; using Java {newer[0]} (closest available). "
            f"Some tests may misbehave — install openjdk@{required} for exact results.")
        return jdks[newer[0]]
    have = ", ".join(f"Java {m}" for m in sorted(jdks)) or "none found"
    raise ProjectError(f"This project needs Java {required} (found: {have}). "
                       f"Install it with: brew install openjdk@{required}")


def _has_gradle(root):
    return ((root / "build.gradle").exists() or (root / "build.gradle.kts").exists()
            or (root / "gradlew").exists())


def _needs_android_sdk(root):
    for pat in ("build.gradle", "build.gradle.kts", "*/build.gradle", "*/build.gradle.kts"):
        for f in root.glob(pat):
            try:
                text = f.read_text()
            except OSError:
                continue
            if "com.android.application" in text or "com.android.library" in text:
                return True
    return False


def _android_sdk_present(root):
    if os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"):
        return True
    local_props = root / "local.properties"
    if local_props.is_file() and "sdk.dir" in local_props.read_text():
        return True
    return (Path.home() / "Library/Android/sdk").is_dir()


def _finish_gradle(project, log):
    if (project.root / "gradlew").exists():
        project.gradle_cmd = ["./gradlew"]
    elif shutil.which("gradle"):
        project.gradle_cmd = ["gradle"]
    else:
        raise ProjectError("This project builds with Gradle, but neither its gradlew wrapper nor "
                           "a gradle install was found. Install Gradle (e.g. brew install gradle).")
    project.build = "gradle"
    if _needs_android_sdk(project.root):
        if not _android_sdk_present(project.root):
            raise ProjectError("This is an Android project but no Android SDK was found. Install "
                               "Android Studio (accept the SDK licenses) and set ANDROID_HOME, e.g. "
                               "export ANDROID_HOME=~/Library/Android/sdk")
        project.gradle_tasks = ["testDebugUnitTest"]
    else:
        project.gradle_tasks = ["test"]
    return project


KOTLIN_STDLIB_FALLBACKS = ["2.4.21", "1.9.24"]


@functools.lru_cache(maxsize=8)
def _kotlinc_version(kotlinc):
    """Compiler version ("2.4.21") from `kotlinc -version`, or None."""
    try:
        found = subprocess.run([kotlinc, "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", found.stdout + found.stderr)
    return m.group(1) if m else None


def _download(url, dest):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "BugBait"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())


def _ensure_kotlin_libs(kotlinc, log):
    """JUnit + stdlib jars, downloaded once into the workspace and reused."""
    d = WORKSPACE / "kotlin-libs"
    d.mkdir(parents=True, exist_ok=True)
    urls = {"junit": ["https://repo1.maven.org/maven2/junit/junit/4.13.2/junit-4.13.2.jar"],
            "hamcrest": ["https://repo1.maven.org/maven2/org/hamcrest/hamcrest-core/1.3/"
                         "hamcrest-core-1.3.jar"]}
    versions = [v for v in [_kotlinc_version(kotlinc), *KOTLIN_STDLIB_FALLBACKS] if v]
    urls["stdlib"] = [f"https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-stdlib/{v}/"
                      f"kotlin-stdlib-{v}.jar" for v in versions]
    out = {}
    for key, candidates in urls.items():
        dest = d / f"kotlin-{key}.jar" if key == "stdlib" else d / f"{key}.jar"
        if dest.is_file() and dest.stat().st_size:
            out[key] = str(dest)
            continue
        for url in candidates:
            try:
                log(f"Downloading {dest.name} (one-time setup)...")
                _download(url, dest)
                break
            except (OSError, ValueError):
                if dest.is_file():
                    dest.unlink()
        else:
            raise ProjectError(f"Couldn't download {dest.name}. "
                               "Check your network connection and try again.")
        out[key] = str(dest)
    return out


def _dotnet_target(root):
    """What `dotnet test` should run: the solution, else the test project, else any project."""
    slns = sorted(root.glob("*.sln"))
    if slns:
        return slns[0].name
    projects = sorted(p.relative_to(root) for p in root.rglob("*.csproj"))
    tests = [p for p in projects if "test" in p.stem.lower()]
    if tests:
        return tests[0].as_posix()
    if projects:
        return projects[0].as_posix()
    return ""


def _writable_dotnet_env():
    """NUGET_PACKAGES override when the default cache isn't writable (sandboxes, read-only HOME)."""
    if "NUGET_PACKAGES" in os.environ:
        return {}
    try:
        default = Path.home() / ".nuget" / "packages"
        default.mkdir(parents=True, exist_ok=True)
        probe = default / ".bugbait-write-test"
        probe.write_text("x")
        probe.unlink()
        return {}
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "bugbait-nuget"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return {"NUGET_PACKAGES": str(fallback)}
        except OSError:
            return {}


def _find_phpunit(root):
    """PHPUnit command: project copy first, then PATH. None when missing everywhere."""
    vendor = root / "vendor" / "bin" / "phpunit"
    if vendor.is_file():
        return [str(vendor)]
    phar = root / "phpunit.phar"
    if phar.is_file():
        return ["php", str(phar)]
    path = shutil.which("phpunit")
    if path:
        return [path]
    path = shutil.which("phpunit.phar")
    if path:
        return ["php", path]
    return None


def _node_strips_types(node):
    """True if this Node runs .ts files without flags (22.18+, 23.6+, or 24+)."""
    try:
        found = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15)
        m = re.match(r"v(\d+)\.(\d+)", found.stdout.strip())
        if not m:
            return False
        major, minor = int(m.group(1)), int(m.group(2))
        return major >= 24 or (major == 23 and minor >= 6) or (major == 22 and minor >= 18)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _writable_go_env():
    """GOCACHE/GOPATH overrides when Go's defaults aren't writable (sandboxes, read-only HOME)."""
    out = {}
    try:
        found = subprocess.run(["go", "env", "GOCACHE", "GOPATH"],
                               capture_output=True, text=True, timeout=30)
        values = found.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return out
    for var, val in zip(("GOCACHE", "GOPATH"), values):
        if var in os.environ or not val:
            continue
        try:
            Path(val).mkdir(parents=True, exist_ok=True)
            probe = Path(val) / ".bugbait-write-test"
            probe.write_text("x")
            probe.unlink()
        except OSError:
            fallback = Path(tempfile.gettempdir()) / ("bugbait-" + var.lower())
            try:
                fallback.mkdir(parents=True, exist_ok=True)
                out[var] = str(fallback)
            except OSError:
                pass
    return out


def _finish(project, log):
    project.language = detect(project.root)
    if project.language.id in ("javascript", "typescript"):
        node = shutil.which("node")
        if not node:
            raise ProjectError(f"This is a {project.language.label} project, but Node.js isn't installed. "
                               "Install Node.js 18+ and try again.")
        if project.language.id == "typescript" and not _node_strips_types(node):
            raise ProjectError("This is a TypeScript project, but your Node.js is too old to run "
                               ".ts files directly. Install Node.js 22.18+ and try again.")
        return project  # node --test needs no environment setup
    if project.language.id == "kotlin":
        kotlinc = shutil.which("kotlinc")
        if not kotlinc:
            raise ProjectError("This is a Kotlin project, but kotlinc isn't installed. "
                               "Install Kotlin (https://kotlinlang.org/docs/command-line-compiler.html) "
                               "and try again.")
        java = shutil.which("java")
        if not java:
            raise ProjectError("Kotlin tests run on Java, but Java isn't installed. "
                               "Install a JDK and try again.")
        project.kotlin_cmd = kotlinc
        project.java_bin = java
        project.kotlin_libs = _ensure_kotlin_libs(kotlinc, log)
        return project  # test libraries are downloaded once into the workspace
    if project.language.id == "csharp":
        if not shutil.which("dotnet"):
            raise ProjectError("This is a C# project, but the .NET SDK isn't installed. "
                               "Install it (https://dotnet.microsoft.com/download) and try again.")
        target = _dotnet_target(project.root)
        if not target:
            raise ProjectError("No .sln or .csproj found. BugBait needs a .NET solution or project "
                               "to test C# code.")
        project.dotnet_target = target
        project.dotnet_extra_env = _writable_dotnet_env()
        return project  # dotnet restores its own packages
    if project.language.id == "php":
        php = shutil.which("php")
        if not php:
            raise ProjectError("This is a PHP project, but PHP isn't installed. "
                               "Install PHP 8.1+ and try again.")
        find = _find_phpunit(project.root)
        if not find:
            raise ProjectError("PHPUnit wasn't found. Install it with "
                               "`composer require --dev phpunit/phpunit`, or download phpunit.phar "
                               "and put it on your PATH.")
        project.phpunit_cmd = find
        return project  # composer dependencies are the project's own business
    if project.language.id == "ruby":
        ruby = shutil.which("ruby")
        if not ruby:
            raise ProjectError("This is a Ruby project, but Ruby isn't installed. "
                               "Install Ruby (https://www.ruby-lang.org/) and try again.")
        project.ruby_framework = project.language.detect_framework(project.test_files())
        if project.ruby_framework == "rspec":
            if not shutil.which("rspec"):
                raise ProjectError("This project uses RSpec, but it isn't installed. "
                                   "Run `gem install rspec` and try again.")
        else:
            try:
                has_minitest = subprocess.run(
                    [ruby, "-e", "require 'minitest/autorun'"],
                    capture_output=True, timeout=60).returncode == 0
            except (OSError, subprocess.SubprocessError):
                has_minitest = False
            if not has_minitest:
                raise ProjectError("This project uses minitest, but it isn't installed. "
                                   "Run `gem install minitest` and try again.")
        return project  # system gems; bundler projects aren't installed automatically (yet)
    if project.language.id == "rust":
        if not shutil.which("cargo"):
            raise ProjectError("This is a Rust project, but Rust isn't installed. "
                               "Install Rust (https://rustup.rs/) and try again.")
        if not (project.root / "Cargo.toml").is_file():
            raise ProjectError("No Cargo.toml found. BugBait needs a Cargo project to test Rust code.")
        return project  # cargo manages its own dependencies
    if project.language.id == "go":
        if not shutil.which("go"):
            raise ProjectError("This is a Go project, but Go isn't installed. "
                               "Install Go (https://go.dev/dl/) and try again.")
        if not (project.root / "go.mod").is_file():
            raise ProjectError("No go.mod found. BugBait needs a Go module to test Go code "
                               "(run `go mod init <name>` in the project).")
        project.go_extra_env = _writable_go_env()
        return project  # go test manages its own dependencies
    if project.language.id == "java":
        if (project.root / "pom.xml").is_file():
            if not shutil.which("mvn"):
                raise ProjectError("This is a Java project, but Maven isn't installed. "
                                   "Install Maven (e.g. brew install maven) and try again.")
            project.build = "maven"
            project.java_home = select_jdk(project, log)
            return project  # Maven manages its own dependencies
        modules = sorted(p.parent.name for p in project.root.glob("*/pom.xml"))
        if modules:
            raise ProjectError(f"No pom.xml at the top level. Open one service folder directly: "
                               f"{', '.join(modules)}")
        if _has_gradle(project.root):
            return _finish_gradle(project, log)
        raise ProjectError("No pom.xml found. BugBait needs a Maven project (or Gradle wrapper) "
                           "to test Java code.")
    if project.source == "example":
        project.python = sys.executable
    else:
        project.python = ensure_python(project, log)
    if (project.root / "src").is_dir():
        project.pythonpath = ["src"]
    if project.source != "example":
        project.extra_files = _generated_files(project)
    return project


def _generated_files(project):
    """Find small .py files that installing the project created but the source tree doesn't have.

    Example: setuptools-scm / hatch-vcs write `_version.py` at install time. The sandbox runs the
    source tree, so without this the import fails.
    """
    try:
        purelib = subprocess.run([project.python, "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
                                 capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if not purelib:
        return {}
    extra = {}
    for base in [project.root / p for p in project.pythonpath] + [project.root]:
        if not base.is_dir():
            continue
        for pkg in base.iterdir():
            installed = Path(purelib) / pkg.name
            if not (pkg.is_dir() and (pkg / "__init__.py").exists() and installed.is_dir()):
                continue
            for f in installed.rglob("*.py"):
                rel_in_pkg = f.relative_to(installed)
                if not (pkg / rel_in_pkg).exists() and f.stat().st_size < 20_000:
                    extra[(pkg / rel_in_pkg).relative_to(project.root).as_posix()] = f.read_text()
    return extra


# ---------- per-project Python environment ----------

def _has_pytest(python):
    try:
        return subprocess.run([python, "-c", "import pytest"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _project_venv_python(root: Path):
    for name in (".venv", "venv", "env"):
        for sub in ("bin/python", "Scripts/python.exe"):
            p = root / name / sub
            if p.exists():
                return str(p)
    return None


def ensure_python(project: Project, log=print) -> str:
    """Find or build a Python that can run this project's tests.

    1. If the project has its own virtualenv with pytest, use it.
    2. Otherwise build one in workspace/envs/ and install the project's dependencies there.
    """
    own = _project_venv_python(project.root)
    if own and _has_pytest(own):
        log("Using the project's own Python environment")
        return own

    key = hashlib.sha1(str(project.root).encode()).hexdigest()[:10]
    env_dir = WORKSPACE / "envs" / f"{project.name}-{key}"
    py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    stamp = env_dir / ".bugbait-ready"
    deps = _dependency_files(project.root)
    fingerprint = "\n".join(f"{d}:{(project.root / d).stat().st_mtime}" for d in deps)

    if stamp.exists() and stamp.read_text() == fingerprint and _has_pytest(str(py)):
        return str(py)

    log("Setting up a Python environment for this project (first time only, may take a minute)...")
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(env_dir)], check=True, capture_output=True)
    pip = [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    _pip(pip + ["pytest"], project.root, log, required=True)

    # Install from a temporary copy so the user's folder never gets build/ or .egg-info files.
    with tempfile.TemporaryDirectory(prefix="bugbait-install-") as tmp:
        from runner import _ignore
        copy = Path(tmp) / "project"
        shutil.copytree(project.root, copy, ignore=_ignore, symlinks=True)
        if (project.root / ".git").is_dir():   # version tools (setuptools-scm) read git metadata
            shutil.copytree(project.root / ".git", copy / ".git", symlinks=True)
        for req in deps:
            if req.endswith(".txt"):
                log(f"Installing {req}")
                _pip(pip + ["-r", req], copy, log)
        if any(d in deps for d in ("pyproject.toml", "setup.py", "setup.cfg")):
            log("Installing the project and its dependencies")
            for target in (".[test]", ".[tests]", ".[testing]", ".[dev]", "."):
                if _pip(pip + [target], copy, log, quiet_fail=True):
                    break

    stamp.write_text(fingerprint)
    return str(py)


def _dependency_files(root: Path):
    found = sorted(p.name for p in root.glob("*requirements*.txt"))
    found += [n for n in ("pyproject.toml", "setup.py", "setup.cfg") if (root / n).is_file()]
    if (root / "requirements").is_dir():
        found += [f"requirements/{p.name}" for p in sorted((root / "requirements").glob("*.txt"))]
    return found


def _pip(cmd, cwd, log, required=False, quiet_fail=False):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        reason = next((l for l in reversed(r.stderr.splitlines()) if l.startswith("ERROR")), "")
        if required:
            raise ProjectError("Couldn't install pytest:\n" + r.stderr[-1500:])
        if "requires a different Python" in reason:
            raise ProjectError(f"This project needs a different Python version than BugBait is running on. "
                               f"{reason.replace('ERROR: ', '')}")
        if not quiet_fail:
            log(f"  Some dependencies failed to install; continuing. {reason}")
        return False
    return True
