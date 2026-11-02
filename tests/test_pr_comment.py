"""PR comment rendering."""

import json
import subprocess
import sys

from pr_comment import render

ROOT = __file__.rsplit("/", 2)[0]


def sample_report():
    return {
        "project": {"name": "calc"},
        "target": "calc",
        "baseline_tests": 4,
        "test_path": "tests/test_bugbait.py",
        "generated_tests": None,
        "score_before": {"caught": 1, "total": 3},
        "score_after": {"caught": 3, "total": 3},
        "bugs": [
            {"file": "calc.py", "old": "a + b", "new": "a - b",
             "survived_original_tests": False, "function": "add",
             "context": {"line": 4}, "explanation": ""},
            {"file": "calc.py", "old": "age >= 18", "new": "age > 18",
             "survived_original_tests": True, "function": "is_adult",
             "context": {"line": 9}, "explanation": "boundary at 18"},
        ],
        "risk": {"functions": [
            {"file": "calc.py", "function": "is_adult", "planted": 1, "survived": 1},
            {"file": "calc.py", "function": "add", "planted": 1, "survived": 0},
        ]},
    }


def test_render_covers_score_survivors_and_risk():
    md = render(sample_report())
    assert "## BugBait report — calc" in md
    assert "**Score: 1/3 bugs caught (33%)**" in md
    assert "After generated tests: **3/3 (100%)**" in md
    assert "`calc.py:9` in `is_adult`" in md
    assert "why it slipped: boundary at 18" in md
    assert "| `calc.py is_adult` | 1/1 |" in md
    assert "<summary>Caught bugs (1)</summary>" in md


def test_render_all_caught():
    report = sample_report()
    for b in report["bugs"]:
        b["survived_original_tests"] = False
    report["score_before"] = {"caught": 2, "total": 2}
    report["risk"] = {"functions": []}
    md = render(report)
    assert "All planted bugs were caught" in md
    assert "missed" not in md


def test_render_cli(tmp_path):
    report_file = tmp_path / "run.json"
    report_file.write_text(json.dumps(sample_report()))
    out = subprocess.run([sys.executable, f"{ROOT}/pr_comment.py", str(report_file)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "BugBait report" in out.stdout
    missing = subprocess.run([sys.executable, f"{ROOT}/pr_comment.py", str(tmp_path / "nope.json")],
                             capture_output=True, text=True, timeout=30)
    assert missing.returncode == 1 and "Couldn't read" in missing.stdout
