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


def _sample_report(ts, caught, total, funcs=(), bugs=()):
    return {"timestamp": ts, "score_before": {"caught": caught, "total": total}, "score_after": None,
            "risk": {"functions": [{"file": f[0], "function": f[1], "planted": f[2], "survived": f[3]}
                                   for f in funcs]},
            "bugs": [{"file": b[0], "old": b[1], "new": b[2], "survived_original_tests": b[3]}
                     for b in bugs]}


def test_compare_reports_trends_and_matches():
    a = _sample_report("2026-01-01", 1, 2, funcs=[("c.py", "f", 2, 2), ("c.py", "g", 1, 0)],
                       bugs=[("c.py", "x", "y", True)])
    b = _sample_report("2026-02-01", 2, 2, funcs=[("c.py", "f", 2, 0), ("c.py", "h", 1, 1)],
                       bugs=[("c.py", "x", "y", False)])
    d = app.engine.compare_reports(a, b)
    assert d["a"]["before"] == {"caught": 1, "total": 2}
    trends = {(f["file"], f["function"]): f["trend"] for f in d["functions"]}
    assert trends == {("c.py", "f"): "improved", ("c.py", "g"): "gone", ("c.py", "h"): "new"}
    assert d["bugs"] == [{"file": "c.py", "old": "x", "new": "y", "a": "survived", "b": "caught"}]


def test_compare_endpoint_and_validation(client, tmp_path, monkeypatch):
    import json as _json

    res = tmp_path / "results" / "calc"
    res.mkdir(parents=True)
    (res / "run-20260101-000000.json").write_text(_json.dumps(_sample_report("2026-01-01", 1, 1)))
    (res / "run-20260201-000000.json").write_text(_json.dumps(_sample_report("2026-02-01", 2, 2)))
    monkeypatch.setattr(app, "ROOT", tmp_path)
    good = "/api/compare?project=calc&a=run-20260101-000000.json&b=run-20260201-000000.json"
    r = client.get(good)
    assert r.status_code == 200 and r.get_json()["b"]["before"] == {"caught": 2, "total": 2}
    assert client.get("/api/compare?project=calc&a=../x.json&b=run-20260201-000000.json").status_code == 400
    assert client.get("/api/compare?project=calc&a=run-20260101-000000.json&b=run-9.json").status_code == 404
    hist = client.get("/api/history").get_json()
    assert hist[0]["file"] == "run-20260201-000000.json"


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


def test_display_url():
    assert app.display_url("127.0.0.1", 5050) == "http://localhost:5050"
    assert app.display_url("0.0.0.0", 5050) == "http://localhost:5050"
    assert app.display_url("example.com", 80) == "http://example.com:80"


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


# ---------- hosted demo mode ----------

def test_hosted_safe_spec():
    from projects import is_hosted_safe_spec

    assert is_hosted_safe_spec("calculator")
    assert is_hosted_safe_spec("https://github.com/owner/repo")
    assert is_hosted_safe_spec("owner/repo")
    assert not is_hosted_safe_spec("/etc")
    assert not is_hosted_safe_spec("~/code/proj")
    assert not is_hosted_safe_spec("../bugbait")
    assert not is_hosted_safe_spec("")


def test_run_forwards_visitor_key_without_touching_env(client, monkeypatch):
    import os

    import engine

    seen = {}
    monkeypatch.setattr(engine, "run", lambda *a, **k: seen.update(k) or {})
    monkeypatch.delenv("META_API_KEY", raising=False)
    body = client.get("/api/run?project=calculator&bugs=1&key=visitor-key").get_data(as_text=True)
    assert "event: end" in body
    assert seen.get("api_key") == "visitor-key"
    assert "META_API_KEY" not in os.environ  # never in env: untrusted project code could read it


def test_hosted_blocks_local_paths_and_picker(client, monkeypatch):
    monkeypatch.setattr(app, "HOSTED", True)
    assert client.post("/api/choose-folder").status_code == 400
    assert client.post("/api/github/connect", json={"token": "x"}).status_code == 400
    assert client.post("/api/github/use-cli").status_code == 400
    body = client.get("/api/load?project=/etc").get_data(as_text=True)
    assert "public GitHub repos only" in body
    body = client.get("/api/run?project=/etc").get_data(as_text=True)
    assert "public GitHub repos only" in body


def test_version_reports_hosted_flag(client, monkeypatch):
    monkeypatch.setattr(app, "HOSTED", True)
    v = client.get("/api/version").get_json()
    assert v["hosted"] is True and "has_server_key" in v
