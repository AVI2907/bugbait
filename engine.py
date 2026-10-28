"""BugBait engine: the full Saboteur -> run -> Fixer -> rescore pipeline.

Progress is reported through an `emit(event, **data)` callback, so the same engine
drives both the terminal (main.py) and the web dashboard (app.py).
"""

import ast
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# ---------- target helpers ----------

def target_dir(target):
    return ROOT / "targets" / target


def list_targets():
    return sorted(p.name for p in (ROOT / "targets").iterdir() if p.is_dir())


def target_files(target, only=None):
    """Split a target into source files and test files."""
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


def bug_context(bug, target, radius=3):
    """Line number and surrounding lines of a bug, for display."""
    code = (target_dir(target) / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    lines = code.splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + bug["old"].count("\n") + radius)
    return {"line": line, "start": start, "lines": lines[start - 1:end]}


# ---------- pipeline stages ----------

def bug_survives(target, bug, extra_tests=None):
    with Sandbox(target) as sb:
        sb.apply_mutation(bug["file"], bug["old"], bug["new"])
        if extra_tests:
            sb.add_file(GENERATED_NAME, extra_tests)
        passed, _ = sb.run_tests()
    return passed


def run_bugs(target, bugs, workers, extra_tests=None, on_result=None):
    """Plant each bug in its own sandbox, in parallel. Returns {bug_id: survived}."""
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(bug_survives, target, b, extra_tests): b for b in bugs}
        for fut in as_completed(futures):
            bug = futures[fut]
            results[bug["id"]] = fut.result()
            if on_result:
                on_result(bug, results[bug["id"]])
    return results


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


def save_report(report):
    out_dir = ROOT / "results" / report["target"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    text = json.dumps(report, indent=2)
    (out_dir / f"run-{stamp}.json").write_text(text)
    (out_dir / "latest.json").write_text(text)
    return f"results/{report['target']}/run-{stamp}.json"


def load_latest_report(target):
    path = ROOT / "results" / target / "latest.json"
    return json.loads(path.read_text()) if path.is_file() else None


# ---------- the whole pipeline ----------

def run(target, emit, bugs_per_file=5, files=None, workers=4, fix=True, offline=False):
    """Run BugBait end to end. Returns the report dict (or None if it stopped early)."""
    started = time.time()
    if target not in list_targets():
        emit("error", message=f"No target named '{target}'. Available: {', '.join(list_targets())}")
        return None

    emit("start", target=target, model=None if offline else DEFAULT_MODEL)

    with Sandbox(target) as sb:
        passed, output = sb.run_tests()
    m = re.search(r"(\d+) passed", output)
    test_count = int(m.group(1)) if m else 0
    emit("baseline", passed=passed, tests=test_count, output=None if passed else output[-3000:])
    if not passed:
        return None

    # Stage 1: Saboteur
    try:
        if offline:
            if target not in OFFLINE_BUGS:
                raise RuntimeError(f"Offline mode only has built-in bugs for: {', '.join(OFFLINE_BUGS)}")
            candidates = [dict(b) for b in OFFLINE_BUGS[target]]
        else:
            from agents.saboteur import generate_bugs

            sources, tests = target_files(target, files)
            if not sources:
                raise RuntimeError("No source files matched.")
            ttext = tests_text(tests, target)
            base = target_dir(target)

            def study(src):
                rel = src.relative_to(base).as_posix()
                emit("saboteur", file=rel)
                return generate_bugs(rel, src.read_text(), ttext, bugs_per_file)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                candidates = [b for found in pool.map(study, sources) for b in found]
    except Exception as e:
        emit("error", message=f"Saboteur failed: {e}")
        return None

    bugs = []
    for bug in candidates:
        reason = validate(bug, target)
        if reason:
            emit("skipped", bug=bug, reason=reason)
            continue
        bug["id"] = len(bugs) + 1
        bug["context"] = bug_context(bug, target)
        bugs.append(bug)
    if not bugs:
        emit("error", message="The Saboteur produced no usable bugs.")
        return None
    emit("bugs", bugs=bugs)

    # Stage 2: run every bug against the original tests
    def on_before(bug, survived):
        bug["survived_original_tests"] = survived
        emit("bug_result", id=bug["id"], survived=survived)

    run_bugs(target, bugs, workers, on_result=on_before)
    survivors = [b for b in bugs if b["survived_original_tests"]]
    caught_before = len(bugs) - len(survivors)
    emit("score_before", caught=caught_before, total=len(bugs))

    report = {
        "target": target,
        "model": None if offline else DEFAULT_MODEL,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "baseline_tests": test_count,
        "bugs": bugs,
        "score_before": {"caught": caught_before, "total": len(bugs)},
        "score_after": None,
        "generated_tests": None,
    }

    # Stage 3: Fixer
    if survivors and fix and not offline:
        from agents.fixer import write_test

        _, tests = target_files(target)
        ttext = tests_text(tests, target)
        emit("fixer_start", count=len(survivors))

        def fix_one(bug):
            source = (target_dir(target) / bug["file"]).read_text()
            try:
                result = write_test(target, bug, source, ttext,
                                    on_attempt=lambda n: emit("fix_attempt", id=bug["id"], attempt=n))
            except Exception as e:
                result = {"status": "failed", "test_code": None, "reason": str(e)[:200], "attempts": 0}
            bug["fix"] = result
            emit("fix_result", id=bug["id"], **result)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(fix_one, survivors))

        accepted = [(b["id"], b, b["fix"]["test_code"]) for b in survivors if b["fix"]["status"] == "fixed"]
        if accepted:
            test_file = build_test_file(accepted)
            out_dir = ROOT / "generated_tests" / target
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / GENERATED_NAME).write_text(test_file)

            def on_after(bug, survived):
                bug["survived_with_new_tests"] = survived
                emit("bug_after", id=bug["id"], survived=survived)

            after = run_bugs(target, bugs, workers, extra_tests=test_file, on_result=on_after)
            caught_after = len(bugs) - sum(after.values())
            report["score_after"] = {"caught": caught_after, "total": len(bugs)}
            report["generated_tests"] = test_file
            emit("score_after", caught=caught_after, total=len(bugs), tests_added=len(accepted),
                 saved_to=f"generated_tests/{target}/{GENERATED_NAME}")

    report["seconds"] = round(time.time() - started, 1)
    path = save_report(report)
    emit("done", report_path=path, seconds=report["seconds"])
    return report
