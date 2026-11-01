"""Where BugBait's projects come from, and how to run their tests.

A project can be:
  - a built-in example in targets/            e.g.  "inflection"
  - any folder on this computer               e.g.  "/Users/me/code/myapp"
  - a GitHub repository                       e.g.  "https://github.com/owner/repo" or "owner/repo"

BugBait never edits the original project. Every trial runs on a temporary copy.
"""

import base64
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

# Folders that are never copied into a sandbox or scanned for source files
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    "node_modules", "venv", ".venv", "env", ".env", "build", "dist", ".eggs", "site-packages",
    ".idea", ".vscode", "htmlcov", ".ipynb_checkpoints",
}
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
    python: str = sys.executable
    pythonpath: list = field(default_factory=list)   # relative paths to put first on sys.path
    pytest_args: list = field(default_factory=list)  # e.g. --deselect for tests that already fail
    extra_files: dict = field(default_factory=dict)  # build-generated files (e.g. _version.py) to add to sandboxes

    # ----- files -----
    def is_test_file(self, rel: Path) -> bool:
        return (rel.name.startswith("test_") or rel.name.endswith("_test.py") or rel.name in ("test.py", "tests.py")
                or any(p in ("tests", "test", "testing") for p in rel.parts[:-1]))

    def python_files(self):
        """All .py files in the project (relative paths), skipping venvs, caches and build output."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for f in sorted(filenames):
                if f.endswith(".py"):
                    out.append((Path(dirpath) / f).relative_to(self.root))
        return out

    def test_files(self):
        return [p for p in self.python_files() if self.is_test_file(p)]

    def source_files(self):
        """Files worth attacking: real code, not tests, docs, examples or empty __init__ files."""
        out = []
        for rel in self.python_files():
            if self.is_test_file(rel) or rel.name in NOT_SOURCE_FILES:
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
        return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(paths + ([old] if old else []))}


# ---------- finding / creating projects ----------

def list_examples():
    return sorted(p.name for p in (ROOT / "targets").iterdir() if p.is_dir())


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


def _finish(project, log):
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
