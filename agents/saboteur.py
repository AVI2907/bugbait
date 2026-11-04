"""Saboteur agent: reads source code and invents realistic bugs that tests might miss."""

from llm import chat, extract_json

KINDS = ["off-by-one", "boundary", "wrong operator", "inverted condition", "wrong default",
         "swapped arguments", "missing case", "wrong value", "error handling", "other"]

SYSTEM_PROMPT = f"""You are the Saboteur in BugBait, a tool that measures how good a project's test suite is.
You plant small, realistic bugs (the kind a real developer could accidentally write) to see whether the
existing tests catch them. Plant a mix: roughly half straightforward mistakes (wrong arithmetic, flipped
boolean, wrong constant, swapped arguments) that any decent test should catch, and half subtle ones
that weak tests tend to miss (off-by-one errors, boundary conditions, wrong comparison operators).

Rules:
- Each bug changes ONE small piece of code.
- "old" must be copied EXACTLY from the source (same spacing), and must appear in the file only once.
- "new" must keep the code syntactically valid Python.
- Do not delete whole functions or change function names.
- Do not add contrived conditions that no developer would write (like `and x < 1000` out of nowhere).

For each bug, also rate "realism" from 1 to 5:
  5 = a mistake developers make all the time (e.g. < instead of <=)
  3 = plausible, seen in real code reviews
  1 = artificial, only written to fool tests
and pick "kind" from: {", ".join(KINDS)}.

Reply with ONLY a JSON array, no prose:
[{{"old": "...", "new": "...", "kind": "...", "realism": 4, "explanation": "what this bug breaks and why the current tests might miss it"}}]"""


def generate_bugs(file_name, source, tests, count=5, language="Python", api_key=None):
    """Ask Muse Spark for `count` candidate bugs for one source file."""
    fence = {"Python": "python", "JavaScript": "js", "Java": "java",
             "Go": "go", "TypeScript": "ts", "Rust": "rust",
             "Ruby": "ruby", "PHP": "php", "C#": "csharp",
             "Kotlin": "kotlin"}.get(language, "")
    system = SYSTEM_PROMPT.replace("syntactically valid Python", f"syntactically valid {language}")
    user_prompt = (
        f"Plant {count} different bugs in `{file_name}`.\n\n"
        f"### {file_name}\n```{fence}\n{source}\n```\n\n"
        f"### Existing tests\n```{fence}\n{tests}\n```"
    )
    reply = chat(agent="saboteur", messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ], api_key=api_key)
    bugs = extract_json(reply)
    if isinstance(bugs, dict):
        bugs = [bugs]
    out = []
    for b in bugs:
        if not (isinstance(b, dict) and b.get("old") and b.get("new") is not None):
            continue
        try:
            realism = max(1, min(5, int(b.get("realism", 3))))
        except (TypeError, ValueError):
            realism = 3
        kind = b.get("kind") if b.get("kind") in KINDS else "other"
        out.append({"file": file_name, "old": b["old"], "new": b["new"], "kind": kind,
                    "realism": realism, "explanation": b.get("explanation", "")})
    return out
