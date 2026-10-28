"""BugBait: plant bugs in a project and see which ones its tests miss.

Usage:
    python main.py              # Saboteur agent (Muse Spark) invents the bugs
    python main.py --offline    # use the built-in hardcoded bugs (no API key needed)
    python main.py --bugs 8     # ask the Saboteur for 8 bugs
    python main.py --no-fix     # only measure, don't run the Fixer agent
"""

import argparse
import ast
import re

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


def pct(n, total):
    return n * 100 // total if total else 0


def run_bugs(target, bugs, extra_tests=None, verbose=False):
    """Plant each bug in a fresh sandbox and run the tests. Returns the bugs that survived."""
    survivors = []
    for i, bug in enumerate(bugs, 1):
        with Sandbox(target) as sb:
            sb.apply_mutation(bug["file"], bug["old"], bug["new"])
            if extra_tests:
                sb.add_file(GENERATED_NAME, extra_tests)
            passed, _ = sb.run_tests()
        if passed:
            survivors.append(bug)
        if verbose:
            label = "SURVIVED" if passed else "caught  "
            print(f"Bug {i} {label} {bug['file']}: {bug['old'].strip()!r} -> {bug['new'].strip()!r}")
            if passed and bug.get("explanation"):
                print(f"           why it slipped: {bug['explanation']}")
    return survivors


GENERATED_NAME = "test_bugbait_generated.py"


def run_fixer(target, bugs, survivors, caught_before):
    from agents.fixer import write_test

    sources, tests = target_files(target)
    tests_text = "\n\n".join(f"# {t.name}\n{t.read_text()}" for t in tests)
    print(f"\nFixer is writing tests for {len(survivors)} surviving bugs...\n")

    accepted, skipped = [], 0
    for i, bug in enumerate(survivors, 1):
        source = (ROOT / "targets" / target / bug["file"]).read_text()
        try:
            result = write_test(target, bug, source, tests_text)
        except Exception as e:
            result = {"status": "failed", "reason": str(e)[:200], "attempts": 0}
        name = f"{bug['old'].strip()!r} -> {bug['new'].strip()!r}"
        if result["status"] == "fixed":
            tries = f" (after {result['attempts']} tries)" if result["attempts"] > 1 else ""
            print(f"  [+] verified test added{tries}: {name}")
            accepted.append((bugs.index(bug) + 1, bug, result["test_code"]))
        elif result["status"] == "skipped":
            skipped += 1
            print(f"  [-] skipped, not a real behavior change: {name}\n        {result['reason']}")
        else:
            print(f"  [x] could not write a working test: {name}\n        {result['reason']}")

    if not accepted:
        print("\nNo verified tests were produced.")
        return

    test_file = build_test_file(accepted)
    out_dir = ROOT / "generated_tests" / target
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / GENERATED_NAME).write_text(test_file)

    still_alive = run_bugs(target, bugs, extra_tests=test_file)
    caught_after = len(bugs) - len(still_alive)
    print(f"\nTests added: {len(accepted)}  |  Skipped as harmless: {skipped}")
    print(f"Score: {pct(caught_before, len(bugs))}% -> {pct(caught_after, len(bugs))}% "
          f"({caught_before}/{len(bugs)} -> {caught_after}/{len(bugs)} bugs caught)")
    print(f"New tests saved to generated_tests/{target}/{GENERATED_NAME}")


def build_test_file(accepted):
    """Merge verified tests into one file, renaming functions so they can't collide."""
    imports, bodies = [], []
    for i, bug, code in accepted:
        lines = code.splitlines()
        body = []
        for line in lines:
            if re.match(r"^(import |from \S+ import )", line):
                if line not in imports:
                    imports.append(line)
            else:
                body.append(line)
        text = "\n".join(body).strip()
        text = re.sub(r"\bdef test_", f"def test_bug{i}_", text)
        header = f"# Catches: {bug['old'].strip()} -> {bug['new'].strip()}"
        bodies.append(f"{header}\n{text}")
    head = '"""Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug."""\n\n'
    return head + "\n".join(merge_imports(imports)) + "\n\n\n" + "\n\n\n".join(bodies) + "\n"


def merge_imports(lines):
    """Combine 'from x import a' and 'from x import b' into 'from x import a, b'."""
    plain, from_names = [], {}
    for line in lines:
        m = re.match(r"^from (\S+) import (.+)$", line)
        if m and "(" not in line:
            names = from_names.setdefault(m.group(1), [])
            for name in (n.strip() for n in m.group(2).split(",")):
                if name not in names:
                    names.append(name)
        elif line not in plain:
            plain.append(line)
    return sorted(plain) + [f"from {mod} import {', '.join(names)}" for mod, names in from_names.items()]


def main():
    parser = argparse.ArgumentParser(description="BugBait: test your tests.")
    parser.add_argument("--target", default="calculator")
    parser.add_argument("--offline", action="store_true", help="use hardcoded bugs, no API call")
    parser.add_argument("--bugs", type=int, default=5, help="bugs to request per source file")
    parser.add_argument("--no-fix", action="store_true", help="skip the Fixer agent")
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
    survivors = run_bugs(args.target, bugs, verbose=True)
    caught = len(bugs) - len(survivors)
    print(f"\nScore: {caught}/{len(bugs)} bugs caught ({pct(caught, len(bugs))}%)")

    if not survivors or args.no_fix or args.offline:
        return
    run_fixer(args.target, bugs, survivors, caught)


if __name__ == "__main__":
    main()
