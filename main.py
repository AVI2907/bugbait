"""BugBait command line: plant bugs, see which ones the tests miss, then close the gaps.

Usage:
    python main.py                              # attack the default target (calculator)
    python main.py --target inflection          # attack a real open-source library
    python main.py --target inflection --bugs 8 # ask the Saboteur for 8 bugs per file
    python main.py --files __init__.py          # only attack files whose path contains this
    python main.py --no-fix                     # only measure, don't run the Fixer agent
    python main.py --offline                    # built-in bugs, no API key needed (calculator only)

For the web dashboard, run:  python app.py
"""

import argparse

import engine


def pct(n, total):
    return n * 100 // total if total else 0


def short(bug):
    return f"{bug['file']}: {bug['old'].strip()!r} -> {bug['new'].strip()!r}"


def make_printer():
    bugs = {}

    def emit(event, **d):
        if event == "start":
            print(f"BugBait target: {d['target']}")
        elif event == "baseline":
            print(f"Baseline: {d['tests']} tests pass\n" if d["passed"]
                  else f"Baseline tests fail. Fix the target first.\n\n{d['output']}")
        elif event == "saboteur":
            print(f"Saboteur is studying {d['file']}...")
        elif event == "skipped":
            print(f"  skipped: {short(d['bug'])} ({d['reason']})")
        elif event == "bugs":
            bugs.update({b["id"]: b for b in d["bugs"]})
            print(f"Testing {len(bugs)} planted bugs:\n")
        elif event == "bug_result":
            b = bugs[d["id"]]
            print(f"Bug {b['id']:>2} {'SURVIVED' if d['survived'] else 'caught  '} {short(b)}")
            if d["survived"] and b.get("explanation"):
                print(f"            why it slipped: {b['explanation']}")
        elif event == "score_before":
            print(f"\nScore: {d['caught']}/{d['total']} bugs caught ({pct(d['caught'], d['total'])}%)")
        elif event == "fixer_start":
            print(f"\nFixer is writing tests for {d['count']} surviving bugs...\n")
        elif event == "fix_result":
            b = bugs[d["id"]]
            if d["status"] == "fixed":
                tries = f" (after {d['attempts']} tries)" if d["attempts"] > 1 else ""
                print(f"  [+] verified test added{tries}: {short(b)}")
            elif d["status"] == "skipped":
                print(f"  [-] skipped, not a real behavior change: {short(b)}\n        {d['reason']}")
            else:
                print(f"  [x] could not write a working test: {short(b)}\n        {d['reason']}")
        elif event == "score_after":
            before = sum(1 for b in bugs.values() if not b.get("survived_original_tests"))
            print(f"\nTests added: {d['tests_added']}")
            print(f"Score: {pct(before, d['total'])}% -> {pct(d['caught'], d['total'])}% "
                  f"({before}/{d['total']} -> {d['caught']}/{d['total']} bugs caught)")
            print(f"New tests saved to {d['saved_to']}")
        elif event == "done":
            print(f"Report saved to {d['report_path']}  ({d['seconds']}s)")
        elif event == "error":
            print(d["message"])

    return emit


def main():
    parser = argparse.ArgumentParser(description="BugBait: test your tests.")
    parser.add_argument("--target", default="calculator", help="folder name inside targets/")
    parser.add_argument("--files", nargs="*", help="only attack source files whose path contains one of these")
    parser.add_argument("--bugs", type=int, default=5, help="bugs to request per source file")
    parser.add_argument("--workers", type=int, default=4, help="how many things to run in parallel")
    parser.add_argument("--no-fix", action="store_true", help="skip the Fixer agent")
    parser.add_argument("--offline", action="store_true", help="use built-in bugs, no API call")
    args = parser.parse_args()

    engine.run(args.target, make_printer(), bugs_per_file=args.bugs, files=args.files,
               workers=args.workers, fix=not args.no_fix, offline=args.offline)


if __name__ == "__main__":
    main()
