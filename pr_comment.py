"""Render a BugBait run report as Markdown for PR comments and job summaries.

Usage:
    python pr_comment.py results/calculator/latest.json
"""

import json
import sys


def pct(caught, total):
    return caught * 100 // total if total else 0


def render(report):
    """Turn a report dict (as saved by engine.save_report) into Markdown."""
    before = report.get("score_before") or {}
    after = report.get("score_after") or {}
    caught, total = before.get("caught", 0), before.get("total", 0)
    name = (report.get("project") or {}).get("name", report.get("target", "project"))
    lines = [f"## BugBait report — {name}",
             "",
             f"**Score: {caught}/{total} bugs caught ({pct(caught, total)}%)** "
             f"across {report.get('baseline_tests', '?')} existing tests."]
    if after.get("total"):
        lines.append(f"After generated tests: **{after['caught']}/{after['total']} "
                     f"({pct(after['caught'], after['total'])}%)**.")
    bugs = report.get("bugs", [])
    survived = [b for b in bugs if b.get("survived_original_tests")]
    if not bugs:
        lines += ["", "No bugs were tested in this run."]
    elif not survived:
        lines += ["", "All planted bugs were caught. Your tests are holding the line."]
    else:
        lines += ["", "### Bugs your tests missed"]
        for b in survived:
            line = (b.get("context") or {}).get("line", "?")
            func = f" in `{b['function']}`" if b.get("function") else ""
            lines.append(f"- `{b['file']}:{line}`{func}: "
                         f"`{(b.get('old') or '').strip()}` → `{(b.get('new') or '').strip()}`")
            if b.get("explanation"):
                lines.append(f"  - why it slipped: {b['explanation']}")
    weak = [f for f in (report.get("risk") or {}).get("functions", []) if f.get("survived")][:5]
    if weak:
        lines += ["", "### Weakest spots",
                  "", "| Function | Got through |", "|---|---|"]
        for f in weak:
            lines.append(f"| `{f['file']} {f['function']}` | {f['survived']}/{f['planted']} |")
    caught_bugs = [b for b in bugs if not b.get("survived_original_tests")]
    if caught_bugs and survived:
        lines += ["", "<details>", f"<summary>Caught bugs ({len(caught_bugs)})</summary>", ""]
        for b in caught_bugs:
            lines.append(f"- `{b['file']}`: `{(b.get('old') or '').strip()}` "
                         f"→ `{(b.get('new') or '').strip()}`")
        lines.append("</details>")
    if report.get("generated_tests"):
        lines += ["", f"Fixer tests were verified and saved "
                      f"(belongs at `{report.get('test_path')}` in the project)."]
    lines += ["", "<sub>Planted by BugBait. Weak commits can be blocked automatically "
                  "with `main.py --install-hook`.</sub>"]
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) != 2:
        print("Usage: python pr_comment.py <report.json>")
        sys.exit(1)
    try:
        report = json.loads(open(sys.argv[1]).read())
    except (OSError, ValueError):
        print(f"Couldn't read a BugBait report from {sys.argv[1]}")
        sys.exit(1)
    print(render(report), end="")


if __name__ == "__main__":
    main()
