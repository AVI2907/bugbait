"""Tests for GitHub connect (endpoints, token storage, authenticated clone).

The GitHub API and git are mocked: no network, no real .env writes.
"""

import os

import pytest

import app as app_module
import github_pr
import projects


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    """Redirect .env writes to a temp file and disable the gh-CLI fallback."""
    env = tmp_path / ".env"
    monkeypatch.setattr(github_pr, "ENV_PATH", env)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("shutil.which", lambda c: None)
    return env


def test_save_and_remove_token(temp_env):
    temp_env.write_text("META_API_KEY=x\n")
    github_pr.save_token("abc123")
    text = temp_env.read_text()
    assert "GITHUB_TOKEN=abc123" in text
    assert "META_API_KEY=x" in text  # other lines preserved
    assert os.environ["GITHUB_TOKEN"] == "abc123"
    github_pr.save_token("")
    assert "GITHUB_TOKEN" not in temp_env.read_text()
    assert "GITHUB_TOKEN" not in os.environ


def test_connect_verifies_then_stores(client, temp_env, monkeypatch):
    monkeypatch.setattr(github_pr.GitHub, "call", lambda self, m, p, **k: {"login": "octocat"})
    r = client.post("/api/github/connect", json={"token": "good"})
    assert r.status_code == 200
    assert r.get_json() == {"login": "octocat"}
    assert "GITHUB_TOKEN=good" in temp_env.read_text()


def test_connect_rejects_bad_token_without_storing(client, temp_env, monkeypatch):
    def boom(self, m, p, **k):
        raise github_pr.PRError("GitHub: Bad credentials")

    monkeypatch.setattr(github_pr.GitHub, "call", boom)
    r = client.post("/api/github/connect", json={"token": "bad"})
    assert r.status_code == 400
    assert "error" in r.get_json()
    assert not temp_env.exists()


def test_status_and_disconnect(client, temp_env, monkeypatch):
    monkeypatch.setattr(github_pr.GitHub, "call", lambda self, m, p, **k: {"login": "octocat"})
    assert client.get("/api/github/status").get_json() == {"connected": False, "login": None}
    client.post("/api/github/connect", json={"token": "good"})
    assert client.get("/api/github/status").get_json() == {"connected": True, "login": "octocat"}
    client.post("/api/github/disconnect")
    assert client.get("/api/github/status").get_json() == {"connected": False, "login": None}


def test_repos_returns_trimmed_shape(client, temp_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    payload = [{"full_name": "me/priv", "private": True, "default_branch": "main",
                "description": "d", "language": "Python", "updated_at": "2026-01-01",
                "extra": "dropped"}]
    monkeypatch.setattr(github_pr.GitHub, "call", lambda self, m, p, **k: payload)
    r = client.get("/api/github/repos")
    assert r.get_json() == [{k: v for k, v in payload[0].items() if k != "extra"}]


def test_network_failure_is_clean_json_error(client, temp_env, monkeypatch):
    def boom(self, m, p, **k):
        raise github_pr.PRError("GitHub: couldn't reach api.github.com (ReadTimeout). Check your connection.")

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(github_pr.GitHub, "call", boom)
    r = client.get("/api/github/repos")
    assert r.status_code == 400
    assert "couldn't reach" in r.get_json()["error"]


def test_github_call_wraps_request_exceptions(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ReadTimeout("timed out")

    monkeypatch.setattr(github_pr.requests.Session, "request", boom)
    with pytest.raises(github_pr.PRError, match="couldn't reach"):
        github_pr.GitHub("x").call("GET", "/user")


def test_clone_uses_token_header(tmp_path, temp_env, monkeypatch):
    monkeypatch.setattr(projects, "WORKSPACE", tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        (tmp_path / "repos" / "o__r").mkdir(parents=True)
        return R()

    monkeypatch.setattr(projects.subprocess, "run", fake_run)
    p = projects._clone("o", "r", log=lambda m: None, refresh=True)
    assert p.root == tmp_path / "repos" / "o__r"
    assert seen["cmd"][0:2] == ["git", "-c"]
    assert seen["cmd"][2] == "http.extraHeader=Authorization: Bearer secret"


def test_clone_without_token_has_no_auth_header(tmp_path, temp_env, monkeypatch):
    monkeypatch.setattr(projects, "WORKSPACE", tmp_path)
    seen = {}

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        (tmp_path / "repos" / "o__r").mkdir(parents=True)
        return R()

    monkeypatch.setattr(projects.subprocess, "run", fake_run)
    projects._clone("o", "r", log=lambda m: None, refresh=True)
    assert seen["cmd"][1] == "clone"  # no -c extraHeader
