"""Open a GitHub pull request with BugBait's verified tests.

Uses the GitHub REST API with a token from .env (GITHUB_TOKEN), or from the GitHub CLI (`gh auth login`).
The token needs permission to create branches, write contents and open pull requests:
  - fine-grained token: Contents (read & write) and Pull requests (read & write)
  - classic token: the "public_repo" scope (or "repo" for private repos)

If you can push to the repository, the PR is opened there. If you can't, BugBait forks it and opens the
PR inside your fork, so maintainers of projects you don't own never get surprise pull requests.
"""

import base64
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API = os.getenv("GITHUB_API", "https://api.github.com")
REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


class PRError(RuntimeError):
    pass


def get_token():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        return token
    if shutil.which("gh"):
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    raise PRError("No GitHub token found. Add GITHUB_TOKEN=... to BugBait's .env file "
                  "(or install the GitHub CLI and run `gh auth login`).")


def get_token_optional():
    """get_token, but None instead of raising when no token is configured."""
    try:
        return get_token()
    except PRError:
        return None


ENV_PATH = Path(__file__).parent / ".env"


def save_token(token):
    """Store (or, when empty, remove) GITHUB_TOKEN in BugBait's .env and this process."""
    token = (token or "").strip()
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.is_file() else []
    lines = [l for l in lines if not l.startswith("GITHUB_TOKEN=")]
    if token:
        lines.append(f"GITHUB_TOKEN={token}")
    ENV_PATH.write_text("\n".join(lines) + ("\n" if lines else ""))
    if token:
        os.environ["GITHUB_TOKEN"] = token
    else:
        os.environ.pop("GITHUB_TOKEN", None)


def verify_token(token):
    """Return the GitHub login a token belongs to, or raise PRError."""
    return GitHub(token).call("GET", "/user")["login"]


def list_repos(token, limit=100):
    """The token owner's repos (own, collaborating, org), most recently updated first."""
    repos = GitHub(token).call(
        "GET", "/user/repos",
        params={"per_page": max(1, min(limit, 100)), "sort": "updated",
                "affiliation": "owner,collaborator,organization_member"})
    return [{"full_name": r["full_name"], "private": r["private"],
             "default_branch": r.get("default_branch") or "main",
             "description": r.get("description"), "language": r.get("language"),
             "updated_at": r.get("updated_at")} for r in repos]


def github_repo_for(project_info):
    """(owner, repo) for a project: from its GitHub URL, or a local folder's git remote."""
    url = project_info.get("url")
    if not url and project_info.get("root"):
        cfg = Path(project_info["root"]) / ".git" / "config"
        if cfg.is_file():
            m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfg.read_text())
            url = m.group(1) if m else None
    m = REMOTE_RE.search(url or "")
    return (m.group(1), m.group(2)) if m else None


class GitHub:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "BugBait"})

    def call(self, method, path, ok=(200, 201), **kw):
        try:
            r = self.s.request(method, API + path, timeout=30, **kw)
        except requests.RequestException as e:
            raise PRError(f"GitHub: couldn't reach api.github.com ({e.__class__.__name__}). "
                          f"Check your connection.")
        if r.status_code not in ok:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            if r.status_code == 401:
                msg = "GitHub rejected the token. Check GITHUB_TOKEN in .env."
            elif r.status_code == 403 and "Resource not accessible" in msg:
                msg = "The token doesn't have permission for this. It needs Contents and Pull requests (read & write)."
            raise PRError(f"GitHub: {msg}")
        return r.json() if r.content else {}


