"""BugBait engine: the full Saboteur -> run -> Fixer -> rescore pipeline.

Progress is reported through an `emit(event, **data)` callback, so the same engine drives
the terminal (main.py), the web dashboard (app.py) and the VS Code extension (main.py --json).
"""

import ast
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from llm import model_for
from projects import ROOT, Project, ProjectError, resolve
from runner import Sandbox, parse_go_results, parse_gradle_results

OFFLINE_BUGS = {
    "calculator": [
        {"file": "calc.py", "old": "a + b", "new": "a - b", "explanation": "basic arithmetic"},
        {"file": "calc.py", "old": "age >= 18", "new": "age > 18", "explanation": "boundary at 18"},
        {"file": "calc.py", "old": "percent > 100", "new": "percent >= 100", "explanation": "boundary at 100"},
    ],
    "javascript": [
        {"file": "calc.js", "old": "a + b", "new": "a - b", "explanation": "basic arithmetic"},
        {"file": "calc.js", "old": "age >= 18", "new": "age > 18", "explanation": "boundary at 18"},
        {"file": "calc.js", "old": "percent > 100", "new": "percent >= 100", "explanation": "boundary at 100"},
    ],
    "javacalc": [
        {"file": "src/main/java/calc/Calc.java", "old": "a + b", "new": "a - b",
         "explanation": "basic arithmetic"},
        {"file": "src/main/java/calc/Calc.java", "old": "age >= 18", "new": "age > 18",
         "explanation": "boundary at 18"},
        {"file": "src/main/java/calc/Calc.java", "old": "percent > 100", "new": "percent >= 100",
         "explanation": "boundary at 100"},
    ],
    "gradlecalc": [
        {"file": "src/main/java/calc/Calc.java", "old": "a + b", "new": "a - b",
         "explanation": "basic arithmetic"},
        {"file": "src/main/java/calc/Calc.java", "old": "age >= 18", "new": "age > 18",
         "explanation": "boundary at 18"},
        {"file": "src/main/java/calc/Calc.java", "old": "percent > 100", "new": "percent >= 100",
         "explanation": "boundary at 100"},
    ],
    "gocalc": [
        {"file": "calc.go", "old": "a + b", "new": "a - b", "explanation": "basic arithmetic"},
        {"file": "calc.go", "old": "age >= 18", "new": "age > 18", "explanation": "boundary at 18"},
        {"file": "calc.go", "old": "percent > 100", "new": "percent >= 100",
         "explanation": "boundary at 100"},
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
    if not project.language.syntax_ok(code.replace(bug["old"], bug["new"], 1)):
        return f"mutation is not valid {project.language.label}"
    return None


def bug_context(bug, project, radius=3):
    """Line number and surrounding lines of a bug, for display."""
    code = (project.root / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    lines = code.splitlines()
    start = max(1, line - radius)
    end = min(len(lines), line + bug["old"].count("\n") + radius)
    return {"line": line, "start": start, "lines": lines[start - 1:end]}


def enclosing_js_function(bug, project):
    """Nearest function above a bug, via simple patterns (function f, const f =, methods)."""
    code = (project.root / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    lines = code.splitlines()
    func, at = None, line - 1
    for i in range(line - 1, -1, -1):
        text = lines[i]
        m = (re.search(r"\bfunction\s+(\w+)", text)
             or re.search(r"\b(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)\s*=>|function\b)", text)
             or re.search(r"^\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", text))
        if m and m.group(1) not in ("if", "for", "while", "switch", "catch"):
            func, at = m.group(1), i
            break
    if not func:
        return None
    for i in range(at - 1, -1, -1):
        m = re.search(r"\bclass\s+(\w+)", lines[i])
        if m:
            return f"{m.group(1)}.{func}"
    return func


def enclosing_java_function(bug, project):
    """Nearest method above a bug, plus its class (e.g. 'Calc.add')."""
    code = (project.root / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    lines = code.splitlines()
    func, at = None, line - 1
    for i in range(line - 1, -1, -1):
        m = re.search(r"(?:^|[\s;}])(\w+)\s*\([^;()]*\)\s*(?:throws\s+[\w.,\s]+)?\{", lines[i])
        if m and m.group(1) not in ("if", "for", "while", "switch", "catch", "synchronized", "return", "new"):
            func, at = m.group(1), i
            break
    if not func:
        return None
    for i in range(at - 1, -1, -1):
        m = re.search(r"\bclass\s+(\w+)", lines[i])
        if m:
            return f"{m.group(1)}.{func}"
    return func


def enclosing_go_function(bug, project):
    """Nearest func above a bug; methods as 'Type.Method'."""
    code = (project.root / bug["file"]).read_text()
    line = code[: code.index(bug["old"])].count("\n") + 1
    lines = code.splitlines()
    for i in range(line - 1, -1, -1):
        m = re.search(r"^\s*func\s+(?:\(\s*\w+\s+[*]?(\w+)\s*\)\s*)?(\w+)\s*[(\[<]", lines[i])
        if m:
            recv, name = m.group(1), m.group(2)
            return f"{recv}.{name}" if recv else name
    return None


def enclosing_function(bug, project):
    """Name of the function or method a bug was planted in (e.g. 'Parser.parse'), or None."""
    if project.language.id == "go":
        return enclosing_go_function(bug, project)
    if project.language.id == "javascript":
        return enclosing_js_function(bug, project)
    if project.language.id == "java":
        return enclosing_java_function(bug, project)
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
    name = project.language.generated_name
    return name if d in (".", "") else f"{d}/{name}"


def no_tests_message(project):
    """Explain why the test runner found nothing to run."""
    lang = project.language
    n = len(project.test_files())
    if n == 0:
        msg = (f"No {lang.label} test files were found in this project (BugBait looks for "
               f"{lang.test_hint}). It measures test suites, so it needs {lang.test_framework} tests to run.")
        if not project.code_files():
            msg += " If this isn't a Python, JavaScript, Go or Java project, that's why — more languages are coming."
        return msg
    return (f"Found {n} test file(s), but {lang.test_framework} collected no tests from them. "
            "They may need dependencies BugBait couldn't install, or they define no tests.")


def gradle_baseline(project, emit):
    """Baseline for Gradle projects, measured from JUnit XML (Gradle prints no counts)."""
    with Sandbox(project) as sb:
        passed, output = sb.run_tests(stop_early=False)
        total, failed, failed_c, passed_c = parse_gradle_results(sb.path)
    set_aside = 0
    if not passed:
        if not failed_c:
            emit("baseline", passed=False, tests=0, output=output[-3000:])
            return None
        project.included_gradle_tests = passed_c
        set_aside = len(failed_c)
        emit("setup", message=f"{set_aside} test classes already fail without any planted bugs. "
                              f"BugBait will set them aside.")
        with Sandbox(project) as sb:
            passed, output = sb.run_tests(stop_early=False)
            total, failed, _, _ = parse_gradle_results(sb.path)
        if not passed or failed:
            emit("baseline", passed=False, tests=0, output=output[-3000:])
            return None
    if total == 0:
        emit("baseline", passed=False, tests=0, output=no_tests_message(project))
        return None
    emit("baseline", passed=True, tests=total, set_aside=set_aside)
    return total


def _go_readable(output, limit=3000):
    """Human-readable text from `go test -json` output (the Output payloads, in order)."""
    texts = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("Action") == "output" and event.get("Output"):
            texts.append(event["Output"])
    return ("".join(texts).strip() or output)[-limit:]


def go_baseline(project, emit):
    """Baseline for Go projects, measured from `go test -json` (plain output has no counts)."""
    with Sandbox(project) as sb:
        passed, output = sb.run_tests(stop_early=False)
        total, failed, failed_t, passed_t = parse_go_results(output)
    set_aside = 0
    if not passed and total == 0:
        emit("baseline", passed=False, tests=0, output=_go_readable(output))
        return None
    if total == 0:
        emit("baseline", passed=False, tests=0, output=no_tests_message(project))
        return None
    if failed_t:
        if not passed_t:
            emit("baseline", passed=False, tests=0,
                 output=(f"All {total} tests already fail without any planted bugs:\n"
                         + _go_readable(output, 2500)))
            return None
        project.included_go_tests = passed_t
        set_aside = len(failed_t)
        emit("setup", message=f"{set_aside} tests already fail without any planted bugs. "
                              f"BugBait will set them aside.")
        with Sandbox(project) as sb:
            passed, output = sb.run_tests(stop_early=False)
            total, failed, _, _ = parse_go_results(output)
        if not passed or failed:
            emit("baseline", passed=False, tests=0, output=_go_readable(output))
            return None
    emit("baseline", passed=True, tests=total, set_aside=set_aside)
    return total


def baseline(project, emit):
    """Run the project's own tests. Tests that already fail are set aside so they don't hide results.

    Returns the number of passing tests, or None if the project can't be tested.
    """
    project.pytest_args = []
    if project.language.id == "go":
        return go_baseline(project, emit)
    if project.language.id == "java" and project.build == "gradle":
        return gradle_baseline(project, emit)
    with Sandbox(project) as sb:
        passed, output = sb.run_tests(stop_early=False)
    if not passed:
        if project.language.id == "javascript":
            if "no tests found" in output.lower() or "# tests 0" in output or "No tests to run" in output:
                emit("baseline", passed=False, tests=0, output=no_tests_message(project))
            else:
                emit("baseline", passed=False, tests=0, output=output[-3000:])
            return None
        if project.language.id == "java":
            failed = re.findall(r"<<< (?:FAILURE|ERROR)! -- in ([\w.]+)", output)
            if not failed:
                emit("baseline", passed=False, tests=0, output=output[-3000:])
                return None
            project.excluded_java_tests = sorted({c.split(".")[-1] for c in failed})
            emit("setup", message=f"{len(project.excluded_java_tests)} test classes already fail "
                                  f"without any planted bugs. BugBait will set them aside.")
            with Sandbox(project) as sb:
                passed, output = sb.run_tests(stop_early=False)
            if not passed:
                emit("baseline", passed=False, tests=0, output=output[-3000:])
                return None
        else:
            failed = re.findall(r"^FAILED (\S+?)(?: - .*)?$", output, re.M)
            errored = re.findall(r"^ERROR (\S+?)(?: - .*)?$", output, re.M)
            if not failed and not errored:
                if "no tests ran" in output or "no tests collected" in output:
                    emit("baseline", passed=False, tests=0, output=no_tests_message(project))
                else:
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
    if project.language.id == "javascript":
        m = re.search(r"[#ℹ] pass (\d+)", output)
        count = int(m.group(1)) if m else 0
    elif project.language.id == "java":
        # Surefire prints per-class lines ("... -- in FooTest") plus bare totals lines; sum the totals.
        count = 0
        for line in output.splitlines():
            m = re.search(r"Tests run: (\d+)", line)
            if m and " -- in " not in line:
                count += int(m.group(1))
    else:
        m = re.search(r"(\d+) passed", output)
        count = int(m.group(1)) if m else 0
    if count == 0:
        emit("baseline", passed=False, tests=0, output=no_tests_message(project))
        return None
    if project.language.id == "java":
        set_aside = len(project.excluded_java_tests)
    else:
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


def _go_test_funcs(code):
    """Extract (name, text) of each top-level Test func, matched by braces."""
    out = []
    for m in re.finditer(r"(?m)^func\s+(Test\w*)\s*\([^)]*\)\s*\{", code):
        depth = 0
        for i in range(m.end() - 1, len(code)):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), code[m.start():i + 1]))
                    break
    return out


def _go_package(project):
    """Package line for generated tests: the package of the tests they sit next to."""
    d = project.root / project.test_dir()
    for pattern in ("*_test.go", "*.go"):
        for path in sorted(d.glob(pattern)):
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            m = re.search(r"(?m)^package\s+(\w+)", text)
            if m:
                return m.group(1)
    return "main"


def _build_go_test_file(accepted, project):
    imports, bodies = [], []
    for i, bug, code in accepted:
        for block in re.findall(r"(?ms)^import\s*\(\s*(.*?)^\)", code):
            for line in block.splitlines():
                s = line.strip()
                if s and not s.startswith("//") and s not in imports:
                    imports.append(s)
        for line in code.splitlines():
            s = line.strip()
            if s.startswith("import ") and '"' in s and "(" not in s:
                s = s[len("import "):].strip()  # single-line form -> block entry
                if s not in imports:
                    imports.append(s)
        for name, func in _go_test_funcs(code):
            renamed = func.replace(f"func {name}(", f"func TestBug{i}{name[4:]}(", 1)
            header = f"// Catches bug {i}: {bug['file']}: {bug['old'].strip()} -> {bug['new'].strip()}"
            bodies.append(f"{header}\n{renamed}")
    head = "// Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug.\n\n"
    import_block = ("import (\n" + "\n".join(f"\t{l}" for l in imports) + "\n)\n\n") if imports else ""
    return f"{head}package {_go_package(project)}\n\n{import_block}" + "\n\n".join(bodies) + "\n"


def build_test_file(accepted, project):
    """Merge verified tests into one file, renaming tests so they can't collide."""
    language = project.language
    if language.id == "javascript":
        return _build_js_test_file(accepted)
    if language.id == "java":
        return _build_java_test_file(accepted)
    if language.id == "go":
        return _build_go_test_file(accepted, project)
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


def _build_js_test_file(accepted):
    imports, bodies = [], []
    for i, bug, code in accepted:
        body = []
        for line in code.splitlines():
            if re.match(r"^(import |export |(?:const|let|var)\s+.+=\s*require\()", line):
                if line not in imports:
                    imports.append(line)
            else:
                body.append(line)
        text = "\n".join(body).strip()
        text = re.sub(r"\b(test|it)\(\s*(['\"])",
                      lambda m: f"{m.group(1)}({m.group(2)}bug{i}: ", text)
        header = f"// Catches bug {i}: {bug['file']}: {bug['old'].strip()} -> {bug['new'].strip()}"
        bodies.append(f"{header}\n{text}")
    head = "// Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug.\n\n"
    return head + "\n".join(imports) + "\n\n\n" + "\n\n\n".join(bodies) + "\n"


def _java_methods(code):
    """Extract (name, text) of each @Test method, matched by braces."""
    out = []
    for m in re.finditer(r"@Test\b[^{]*?\bvoid\s+(\w+)\s*\([^)]*\)\s*\{", code):
        depth = 0
        for i in range(m.end() - 1, len(code)):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), code[m.start():i + 1]))
                    break
    return out


