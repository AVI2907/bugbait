"""BugBait engine: the full Saboteur -> run -> Fixer -> rescore pipeline.

Progress is reported through an `emit(event, **data)` callback, so the same engine drives
the terminal (main.py), the web dashboard (app.py) and the VS Code extension (main.py --json).
"""

import ast
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from llm import model_for
from projects import ROOT, Project, ProjectError, resolve
from runner import Sandbox

GENERATED_NAME = "test_bugbait_generated.py"

OFFLINE_BUGS = {
    "calculator": [
        {"file": "calc.py", "old": "a + b", "new": "a - b", "explanation": "basic arithmetic"},
        {"file": "calc.py", "old": "age >= 18", "new": "age > 18", "explanation": "boundary at 18"},
        {"file": "calc.py", "old": "percent > 100", "new": "percent >= 100", "explanation": "boundary at 100"},
    ],
}


# ---------- helpers ----------

def validate(bug, project, min_realism=1):
    """Reject bugs we can't apply cleanly, that break Python syntax, or that are too artificial.

    Returns a reason, or None if the bug is usable.
    """
    path = project.root / bug["file"]
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
    if bug.get("realism", 5) < min_realism:
        return f"too artificial (realism {bug['realism']}/5)"
    try:
        ast.parse(code.replace(bug["old"], bug["new"], 1))
    except SyntaxError:
        return "mutation is not valid Python"
    return None


def bug_context(bug, project, radius=3):
    """Line number and surrounding lines of a bug, for display."""
    code = (project.root / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    lines = code.splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + bug["old"].count("\n") + radius)
    return {"line": line, "start": start, "lines": lines[start - 1:end]}


