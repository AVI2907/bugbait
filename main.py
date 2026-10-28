"""BugBait: plant bugs in a project and see which ones its tests miss, then close the gaps.

Usage:
    python main.py                              # attack the default target (calculator)
    python main.py --target inflection          # attack a real open-source library
    python main.py --target inflection --bugs 8 # ask the Saboteur for 8 bugs per file
    python main.py --files __init__.py          # only attack files whose path contains this
    python main.py --no-fix                     # only measure, don't run the Fixer agent
    python main.py --offline                    # built-in bugs, no API key needed (calculator only)
"""

import argparse
import ast
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from llm import DEFAULT_MODEL
from runner import ROOT, Sandbox

GENERATED_NAME = "test_bugbait_generated.py"

OFFLINE_BUGS = {
    "calculator": [
        {"file": "calc.py", "old": "a + b", "new": "a - b", "explanation": "basic arithmetic"},
        {"file": "calc.py", "old": "age >= 18", "new": "age > 18", "explanation": "boundary at 18"},
        {"file": "calc.py", "old": "percent > 100", "new": "percent >= 100", "explanation": "boundary at 100"},
    ],
}


# ---------- helpers ----------

def pct(n, total):
    return n * 100 // total if total else 0


def short(bug):
    return f"{bug['file']}: {bug['old'].strip()!r} -> {bug['new'].strip()!r}"


def target_dir(target):
    return ROOT / "targets" / target


def target_files(target, only=None):
    """Split a target into source files and test files (paths relative to the target)."""
    base = target_dir(target)
    files = sorted(base.rglob("*.py"))
    is_test = lambda f: f.name.startswith("test_") or f.name.endswith("_test.py") or "tests" in f.parts
    tests = [f for f in files if is_test(f)]
    sources = [f for f in files if f not in tests and f.stat().st_size > 0]
    if only:
        sources = [f for f in sources if any(o in f.relative_to(base).as_posix() for o in only)]
    return sources, tests


def tests_text(tests, target):
    base = target_dir(target)
    return "\n\n".join(f"# {t.relative_to(base).as_posix()}\n{t.read_text()}" for t in tests)


def validate(bug, target):
    """Reject bugs we can't apply cleanly or that break Python syntax. Returns a reason or None."""
    path = target_dir(target) / bug["file"]
    if not path.is_file():
        return "file not found"
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


# ---------- stage 1: Saboteur ----------

def get_bugs(target, offline, count, only, workers):
    if offline:
        if target not in OFFLINE_BUGS:
            raise RuntimeError(f"--offline only has built-in bugs for: {', '.join(OFFLINE_BUGS)}")
        return [dict(b) for b in OFFLINE_BUGS[target]]

    from agents.saboteur import generate_bugs

    sources, tests = target_files(target, only)
    if not sources:
        raise RuntimeError("No source files matched.")
    ttext = tests_text(tests, target)
    base = target_dir(target)

    def study(src):
        rel = src.relative_to(base).as_posix()
        print(f"Saboteur is studying {rel}...")
        return generate_bugs(rel, src.read_text(), ttext, count)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [bug for found in pool.map(study, sources) for bug in found]


# ---------- stage 2: run the bugs ----------

def bug_survives(target, bug, extra_tests=None):
    with Sandbox(target) as sb:
        sb.apply_mutation(bug["file"], bug["old"], bug["new"])
        if extra_tests:
            sb.add_file(GENERATED_NAME, extra_tests)
        passed, _ = sb.run_tests()
    return passed


def run_bugs(target, bugs, workers, extra_tests=None):
    """Plant each bug in its own sandbox (in parallel). Returns a list of True (survived) / False."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda b: bug_survives(target, b, extra_tests), bugs))


# ---------- stage 3: Fixer ----------

def run_fixer(target, bugs, survivors, workers):
    from agents.fixer import write_test

    _, tests = target_files(target)
    ttext = tests_text(tests, target)

    def fix(bug):
        source = (target_dir(target) / bug["file"]).read_text()
        try:
            return write_test(target, bug, source, ttext)
        except Exception as e:
            return {"status": "failed", "test_code": None, "reason": str(e)[:200], "attempts": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fix, survivors))

    accepted = []
    for bug, result in zip(survivors, results):
        bug["fix"] = result
        if result["status"] == "fixed":
            tries = f" (after {result['attempts']} tries)" if result["attempts"] > 1 else ""
            print(f"  [+] verified test added{tries}: {short(bug)}")
            accepted.append((bug["id"], bug, result["test_code"]))
        elif result["status"] == "skipped":
            print(f"  [-] skipped, not a real behavior change: {short(bug)}\n        {result['reason']}")
        else:
            print(f"  [x] could not write a working test: {short(bug)}\n        {result['reason']}")
    return accepted


def build_test_file(accepted):
    """Merge verified tests into one file, renaming functions so they can't collide."""
    imports, bodies = [], []
    for i, bug, code in accepted:
        body = []
        for line in code.splitlines():
            if re.match(r"^(import |from \S+ import )", line):
                if line not in imports:
                    imports.append(line)
            else:
                body.append(line)
        text = re.sub(r"\bdef test_", f"def test_bug{i}_", "\n".join(body).strip())
        header = f"# Catches bug {i}: {bug['file']}: {bug['old'].strip()} -> {bug['new'].strip()}"
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


