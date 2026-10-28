"""Saboteur agent: reads source code and invents realistic bugs that tests might miss."""

from llm import chat, extract_json

SYSTEM_PROMPT = """You are the Saboteur in BugBait, a tool that measures how good a project's test suite is.
You plant small, realistic bugs (the kind a real developer could accidentally write) to see whether the
existing tests catch them. Favor subtle bugs that weak tests tend to miss: off-by-one errors, wrong
comparison operators, boundary conditions, inverted conditions, wrong default values, swapped arguments.

Rules:
- Each bug changes ONE small piece of code.
- "old" must be copied EXACTLY from the source (same spacing), and must appear in the file only once.
- "new" must keep the code syntactically valid Python.
- Do not delete whole functions or change function names.

Reply with ONLY a JSON array, no prose:
[{"old": "...", "new": "...", "explanation": "why the current tests might miss this"}]"""


def generate_bugs(file_name, source, tests, count=5):
    """Ask Muse Spark for `count` candidate bugs for one source file."""
    user_prompt = (
        f"Plant {count} different bugs in `{file_name}`.\n\n"
        f"### {file_name}\n```python\n{source}\n```\n\n"
        f"### Existing tests\n```python\n{tests}\n```"
    )
    reply = chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    bugs = extract_json(reply)
    if isinstance(bugs, dict):
        bugs = [bugs]
    return [
        {"file": file_name, "old": b["old"], "new": b["new"], "explanation": b.get("explanation", "")}
        for b in bugs
        if isinstance(b, dict) and b.get("old") and b.get("new") is not None
    ]