def enclosing_function(bug, project):
    """Name of the function or method a bug was planted in (e.g. 'Parser.parse'), or None."""
    code = (project.root / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    best = None

    def walk(node, prefix=""):
        nonlocal best
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if child.lineno <= line <= (child.end_lineno or child.lineno):
                    if not isinstance(child, ast.ClassDef):
                        best = name
                    walk(child, name + ".")
    try:
        walk(ast.parse(code))
    except SyntaxError:
        pass
    return best


def risk_map(bugs):
    """Group bugs by function and rank the places where the most bugs got through."""
    spots = {}
    for b in bugs:
        key = (b["file"], b.get("function") or "(module level)")
        s = spots.setdefault(key, {"file": key[0], "function": key[1], "planted": 0, "survived": 0,
                                   "line": (b.get("context") or {}).get("line")})
        s["planted"] += 1
        s["survived"] += 1 if b.get("survived_original_tests") else 0
    ranked = sorted(spots.values(), key=lambda s: (-s["survived"], -(s["survived"] / s["planted"]), s["file"]))
    files = {}
    for b in bugs:
        f = files.setdefault(b["file"], {"file": b["file"], "planted": 0, "survived": 0})
        f["planted"] += 1
        f["survived"] += 1 if b.get("survived_original_tests") else 0
    return {"functions": ranked, "files": sorted(files.values(), key=lambda f: -f["survived"])}


def generated_path(project):
    """Where the generated test file goes inside the project (next to the existing tests)."""
    d = project.test_dir()
    return GENERATED_NAME if d in (".", "") else f"{d}/{GENERATED_NAME}"


def baseline(project, emit):
    """Run the project's own tests. Tests that already fail are set aside so they don't hide results.

    Returns the number of passing tests, or None if the project can't be tested.
    """
    project.pytest_args = []
    with Sandbox(project) as sb:
        passed, output = sb.run_tests(stop_early=False)
    if not passed:
        failed = re.findall(r"^FAILED (\S+?)(?: - .*)?$", output, re.M)
        errored = re.findall(r"^ERROR (\S+?)(?: - .*)?$", output, re.M)
        if not failed and not errored:
            emit("baseline", passed=False, tests=0, output=output[-3000:])
            return None
        for node in failed:
            project.pytest_args += ["--deselect", node]
        for node in errored:
            project.pytest_args += (["--deselect", node] if "::" in node else ["--ignore", node])
        emit("setup", message=f"{len(failed) + len(errored)} of the project's tests already fail "
                              f"without any planted bugs. BugBait will set them aside.")
        with Sandbox(project) as sb:
            passed, output = sb.run_tests(stop_early=False)
        if not passed:
            emit("baseline", passed=False, tests=0, output=output[-3000:])
            return None
    m = re.search(r"(\d+) passed", output)
    count = int(m.group(1)) if m else 0
    if count == 0:
        emit("baseline", passed=False, tests=0,
             output="No tests were found. BugBait needs a project with pytest or unittest tests.")
        return None
    set_aside = project.pytest_args.count("--deselect") + project.pytest_args.count("--ignore")
    emit("baseline", passed=True, tests=count, set_aside=set_aside)
    return count


# ---------- pipeline stages ----------

def bug_survives(project, bug, extra_tests=None):
    with Sandbox(project) as sb:
        sb.apply_mutation(bug["file"], bug["old"], bug["new"])
        if extra_tests:
            sb.add_file(generated_path(project), extra_tests)
        passed, _ = sb.run_tests()
    return passed


def run_bugs(project, bugs, workers, extra_tests=None, on_result=None):
    """Plant each bug in its own sandbox, in parallel. Returns {bug_id: survived}."""
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(bug_survives, project, b, extra_tests): b for b in bugs}
        for fut in as_completed(futures):
            bug = futures[fut]
            try:
                results[bug["id"]] = fut.result()
            except Exception:
                results[bug["id"]] = False   # couldn't run: count as caught rather than inflate gaps
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
    out_dir = ROOT / "results" / report["project"]["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    text = json.dumps(report, indent=2, default=str)
    (out_dir / f"run-{stamp}.json").write_text(text)
    (out_dir / "latest.json").write_text(text)
    return (out_dir / f"run-{stamp}.json").relative_to(ROOT).as_posix()


def load_latest_report(name):
    path = ROOT / "results" / name / "latest.json"
    return json.loads(path.read_text()) if path.is_file() else None


def project_info(project):
    from github_pr import github_repo_for
    info = {"name": project.name, "source": project.source, "url": project.url, "root": str(project.root)}
    repo = github_repo_for(info)
    info["github"] = f"{repo[0]}/{repo[1]}" if repo else None
    return info


# ---------- the whole pipeline ----------

def run(spec, emit, files=None, bugs_per_file=5, workers=4, fix=True, offline=False, refresh=False, min_realism=3):
    """Run BugBait end to end on a project.

    spec: an example name, a folder path, a GitHub link, or a Project.
    files: source files to attack (relative paths). None = pick a few automatically.
    Returns the report dict, or None if it stopped early.
    """
    started = time.time()
    try:
        project = spec if isinstance(spec, Project) else resolve(spec, log=lambda m: emit("setup", message=m),
                                                                  refresh=refresh)
    except (ProjectError, OSError) as e:
        emit("error", message=str(e))
        return None

    models = None if offline else {"saboteur": model_for("saboteur"), "fixer": model_for("fixer")}
    emit("start", project=project_info(project), target=project.name,
         model=None if offline else " + ".join(dict.fromkeys(models.values())), models=models)

    test_count = baseline(project, emit)
    if test_count is None:
        return None

    # Which files to attack
    available = {f["path"]: f for f in project.source_files()}
    if offline:
        chosen = sorted({b["file"] for b in OFFLINE_BUGS.get(project.name, [])})
    elif files:
        chosen = [f for f in files if f in available]
        missing = [f for f in files if f not in available]
        if missing:
            emit("setup", message=f"Skipping files that aren't source files in this project: {', '.join(missing)}")
    else:
        chosen = project.default_files()
        if len(available) > len(chosen):
            emit("setup", message=f"Attacking {len(chosen)} of {len(available)} source files. "
                                  f"Choose files to attack others.")
    if not chosen:
        emit("error", message="No source files to attack.")
        return None
    emit("files", files=chosen, available=len(available))

    # Stage 1: Saboteur
    try:
        if offline:
            if project.name not in OFFLINE_BUGS:
                raise RuntimeError(f"Offline mode only has built-in bugs for: {', '.join(OFFLINE_BUGS)}")
            candidates = [dict(b) for b in OFFLINE_BUGS[project.name]]
        else:
            from agents.saboteur import generate_bugs

            def study(rel):
                emit("saboteur", file=rel)
                return generate_bugs(rel, (project.root / rel).read_text(),
                                     project.relevant_tests(rel), bugs_per_file)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                candidates = [b for found in pool.map(study, chosen) for b in found]
    except Exception as e:
        emit("error", message=f"Saboteur failed: {e}")
        return None

    bugs = []
    for bug in candidates:
        reason = validate(bug, project, min_realism)
        if reason:
            emit("skipped", bug=bug, reason=reason)
            continue
        bug["id"] = len(bugs) + 1
        bug["context"] = bug_context(bug, project)
        bug["function"] = enclosing_function(bug, project)
        bugs.append(bug)
    if not bugs:
        emit("error", message="The Saboteur produced no usable bugs. Try again or choose other files.")
        return None
    emit("bugs", bugs=bugs)

    # Stage 2: run every bug against the original tests
    def on_before(bug, survived):
        bug["survived_original_tests"] = survived
        emit("bug_result", id=bug["id"], survived=survived)

    run_bugs(project, bugs, workers, on_result=on_before)
    survivors = [b for b in bugs if b["survived_original_tests"]]
    caught_before = len(bugs) - len(survivors)
    emit("score_before", caught=caught_before, total=len(bugs))
    risk = risk_map(bugs)
    emit("risk", **risk)
    if len(bugs) >= 3 and not caught_before:
        emit("setup", message="No planted bug was caught. If that seems wrong, the tests may not be "
                              "importing this copy of the code (for example, an installed version wins).")

    report = {
        "project": project_info(project),
        "target": project.name,
        "model": None if offline else " + ".join(dict.fromkeys(models.values())),
        "models": models,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "baseline_tests": test_count,
        "files": chosen,
        "test_path": generated_path(project),
        "bugs": bugs,
        "score_before": {"caught": caught_before, "total": len(bugs)},
        "score_after": None,
        "generated_tests": None,
        "risk": risk,
    }

    # Stage 3: Fixer
    if survivors and fix and not offline:
        from agents.fixer import write_test

        emit("fixer_start", count=len(survivors))

        def fix_one(bug):
            source = (project.root / bug["file"]).read_text()
            try:
                result = write_test(project, bug, source, project.relevant_tests(bug["file"]),
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
            out_dir = ROOT / "generated_tests" / project.name
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / GENERATED_NAME).write_text(test_file)

            def on_after(bug, survived):
                bug["survived_with_new_tests"] = survived
                emit("bug_after", id=bug["id"], survived=survived)

            after = run_bugs(project, bugs, workers, extra_tests=test_file, on_result=on_after)
            caught_after = len(bugs) - sum(after.values())
            report["score_after"] = {"caught": caught_after, "total": len(bugs)}
            report["generated_tests"] = test_file
            emit("score_after", caught=caught_after, total=len(bugs), tests_added=len(accepted),
                 saved_to=(out_dir / GENERATED_NAME).relative_to(ROOT).as_posix(),
                 test_path=generated_path(project))

    report["seconds"] = round(time.time() - started, 1)
    path = save_report(report)
    emit("done", report_path=path, seconds=report["seconds"])
    return report
