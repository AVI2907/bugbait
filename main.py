"""BugBait command line: plant bugs, see which ones the tests miss, then close the gaps.

Usage:
    python main.py inflection                         # a built-in example
    python main.py ~/code/my-project                  # any folder on your computer
    python main.py https://github.com/owner/repo      # any public GitHub repo (or just owner/repo)
    python main.py inflection --bugs 8                # ask the Saboteur for 8 bugs per file
    python main.py ~/code/app --files app/models.py   # choose which files to attack
    python main.py ~/code/app --list-files            # show the files BugBait could attack
    python main.py inflection --no-fix                # only measure, don't run the Fixer agent
    python main.py calculator --offline               # built-in bugs, no API key needed
    python main.py inflection --json                  # machine-readable events (used by VS Code)
    python main.py . --staged --no-fix                # only test files with staged git changes
    python main.py . --fail-under 70                  # exit 1 unless 70%+ of bugs are caught
    python main.py --install-hook ~/code/my-project   # block weak commits automatically

For the web dashboard, run:  python app.py
"""

import argparse
import json
import sys

import engine


def pct(n, total):
    return n * 100 // total if total else 0


def short(bug):
    return f"{bug['file']}: {bug['old'].strip()!r} -> {bug['new'].strip()!r}"


def make_printer():
    bugs = {}

    def emit(event, **d):
        if event == "start":
            p = d["project"]
            where = p["url"] or p["root"]
            print(f"BugBait project: {p['name']}  ({where})")
        elif event == "setup":
            print(d["message"])
        elif event == "baseline":
            print(f"Baseline: {d['tests']} tests pass" if d["passed"]
                  else f"The project's tests can't run cleanly yet:\n\n{d['output']}")
        elif event == "files":
            print(f"Attacking: {', '.join(d['files'])}\n")
        elif event == "saboteur":
            print(f"Saboteur is studying {d['file']}...")
        elif event == "skipped":
            print(f"  skipped: {short(d['bug'])} ({d['reason']})")
        elif event == "bugs":
            bugs.update({b["id"]: b for b in d["bugs"]})
            print(f"Testing {len(bugs)} planted bugs:\n")
        elif event == "bug_result":
            b = bugs[d["id"]]
            tag = f"  [{b['kind']}, realism {b['realism']}/5]" if b.get("kind") else ""
            print(f"Bug {b['id']:>2} {'SURVIVED' if d['survived'] else 'caught  '} {short(b)}{tag}")
            if d["survived"] and b.get("explanation"):
                print(f"            why it slipped: {b['explanation']}")
        elif event == "score_before":
            print(f"\nScore: {d['caught']}/{d['total']} bugs caught ({pct(d['caught'], d['total'])}%)")
        elif event == "risk":
            weak = [f for f in d["functions"] if f["survived"]]
            if weak:
                print("\nWeakest spots (where bugs got through):")
                for f in weak[:5]:
                    print(f"  {f['survived']}/{f['planted']} got through  {f['file']}  {f['function']}")
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
            print(f"New tests saved to {d['saved_to']}  (belongs at {d['test_path']} in the project)")
        elif event == "done":
            print(f"Report saved to {d['report_path']}  ({d['seconds']}s)")
        elif event == "error":
            print(d["message"])

    return emit


def json_emit(event, **data):
    print(json.dumps({"event": event, **data}, default=str), flush=True)


def gate(report, threshold):
    """Did the run catch enough bugs? Returns (passed, percent)."""
    s = report["score_before"]
    caught_pct = pct(s["caught"], s["total"])
    return caught_pct >= threshold, caught_pct


def main():
    parser = argparse.ArgumentParser(description="BugBait: test your tests.")
    parser.add_argument("project", nargs="?", default=None,
                        help="example name, folder path, or GitHub link (default: calculator, or . with --staged)")
    parser.add_argument("--target", help=argparse.SUPPRESS)  # old name for the project argument
    parser.add_argument("--files", nargs="*", help="source files to attack (paths relative to the project)")
    parser.add_argument("--list-files", action="store_true", help="list the files BugBait could attack, then exit")
    parser.add_argument("--bugs", type=int, default=5, help="bugs to request per source file")
    parser.add_argument("--workers", type=int, default=4, help="how many things to run in parallel")
    parser.add_argument("--no-fix", action="store_true", help="skip the Fixer agent")
    parser.add_argument("--min-realism", type=int, default=3, choices=range(1, 6), metavar="1-5",
                        help="drop bugs the Saboteur rates as less realistic than this (default 3)")
    parser.add_argument("--offline", action="store_true", help="use built-in bugs, no API call")
    parser.add_argument("--refresh", action="store_true", help="download a GitHub repo again")
    parser.add_argument("--json", action="store_true", help="print one JSON event per line")
    parser.add_argument("--staged", action="store_true", help="only test files with staged git changes")
    parser.add_argument("--fail-under", type=int, default=None, metavar="0-100",
                        help="exit 1 unless at least this percent of planted bugs are caught")
    parser.add_argument("--install-hook", metavar="REPO",
                        help="install a pre-commit hook in REPO that blocks weak commits, then exit")
    args = parser.parse_args()
    spec = args.target or args.project
    emit = json_emit if args.json else make_printer()

    if args.install_hook:
        from precommit import install_hook
        try:
            hook = install_hook(args.install_hook, args.fail_under or 70)
        except ValueError as e:
            emit("error", message=str(e))
            sys.exit(1)
        if args.json:
            emit("hook_installed", hook=hook)
        else:
            print(f"Pre-commit hook installed at {hook}\n"
                  f"Commits now need {args.fail_under or 70}%+ of planted bugs caught to go through.")
        return
    if args.fail_under is not None and not 0 <= args.fail_under <= 100:
        parser.error("--fail-under must be between 0 and 100")
    if args.staged and args.files:
        parser.error("can't combine --staged with --files")
    if spec is None:
        spec = "." if args.staged else "calculator"

    if args.list_files:
        from projects import ProjectError, resolve
        try:
            project = resolve(spec, log=lambda m: emit("setup", message=m), refresh=args.refresh)
        except ProjectError as e:
            emit("error", message=str(e))
            sys.exit(1)
        files = project.source_files()
        if args.json:
            emit("file_list", project=project.name, files=files, suggested=project.default_files())
        else:
            for f in files:
                print(f"{f['lines']:>6} lines  {f['path']}")
        return

    if args.staged:
        from projects import ProjectError, resolve
        try:
            project = resolve(spec, log=lambda m: emit("setup", message=m), refresh=args.refresh)
        except ProjectError as e:
            emit("error", message=str(e))
            sys.exit(1)
        staged, err = engine.staged_source_files(project)
        if err:
            emit("error", message=f"--staged: {err}.")
            sys.exit(1)
        if not staged:
            emit("setup", message="No staged source files to test — nothing to do.")
            return
        spec = project
        args.files = staged

    report = engine.run(spec, emit, files=args.files, bugs_per_file=args.bugs, workers=args.workers,
                        fix=not args.no_fix, offline=args.offline, refresh=args.refresh,
                        min_realism=args.min_realism)
    if not report:
        sys.exit(1)
    if args.fail_under is not None:
        ok, caught_pct = gate(report, args.fail_under)
        if args.json:
            emit("gate", passed=ok, threshold=args.fail_under,
                 caught=report["score_before"]["caught"], total=report["score_before"]["total"])
        else:
            print(f"BugBait gate: {caught_pct}% caught (needs {args.fail_under}%) — "
                  f"{'PASS' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
