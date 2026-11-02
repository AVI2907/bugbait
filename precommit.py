"""Install BugBait as a git pre-commit hook, so weak commits get blocked automatically."""

import shlex
import subprocess
import sys
from pathlib import Path

MARKER = "Installed by BugBait"


def hook_command(repo, threshold):
    """The shell line the hook runs: test staged files, block the commit under the threshold."""
    main = Path(__file__).resolve().parent / "main.py"
    parts = [sys.executable, str(main), "--staged", "--no-fix",
             "--fail-under", str(threshold), str(Path(repo).resolve())]
    return "exec " + " ".join(shlex.quote(p) for p in parts)


def install_hook(repo, threshold=70):
    """Write a pre-commit hook into REPO's git dir. Returns the hook path.

    Raises ValueError with a plain-English reason when it can't install.
    """
    root = Path(repo).expanduser()
    if not root.is_dir():
        raise ValueError(f"Not a folder: {repo}")
    try:
        found = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "hooks"],
                               capture_output=True, text=True, timeout=15)
    except OSError:
        raise ValueError("Couldn't run git — is it installed?")
    if found.returncode:
        raise ValueError(f"{repo} is not a git repository.")
    hooks = Path(found.stdout.strip())
    if not hooks.is_absolute():
        hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    if hook.exists() and MARKER not in hook.read_text():
        raise ValueError(f"{repo} already has a pre-commit hook. Add this line to it so both run:\n"
                         f"{hook_command(root, threshold)}")
    hook.write_text(f"#!/bin/sh\n# {MARKER}: blocks commits when tests miss too many planted bugs.\n"
                    f"# Delete this file to uninstall.\n{hook_command(root, threshold)}\n")
    hook.chmod(0o755)
    return str(hook)
