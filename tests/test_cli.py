"""Pre-commit gating: --staged, --fail-under, --install-hook."""

import json
import os
import subprocess

import pytest

import engine
import main
from precommit import MARKER, install_hook
from projects import Project


def make_project(path, staged=("calc.py",)):
    (path / "calc.py").write_text(
        '"""Tiny calculator."""\n\n\ndef add(a, b):\n    return a + b\n\n\n'
        'def is_adult(age):\n    return age >= 18\n')
    (path / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    if staged:
        subprocess.run(["git", "add", *staged], cwd=path, check=True)
    return Project(name=path.name, root=path, source="local")


def test_staged_picks_only_staged_source_files(tmp_path):
    project = make_project(tmp_path)
    assert engine.staged_source_files(project) == (["calc.py"], None)
    (tmp_path / "notes.md").write_text("hello")
    subprocess.run(["git", "add", "notes.md"], cwd=tmp_path, check=True)
    assert engine.staged_source_files(project) == (["calc.py"], None)


def test_staged_nothing_staged(tmp_path):
    project = make_project(tmp_path, staged=())
    assert engine.staged_source_files(project) == ([], None)


def test_staged_not_a_repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    project = Project(name=tmp_path.name, root=tmp_path, source="local")
    files, err = engine.staged_source_files(project)
    assert files == [] and "git" in err


def test_gate_thresholds():
    report = {"score_before": {"caught": 2, "total": 3}}
    assert main.gate(report, 66) == (True, 66)
    assert main.gate(report, 67) == (False, 66)
    assert main.gate(report, 0) == (True, 66)


def test_install_hook_writes_executable_hook(tmp_path):
    make_project(tmp_path)
    hook = install_hook(tmp_path, 80)
    assert os.access(hook, os.X_OK)
    text = open(hook).read()
    assert MARKER in text and "--staged" in text and "--fail-under" in text and "80" in text
    assert install_hook(tmp_path, 80) == hook  # reinstalling our own hook is fine


def test_install_hook_refuses_foreign_hook_and_non_repo(tmp_path):
    (tmp_path / "repo").mkdir()
    repo = make_project(tmp_path / "repo")
    foreign = repo.root / ".git" / "hooks" / "pre-commit"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("#!/bin/sh\necho hi\n")
    with pytest.raises(ValueError, match="already has a pre-commit hook"):
        install_hook(repo.root)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError, match="not a git repository"):
        install_hook(plain)


def test_main_rejects_bad_flag_combos(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["main.py", "--staged", "--files", "a.py"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 2
    monkeypatch.setattr("sys.argv", ["main.py", "--fail-under", "101"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 2


def test_main_install_hook_json(tmp_path, monkeypatch, capsys):
    make_project(tmp_path)
    monkeypatch.setattr("sys.argv", ["main.py", "--install-hook", str(tmp_path), "--json"])
    main.main()
    event = json.loads(capsys.readouterr().out.strip())
    assert event["event"] == "hook_installed" and event["hook"].endswith("pre-commit")


def test_main_staged_nothing_to_do(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path, staged=())
    monkeypatch.setattr("projects.resolve", lambda *a, **k: project)
    monkeypatch.setattr("sys.argv", ["main.py", str(tmp_path), "--staged"])
    assert main.main() is None
    assert "nothing to do" in capsys.readouterr().out


def test_main_staged_runs_and_gates(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    monkeypatch.setattr("projects.resolve", lambda *a, **k: project)
    seen = {}

    def fake_run(spec, emit, **kwargs):
        seen["files"] = kwargs["files"]
        assert spec is project
        return {"score_before": {"caught": 1, "total": 2}}

    monkeypatch.setattr(engine, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["main.py", str(tmp_path), "--staged", "--fail-under", "50"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 0 and seen["files"] == ["calc.py"]
    assert "PASS" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["main.py", str(tmp_path), "--staged", "--fail-under", "51"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 1
