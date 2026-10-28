"""Fixer agent: writes a test that catches a surviving bug, then proves the test works.

A generated test is only accepted if it
  1. PASSES on the original code (it's a correct test), and
  2. FAILS on the buggy code (it actually catches the bug).
If either check fails, the Fixer gets the pytest output and tries again.
"""

import re

from llm import chat
from runner import Sandbox

SYSTEM_PROMPT = """You are the Fixer in BugBait. A bug was planted in a project and the existing tests did NOT catch it.
Write a pytest test that FAILS on the buggy code and PASSES on the original code.

Rules:
- Test the intended behavior of the ORIGINAL code, not implementation details.
- Import from the module the same way the existing tests do.
- Write one or two small, readable test functions whose names start with test_.
- Reply with ONLY one ```python code block.

If the bug does not change any behavior a reasonable caller depends on (an "equivalent" or harmless
change), do not write a test. Instead reply with a single line: NO_TEST: <short reason>"""

CHECK_FILE = "test_bugbait_check.py"


def _extract_code(reply):
    if reply.strip().upper().startswith("NO_TEST"):
        return None, reply.strip().split(":", 1)[-1].strip()
    block = re.search(r"```(?:python)?\s*(.*?)```", reply, re.DOTALL)
    code = (block.group(1) if block else reply).strip()
    if "def test_" not in code:
        return None, "model did not return a test function"
    return code + "\n", None


def verify_test(target, bug, test_code):
    """Return (ok, problem). ok is True only if the test passes on original and fails on buggy code."""
    with Sandbox(target) as sb:
        sb.add_file(CHECK_FILE, test_code)
        passed, output = sb.run_tests(CHECK_FILE)
    if not passed:
        return False, "The test FAILED on the ORIGINAL (correct) code, so the test itself is wrong:\n" + output[-1500:]

    with Sandbox(target) as sb:
        sb.apply_mutation(bug["file"], bug["old"], bug["new"])
        sb.add_file(CHECK_FILE, test_code)
        passed, output = sb.run_tests(CHECK_FILE)
    if passed:
        return False, "The test PASSED on the BUGGY code, so it does not catch the bug."
    return True, None


def write_test(target, bug, source, tests, max_attempts=3, on_attempt=None):
    """Ask Muse Spark for a test, verify it, retry with feedback.

    Returns a dict: {"status": "fixed" | "skipped" | "failed", "test_code", "reason", "attempts"}
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"### Original `{bug['file']}`\n```python\n{source}\n```\n\n"
            f"### Planted bug\nReplaced:\n```python\n{bug['old']}\n```\nwith:\n```python\n{bug['new']}\n```\n"
            f"Saboteur's note: {bug.get('explanation', '')}\n\n"
            f"### Existing tests (they missed it)\n```python\n{tests}\n```"
        )},
    ]
    problem = None
    for attempt in range(1, max_attempts + 1):
        if on_attempt:
            on_attempt(attempt)
        reply = chat(messages, temperature=0.2)
        code, skip_reason = _extract_code(reply)
        if code is None:
            if skip_reason and "did not return" not in skip_reason:
                return {"status": "skipped", "test_code": None, "reason": skip_reason, "attempts": attempt}
            problem = skip_reason
        else:
            ok, problem = _safe_verify(target, bug, code)
            if ok:
                return {"status": "fixed", "test_code": code, "reason": None, "attempts": attempt}
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": f"That didn't work. {problem}\nPlease fix the test and reply with one ```python block."},
        ]
    return {"status": "failed", "test_code": None, "reason": (problem or "")[:200], "attempts": max_attempts}


def _safe_verify(target, bug, code):
    try:
        return verify_test(target, bug, code)
    except Exception as e:
        return False, f"Running the test crashed: {e}"