def _build_java_test_file(accepted):
    package, imports, bodies = None, [], []
    for i, bug, code in accepted:
        for line in code.splitlines():
            s = line.strip()
            if s.startswith("package ") and package is None:
                package = s
            elif s.startswith("import ") and line not in imports:
                imports.append(line)
        for name, method in _java_methods(code):
            renamed = method.replace(f"void {name}(", f"void bug{i}_{name}(", 1)
            header = f"// Catches bug {i}: {bug['file']}: {bug['old'].strip()} -> {bug['new'].strip()}"
            indented = "\n".join(f"    {l}" if l.strip() else l for l in renamed.splitlines())
            bodies.append(f"    {header}\n{indented}")
    head = "// Tests written by the BugBait Fixer agent. Each one was verified to catch a real bug.\n\n"
    parts = [head.rstrip()]
    if package:
        parts.append(package)
    parts.append("\n".join(imports))
    parts.append("\npublic class BugbaitGeneratedTest {\n" + "\n\n".join(bodies) + "\n}\n")
    return "\n".join(parts)


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


def _merged_tests_pass(project, test_file):
    """Sanity check: the merged file must pass on clean code before it can score anything."""
    try:
        with Sandbox(project) as sb:
            sb.add_file(generated_path(project), test_file)
            passed, _ = sb.run_tests(generated_path(project), stop_early=False)
        return passed
    except Exception:
        return False


