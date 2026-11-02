"""Regression tests for the dashboard folder picker (/api/choose-folder).

The bug: when macOS blocked the native dialog (Automation permission denied),
the endpoint swallowed the error and returned {"path": null} — identical to
"user cancelled" — so the UI just flipped back to Ready with no message.

All dialog backends are mocked here, so running this suite never opens a
real window.
"""

import pytest

import app


class Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def client():
    return app.app.test_client()


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(app.platform, "system", lambda: "Darwin")


# ---------- pick_folder_osx ----------

def test_osx_picker_returns_chosen_path(monkeypatch):
    monkeypatch.setattr(app.subprocess, "run",
                        lambda *a, **k: Proc(0, "/Users/me/code/proj/\n", ""))
    assert app.pick_folder_osx() == "/Users/me/code/proj/"


def test_osx_picker_cancel_returns_none(monkeypatch):
    monkeypatch.setattr(app.subprocess, "run",
                        lambda *a, **k: Proc(1, "", "execution error: User canceled. (-128)"))
    assert app.pick_folder_osx() is None


def test_osx_picker_blocked_raises(monkeypatch):
    monkeypatch.setattr(app.subprocess, "run",
                        lambda *a, **k: Proc(1, "", "execution error: Not authorized to send Apple events. (-1743)"))
    with pytest.raises(OSError, match="Not authorized"):
        app.pick_folder_osx()


# ---------- /api/choose-folder ----------

def test_endpoint_uses_native_result(client, darwin, monkeypatch):
    monkeypatch.setattr(app, "pick_folder_osx", lambda: "/tmp/proj/")
    r = client.post("/api/choose-folder")
    assert r.status_code == 200
    assert r.get_json() == {"path": "/tmp/proj"}  # trailing slash stripped


def test_endpoint_falls_back_to_tk_when_native_blocked(client, darwin, monkeypatch):
    def blocked():
        raise OSError("Not authorized to send Apple events. (-1743)")

    monkeypatch.setattr(app, "pick_folder_osx", blocked)
    monkeypatch.setattr(app, "pick_folder_tk", lambda: "/tmp/proj")
    r = client.post("/api/choose-folder")
    assert r.status_code == 200
    assert r.get_json() == {"path": "/tmp/proj"}


def test_endpoint_cancel_is_not_an_error(client, darwin, monkeypatch):
    monkeypatch.setattr(app, "pick_folder_osx", lambda: None)
    r = client.post("/api/choose-folder")
    assert r.status_code == 200
    assert r.get_json() == {"path": None}


def test_endpoint_failure_is_a_visible_error(client, darwin, monkeypatch):
    monkeypatch.setattr(app, "pick_folder_osx", lambda: (_ for _ in ()).throw(OSError("denied")))
    monkeypatch.setattr(app, "pick_folder_tk", lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    r = client.post("/api/choose-folder")
    assert r.status_code == 500
    body = r.get_json()["error"]
    assert "Automation" in body and "paste the folder path" in body


def test_version_endpoint(client):
    v = client.get("/api/version").get_json()["version"]
    assert isinstance(v, str) and len(v) >= 3


def test_server_config_defaults_and_env(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    assert app.server_config() == ("127.0.0.1", 5050, True)
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "8080")
    assert app.server_config() == ("0.0.0.0", 8080, False)


def test_history_lists_runs_newest_first(client, tmp_path, monkeypatch):
    import json as _json

    res = tmp_path / "results"
    (res / "aaa").mkdir(parents=True)
    (res / "aaa" / "run-20260101-000000.json").write_text(_json.dumps({
        "timestamp": "2026-01-01T00:00:00", "score_before": {"caught": 1, "total": 4},
        "score_after": {"caught": 3, "total": 4},
        "bugs": [{"fix": {"status": "fixed"}}, {"fix": {"status": "failed"}}]}))
    (res / "aaa" / "run-20260201-000000.json").write_text(_json.dumps({
        "timestamp": "2026-02-01T00:00:00", "score_before": {"caught": 2, "total": 2},
        "score_after": None, "bugs": []}))
    (res / "aaa" / "latest.json").write_text("{}")
    (res / "aaa" / "run-broken.json").write_text("not json {")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    hist = client.get("/api/history").get_json()
    assert [h["timestamp"] for h in hist] == ["2026-02-01T00:00:00", "2026-01-01T00:00:00"]
    assert hist[1]["tests_added"] == 1
    assert hist[1]["after"] == {"caught": 3, "total": 4}
    assert hist[0]["after"] == {"caught": 2, "total": 2}  # no fixer run: after == before
