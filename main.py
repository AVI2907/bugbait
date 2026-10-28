"""BugBait: plant bugs in a project and see which ones its tests miss.

Usage:
    python main.py              # Saboteur agent (Muse Spark) invents the bugs
    python main.py --offline    # use the built-in hardcoded bugs (no API key needed)
    python main.py --bugs 8     # ask the Saboteur for 8 bugs
"""

import argparse
import ast
from pathlib import Path

from runner import ROOT, Sandbox

OFFLINE_BUGS = [
    {"file": "calc.py", "old": "a + b", "new": "a - b", "explanation": "basic arithmetic"},
    {"file": "calc.py", "old": "age >= 18", "new": "age > 18", "explanation": "boundary at 18"},
    {"file": "calc.py", "old": "percent > 100", "new": "percent >= 100", "explanation": "boundary at 100"},
]


def target_files(target):
    """Split a target into source files and test files."""
    files = sorted((ROOT / "targets" / target).rglob("*.py"))
    tests = [f for f in files if f.name.startswith("test_") or f.name.endswith("_test.py")]
    sources = [f for f in files if f not in tests]
    return sources, tests


def validate(bug, target):
    """Reject bugs we can't apply cleanly or that break Python syntax. Returns a reason or None."""
    path = ROOT / "targets" / target / bug["file"]
    code = path.read_text()
    hits = code.count(bug["old"])
    if hits == 0:
        return "'old' text not found in file"
    if hits > 1:
        return "'old' text is ambiguous (appears more than once)"
    if bug["old"] == bug["new"]:
        return "no change"
    try:
        ast.parse(code.replace(bug["old"], bug["new"], 1))
    except SyntaxError:
        return "mutation is not valid Python"
    return None


def get_bugs(target, offline, count):
    if offline:
        return OFFLINE_BUGS

    from agents.saboteur import generate_bugs

    sources, tests = target_files(target)
    tests_text = "\n\n".join(f"# {t.name}\n{t.read_text()}" for t in tests)
    bugs = []
    for src in sources:
        rel = src.relative_to(ROOT / "targets" / target).as_posix()
        print(f"Saboteur is studying {rel}...")
        bugs += generate_bugs(rel, src.read_text(), tests_text, count)
    return bugs


def main():
    parser = argparse.ArgumentParser(description="BugBait: test your tests.")
    parser.add_argument("--target", default="calculator")
    parser.add_argument("--offline", action="store_true", help="use hardcoded bugs, no API call")
    parser.add_argument("--bugs", type=int, default=5, help="bugs to request per source file")
    args = parser.parse_args()

    with Sandbox(args.target) as sb:
        passed, output = sb.run_tests()
    if not passed:
        print("Baseline tests fail. Fix the target first.\n")
        print(output)
        return
    print("Baseline tests pass")

    try:
        candidates = get_bugs(args.target, args.offline, args.bugs)
    except Exception as e:  # missing key, network or API errors
        print(f"Saboteur failed: {e}\nTip: run with --offline to use the built-in bugs.")
        return
    bugs = []
    for bug in candidates:
        reason = validate(bug, args.target)
        if reason:
            print(f"  skipped: {bug['old']!r} -> {bug['new']!r} ({reason})")
        else:
            bugs.append(bug)
    if not bugs:
        print("No valid bugs to test.")
        return
    print(f"\nTesting {len(bugs)} planted bugs:\n")

    survivors = []
    for i, bug in enumerate(bugs, 1):
        with Sandbox(args.target) as sb:
            sb.apply_mutation(bug["file"], bug["old"], bug["new"])
            passed, _ = sb.run_tests()
        label = "SURVIVED" if passed else "caught  "
        print(f"Bug {i} {label} {bug['file']}: {bug['old']!r} -> {bug['new']!r}")
        if passed:
            survivors.append(bug)
            if bug.get("explanation"):
                print(f"           why it slipped: {bug['explanation']}")

    caught = len(bugs) - len(survivors)
    print(f"\nScore: {caught}/{len(bugs)} bugs caught ({caught * 100 // len(bugs)}%)")


if __name__ == "__main__":
    main()