def compare_reports(a, b):
    """Diff two run reports: score movement, per-function trends, exact bug matches."""
    def score(r, key):
        s = r.get(key) or r.get("score_before") or {}
        return {"caught": s.get("caught", 0), "total": s.get("total", 0)}

    def riskmap(r):
        return {(f["file"], f["function"]): f for f in (r.get("risk") or {}).get("functions", [])}

    ra, rb = riskmap(a), riskmap(b)
    functions = []
    for key in sorted(set(ra) | set(rb)):
        fa, fb = ra.get(key), rb.get(key)
        sa = fa["survived"] / fa["planted"] if fa else None
        sb = fb["survived"] / fb["planted"] if fb else None
        if fa and fb:
            trend = "improved" if sb < sa else ("regressed" if sb > sa else "same")
        else:
            trend = "new" if fb else "gone"
        functions.append({
            "file": key[0], "function": key[1], "trend": trend,
            "a": {"planted": fa["planted"], "survived": fa["survived"]} if fa else None,
            "b": {"planted": fb["planted"], "survived": fb["survived"]} if fb else None,
        })

    def bugmap(r):
        return {(x["file"], x.get("old"), x.get("new")): x for x in r.get("bugs", [])}

    def state(x):
        if not x:
            return "absent"
        return "survived" if x.get("survived_original_tests") else "caught"

    ba, bb = bugmap(a), bugmap(b)
    bugs = [{"file": k[0], "old": k[1], "new": k[2],
             "a": state(ba.get(k)), "b": state(bb.get(k))}
            for k in sorted(set(ba) | set(bb), key=str)]
    return {
        "a": {"timestamp": a.get("timestamp"), "before": score(a, "score_before"),
              "after": score(a, "score_after")},
        "b": {"timestamp": b.get("timestamp"), "before": score(b, "score_before"),
              "after": score(b, "score_after")},
        "functions": functions, "bugs": bugs,
    }


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
    info = {"name": project.name, "source": project.source, "url": project.url, "root": str(project.root),
            "test_files": len(project.test_files())}
    repo = github_repo_for(info)
    info["github"] = f"{repo[0]}/{repo[1]}" if repo else None
    return info