# ---------- report ----------

def save_report(report):
    out_dir = ROOT / "results" / report["target"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    text = json.dumps(report, indent=2)
    (out_dir / f"run-{stamp}.json").write_text(text)
    (out_dir / "latest.json").write_text(text)
    return f"results/{report['target']}/run-{stamp}.json"


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description="BugBait: test your tests.")
    parser.add_argument("--target", default="calculator", help="folder name inside targets/")
    parser.add_argument("--files", nargs="*", help="only attack source files whose path contains one of these")
    parser.add_argument("--bugs", type=int, default=5, help="bugs to request per source file")
    parser.add_argument("--workers", type=int, default=4, help="how many things to run in parallel")
    parser.add_argument("--no-fix", action="store_true", help="skip the Fixer agent")
    parser.add_argument("--offline", action="store_true", help="use built-in bugs, no API call")
    args = parser.parse_args()
    started = time.time()

    if not target_dir(args.target).is_dir():
        print(f"No target named '{args.target}'. Available: "
              + ", ".join(sorted(p.name for p in (ROOT / "targets").iterdir() if p.is_dir())))
        return

    print(f"BugBait target: {args.target}")
    with Sandbox(args.target) as sb:
        passed, output = sb.run_tests()
    if not passed:
        print("Baseline tests fail. Fix the target first.\n")
        print(output[-3000:])
        return
    print("Baseline tests pass\n")

    try:
        candidates = get_bugs(args.target, args.offline, args.bugs, args.files, args.workers)
    except Exception as e:  # missing key, network or API errors
        print(f"Saboteur failed: {e}")
        return

    bugs = []
    for bug in candidates:
        reason = validate(bug, args.target)
        if reason:
            print(f"  skipped: {short(bug)} ({reason})")
        else:
            bug["id"] = len(bugs) + 1
            bugs.append(bug)
    if not bugs:
        print("No valid bugs to test.")
        return

    print(f"\nTesting {len(bugs)} planted bugs:\n")
    for bug, survived in zip(bugs, run_bugs(args.target, bugs, args.workers)):
        bug["survived_original_tests"] = survived
        label = "SURVIVED" if survived else "caught  "
        print(f"Bug {bug['id']:>2} {label} {short(bug)}")
        if survived and bug.get("explanation"):
            print(f"            why it slipped: {bug['explanation']}")
    survivors = [b for b in bugs if b["survived_original_tests"]]
    caught_before = len(bugs) - len(survivors)
    print(f"\nScore: {caught_before}/{len(bugs)} bugs caught ({pct(caught_before, len(bugs))}%)")

    report = {
        "target": args.target,
        "model": None if args.offline else DEFAULT_MODEL,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "bugs": bugs,
        "score_before": {"caught": caught_before, "total": len(bugs)},
        "score_after": None,
        "generated_tests": None,
    }

    if survivors and not args.no_fix and not args.offline:
        print(f"\nFixer is writing tests for {len(survivors)} surviving bugs...\n")
        accepted = run_fixer(args.target, bugs, survivors, args.workers)
        skipped = sum(1 for b in survivors if b["fix"]["status"] == "skipped")
        if accepted:
            test_file = build_test_file(accepted)
            out_dir = ROOT / "generated_tests" / args.target
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / GENERATED_NAME).write_text(test_file)

            after = run_bugs(args.target, bugs, args.workers, extra_tests=test_file)
            for bug, survived in zip(bugs, after):
                bug["survived_with_new_tests"] = survived
            caught_after = len(bugs) - sum(after)
            report["score_after"] = {"caught": caught_after, "total": len(bugs)}
            report["generated_tests"] = test_file
            print(f"\nTests added: {len(accepted)}  |  Skipped as harmless: {skipped}")
            print(f"Score: {pct(caught_before, len(bugs))}% -> {pct(caught_after, len(bugs))}% "
                  f"({caught_before}/{len(bugs)} -> {caught_after}/{len(bugs)} bugs caught)")
            print(f"New tests saved to generated_tests/{args.target}/{GENERATED_NAME}")
        else:
            print("\nNo verified tests were produced.")

    report["seconds"] = round(time.time() - started, 1)
    print(f"Report saved to {save_report(report)}  ({report['seconds']}s)")


if __name__ == "__main__":
    main()