def pr_body(report):
    before, after = report["score_before"], report.get("score_after") or report["score_before"]
    pct = lambda s: s["caught"] * 100 // s["total"] if s["total"] else 0
    lines = [
        "## BugBait found gaps in this project's tests",
        "",
        f"BugBait's Saboteur agent planted **{before['total']} realistic bugs** in "
        f"{', '.join(f'`{f}`' for f in report.get('files', []))} and ran the existing "
        f"{report.get('baseline_tests', '')} tests against each one.",
        "",
        f"- Existing tests caught **{before['caught']} of {before['total']}** ({pct(before)}%)",
        f"- With the tests in this PR: **{after['caught']} of {after['total']}** ({pct(after)}%)",
        "",
        "Every test here was verified automatically: it **passes on the current code** and **fails when the bug is planted**.",
        "",
        "| | Where | Bug the tests missed | Why it slipped through |",
        "|---|---|---|---|",
    ]
    for b in report["bugs"]:
        if not b.get("survived_original_tests"):
            continue
        status = {"fixed": "✅ test added", "skipped": "➖ harmless", "failed": "⚠️ no test"}.get(
            (b.get("fix") or {}).get("status"), "")
        line = (b.get("context") or {}).get("line", "")
        change = f"`{b['old'].strip()}` → `{b['new'].strip()}`".replace("|", "\\|")
        why = (b.get("explanation") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {status} | `{b['file']}:{line}` | {change} | {why} |")
    weak = [f for f in (report.get("risk") or {}).get("functions", []) if f["survived"]]
    if weak:
        lines += ["", "### Weakest spots", "", "Where planted bugs most often got past the existing tests:", ""]
        lines += [f"- `{f['file']}` → `{f['function']}`: {f['survived']} of {f['planted']} got through" for f in weak[:5]]
    lines += ["", f"_Generated by [BugBait](https://github.com/AVI2907/bugbait) with {report.get('model') or 'Muse Spark'}._"]
    return "\n".join(lines)


def open_pull_request(report, log=print):
    """Create a branch with the generated test file and open a PR. Returns the PR URL."""
    if not report.get("generated_tests"):
        raise PRError("This run didn't produce any verified tests, so there's nothing to put in a pull request.")
    repo = github_repo_for(report.get("project") or {})
    if not repo:
        raise PRError("This project isn't connected to GitHub (no GitHub link or git remote found).")
    owner, name = repo
    gh = GitHub(get_token())
    me = gh.call("GET", "/user")["login"]
    upstream = gh.call("GET", f"/repos/{owner}/{name}")
    base_branch = upstream["default_branch"]

    if (upstream.get("permissions") or {}).get("push"):
        target_owner = owner
        log(f"Opening the pull request in {owner}/{name}")
    else:
        log(f"You can't push to {owner}/{name}, so BugBait will use your fork ({me}/{name})")
        gh.call("POST", f"/repos/{owner}/{name}/forks", ok=(200, 202))
        for _ in range(30):   # forks are created in the background
            try:
                fork = gh.call("GET", f"/repos/{me}/{name}")
                if fork.get("fork") or fork.get("full_name"):
                    break
            except PRError:
                pass
            time.sleep(2)
        else:
            raise PRError("GitHub is taking too long to create the fork. Try again in a minute.")
        target_owner, name = me, fork["name"]
        base_branch = fork["default_branch"]

    base_sha = gh.call("GET", f"/repos/{target_owner}/{name}/git/ref/heads/{base_branch}")["object"]["sha"]
    branch = f"bugbait/tests-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log(f"Creating branch {branch}")
    gh.call("POST", f"/repos/{target_owner}/{name}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})

    path = report.get("test_path") or "test_bugbait_generated.py"
    existing = gh.call("GET", f"/repos/{target_owner}/{name}/contents/{path}", ok=(200, 404),
                       params={"ref": branch})
    added = sum(1 for b in report["bugs"] if (b.get("fix") or {}).get("status") == "fixed")
    log(f"Adding {path}")
    gh.call("PUT", f"/repos/{target_owner}/{name}/contents/{path}", json={
        "message": f"Add {added} BugBait test{'s' if added != 1 else ''} for gaps in the test suite",
        "content": base64.b64encode(report["generated_tests"].encode()).decode(),
        "branch": branch,
        **({"sha": existing["sha"]} if existing.get("sha") else {}),
    })

    before, after = report["score_before"], report.get("score_after") or report["score_before"]
    log("Opening the pull request")
    pr = gh.call("POST", f"/repos/{target_owner}/{name}/pulls", json={
        "title": f"Add {added} tests that catch bugs the current suite misses "
                 f"({before['caught']}/{before['total']} → {after['caught']}/{after['total']})",
        "head": branch, "base": base_branch, "body": pr_body(report),
    })
    return pr["html_url"]


if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Open a pull request with BugBait's verified tests.")
    parser.add_argument("name", help="project name as shown in results/ (e.g. un33k-python-slugify)")
    args = parser.parse_args()
    path = Path(__file__).parent / "results" / args.name / "latest.json"
    if not path.is_file():
        sys.exit(f"No saved run for '{args.name}'. Run BugBait on it first.")
    try:
        print(open_pull_request(json.loads(path.read_text())))
    except PRError as e:
        sys.exit(str(e))
