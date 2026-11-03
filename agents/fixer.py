"""Fixer agent: writes a test that catches a surviving bug, then proves the test works.

A generated test is only accepted if it
  1. PASSES on the original code (it's a correct test), and
  2. FAILS on the buggy code (it actually catches the bug).
If either check fails, the Fixer gets the test output and tries again.
"""

import re

from llm import chat
from runner import Sandbox

SYSTEM_PROMPT = """You are the Fixer in BugBait. A bug was planted in a project and the existing tests did NOT catch it.
Write a pytest test that FAILS on the buggy code and PASSES on the original code.

Rules:
- Test the intended behavior of the ORIGINAL code, not implementation details.
- Import from the module the same way the existing tests do (the test file sits next to them).
- Write one or two small, readable test functions whose names start with test_.
- Reply with ONLY one ```python code block.

If the bug does not change any behavior a reasonable caller depends on (an "equivalent" or harmless
change), do not write a test. Instead reply with a single line: NO_TEST: <short reason>"""

JS_SYSTEM_PROMPT = """You are the Fixer in BugBait. A bug was planted in a project and the existing tests did NOT catch it.
Write a node:test test that FAILS on the buggy code and PASSES on the original code.

Rules:
- Test the intended behavior of the ORIGINAL code, not implementation details.
- Import from the module the same way the existing tests do (the test file sits next to them).
- Use `const test = require('node:test')` and `const assert = require('node:assert')`
  (or the equivalent ESM imports), and write one or two small test() blocks.
- Reply with ONLY one ```js code block.

If the bug does not change any behavior a reasonable caller depends on (an "equivalent" or harmless
change), do not write a test. Instead reply with a single line: NO_TEST: <short reason>"""

JAVA_SYSTEM_PROMPT = """You are the Fixer in BugBait. A bug was planted in a project and the existing tests did NOT catch it.
Write a JUnit test that FAILS on the buggy code and PASSES on the original code.

Rules:
- Test the intended behavior of the ORIGINAL code, not implementation details.
- Match the existing tests' style: same JUnit version imports, same package line.
- Start with the `package` line and needed `import` lines, then one or two self-contained
  @Test methods (no class wrapper, no helper methods).
- Reply with ONLY one ```java code block.

If the bug does not change any behavior a reasonable caller depends on (an "equivalent" or harmless
change), do not write a test. Instead reply with a single line: NO_TEST: <short reason>"""

GO_SYSTEM_PROMPT = """You are the Fixer in BugBait. A bug was planted in a Go project and the existing tests did NOT catch it.
Write a Go test that FAILS on the buggy code and PASSES on the original code.

Rules:
- Test the intended behavior of the ORIGINAL code, not implementation details.
- Start with the same `package` line the existing tests use, then the needed imports.
- Write exactly one test function named TestBugbaitCheck (func TestBugbaitCheck(t *testing.T)).
- Use plain testing assertions (t.Errorf / t.Fatalf); no external assert libraries.
- Reply with ONLY one ```go code block.

If the bug does not change any behavior a reasonable caller depends on (an "equivalent" or harmless
change), do not write a test. Instead reply with a single line: NO_TEST: <short reason>"""


def _check_path(project):
    d = project.test_dir()
    name = project.language.check_name
    return name if d in (".", "") else f"{d}/{name}"


def _extract_code(reply, language="Python"):
    if reply.strip().upper().startswith("NO_TEST"):
        return None, reply.strip().split(":", 1)[-1].strip()
    block = re.search(r"```(?:\w+)?\s*(.*?)```", reply, re.DOTALL)
    code = (block.group(1) if block else reply).strip()
    markers = {"Python": ("def test_",), "JavaScript": ("test(", "it("), "Java": ("@Test",),
               "Go": ("func Test",)}
    if not any(m in code for m in markers.get(language, ("test",))):
        return None, "model did not return a test function"
    return code + "\n", None


def verify_test(project, bug, test_code):
    """Return (ok, problem). ok is True only if the test passes on original and fails on buggy code."""
    check = _check_path(project)
    with Sandbox(project) as sb:
        sb.add_file(check, test_code)
        passed, output = sb.run_tests(check, stop_early=False)
    if not passed:
        return False, "The test FAILED on the ORIGINAL (correct) code, so the test itself is wrong:\n" + output[-1500:]

    with Sandbox(project) as sb:
        sb.apply_mutation(bug["file"], bug["old"], bug["new"])
        sb.add_file(check, test_code)
        passed, output = sb.run_tests(check)
    if passed:
        return False, "The test PASSED on the BUGGY code, so it does not catch the bug."
    return True, None


def write_test(project, bug, source, tests, max_attempts=3, on_attempt=None):
    """Ask Muse Spark for a test, verify it, retry with feedback.

    Returns a dict: {"status": "fixed" | "skipped" | "failed", "test_code", "reason", "attempts"}
    """
    language = project.language.label
    fence = {"python": "python", "javascript": "js", "java": "java",
             "go": "go"}[project.language.id]
    system = {"python": SYSTEM_PROMPT, "javascript": JS_SYSTEM_PROMPT,
              "java": JAVA_SYSTEM_PROMPT, "go": GO_SYSTEM_PROMPT}[project.language.id]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"### Original `{bug['file']}`\n```{fence}\n{source}\n```\n\n"
            f"### Planted bug\nReplaced:\n```{fence}\n{bug['old']}\n```\nwith:\n```{fence}\n{bug['new']}\n```\n"
            f"Saboteur's note: {bug.get('explanation', '')}\n\n"
            f"### Existing tests (they missed it)\n```{fence}\n{tests}\n```"
        )},
    ]
    problem = None
    for attempt in range(1, max_attempts + 1):
        if on_attempt:
            on_attempt(attempt)
        reply = chat(messages, agent="fixer", temperature=0.2)
        code, skip_reason = _extract_code(reply, language)
        if code is None:
            if skip_reason and "did not return" not in skip_reason:
                return {"status": "skipped", "test_code": None, "reason": skip_reason, "attempts": attempt}
            problem = skip_reason
        else:
            ok, problem = _safe_verify(project, bug, code)
            if ok:
                return {"status": "fixed", "test_code": code, "reason": None, "attempts": attempt}
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": f"That didn't work. {problem}\nPlease fix the test and reply with one ```{fence} block."},
        ]
    return {"status": "failed", "test_code": None, "reason": (problem or "")[:200], "attempts": max_attempts}


def _safe_verify(project, bug, code):
    try:
        return verify_test(project, bug, code)
    except Exception as e:
        return False, f"Running the test crashed: {e}"