def staged_source_files(project):
    """Source files with staged git changes. Returns (files, error); error is None on success."""
    try:
        top = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=15)
        if top.returncode:
            return [], "not a git repository (or git isn't installed)"
        toplevel = Path(top.stdout.strip()).resolve()
        diff = subprocess.run(["git", "-C", str(project.root), "diff", "--cached",
                               "--name-only", "-z"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return [], "couldn't run git"
    if diff.returncode:
        return [], "couldn't read the staged changes"
    staged = {p for p in diff.stdout.split("\0") if p}
    available = {f["path"] for f in project.source_files()}
    root = project.root.resolve()
    out = []
    for rel in sorted(staged):
        try:
            local = (toplevel / rel).relative_to(root).as_posix()
        except ValueError:
            continue
        if local in available:
            out.append(local)
    return out, None


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
                                     project.relevant_tests(rel), bugs_per_file,
                                     language=project.language.label)

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
        "language": project.language.label,
        "generated_name": project.language.generated_name,
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
            test_file = build_test_file(accepted, project)
            out_dir = ROOT / "generated_tests" / project.name
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / project.language.generated_name).write_text(test_file)
            report["generated_tests"] = test_file

            if not _merged_tests_pass(project, test_file):
                emit("setup", message="The merged test file didn't pass on its own, so it was saved "
                                      "but not scored. Each test was still individually verified.")
            else:
                def on_after(bug, survived):
                    bug["survived_with_new_tests"] = survived
                    emit("bug_after", id=bug["id"], survived=survived)

                after = run_bugs(project, bugs, workers, extra_tests=test_file, on_result=on_after)
                caught_after = len(bugs) - sum(after.values())
                report["score_after"] = {"caught": caught_after, "total": len(bugs)}
                emit("score_after", caught=caught_after, total=len(bugs), tests_added=len(accepted),
                     saved_to=(out_dir / project.language.generated_name).relative_to(ROOT).as_posix(),
                     test_path=generated_path(project))

    report["seconds"] = round(time.time() - started, 1)
    path = save_report(report)
    emit("done", report_path=path, seconds=report["seconds"])
    return report
