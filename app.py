"""BugBait web dashboard.

    python app.py            then open http://localhost:5050

Pick a project (a GitHub link, a folder on this computer, or an example), choose files, and run.
"Replay last run" re-animates a saved run with no API calls, which is handy for recording a demo.
"""

import json
import platform
import queue
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import engine
from projects import ROOT, ProjectError, list_examples, resolve


def _app_version():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                           timeout=10, cwd=Path(__file__).parent)
        return r.stdout.strip() or "dev"
    except Exception:
        return "dev"


APP_VERSION = _app_version()

app = Flask(__name__, static_folder="static")
run_lock = threading.Lock()
loaded = {}   # spec -> Project, so a project is only downloaded / set up once per session


def sse(event, data):
    # Browsers treat an event named "error" as a broken connection, so send it as "failure".
    if event == "error":
        event = "failure"
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream_job(job):
    """Run job(emit) in a thread and stream every emitted event to the browser."""
    events = queue.Queue()

    def worker():
        try:
            job(lambda e, **d: events.put((e, d)))
        except Exception as e:
            events.put(("error", {"message": f"Unexpected error: {e}"}))
        finally:
            events.put(("end", {}))

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            try:
                event, data = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield sse(event, data)
            if event == "end":
                break

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


def busy_response():
    return Response(sse("error", {"message": "BugBait is already working on something. Wait for it to finish."})
                    + sse("end", {}), mimetype="text/event-stream")


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/examples")
def examples():
    return jsonify(list_examples())


@app.get("/api/version")
def version():
    return jsonify({"version": APP_VERSION})


@app.get("/api/recent")
def recent():
    """Projects that have a saved run (for Replay)."""
    out = []
    results = ROOT / "results"
    if results.is_dir():
        for d in results.iterdir():
            report = engine.load_latest_report(d.name)
            if report:
                out.append({"name": d.name, "timestamp": report.get("timestamp"),
                            "spec": (report.get("project") or {}).get("url")
                                    or (report.get("project") or {}).get("root") or d.name})
    return jsonify(sorted(out, key=lambda r: r["timestamp"] or "", reverse=True))


def pick_folder_osx():
    """Native macOS folder picker. Returns the path, or None if the user cancelled.

    Raises OSError if the dialog itself failed (e.g. macOS blocked it), so the
    caller can fall back to another picker instead of reporting "cancelled".
    """
    r = subprocess.run(["osascript", "-e",
                        'POSIX path of (choose folder with prompt "Choose a project folder for BugBait")'],
                       capture_output=True, text=True, timeout=600)
    if r.returncode == 0:
        return r.stdout.strip() or None
    if "User canceled" in (r.stderr or "") or "-128" in (r.stderr or ""):
        return None
    raise OSError((r.stderr or "").strip() or f"osascript exited with code {r.returncode}")


def pick_folder_tk():
    """Cross-platform folder picker (Tkinter). Returns the path, or None if cancelled."""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        return filedialog.askdirectory(title="Choose a project folder for BugBait") or None
    finally:
        root.destroy()


@app.post("/api/choose-folder")
def choose_folder():
    """Open the operating system's folder picker (the server runs on the user's own computer)."""
    try:
        if platform.system() == "Darwin":
            try:
                path = pick_folder_osx()
            except OSError:
                # The native dialog was blocked (usually macOS Automation permission):
                # fall back to a Tkinter dialog rather than silently doing nothing.
                path = pick_folder_tk()
        else:
            path = pick_folder_tk()
    except Exception as e:
        hint = ("If no window appeared, allow your terminal to control Finder in System Settings "
                "→ Privacy & Security → Automation, or paste the folder path instead.")
        return jsonify({"error": f"Couldn't open a folder picker: {e}. {hint}"}), 500
    return jsonify({"path": path.rstrip("/") if path else None})


@app.get("/api/github/status")
def github_status():
    from github_pr import PRError, dashboard_token, gh_cli_token, verify_token
    gh = bool(gh_cli_token())
    token = dashboard_token()
    login = None
    if token:
        try:
            login = verify_token(token)
        except PRError:
            token = None
    return jsonify({"connected": bool(token), "login": login, "gh_available": gh})


@app.post("/api/github/connect")
def github_connect():
    from github_pr import PRError, save_token, verify_token
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "Paste a token first."}), 400
    try:
        login = verify_token(token)
    except PRError as e:
        return jsonify({"error": f"That token didn't work: {e}"}), 400
    save_token(token)
    return jsonify({"login": login})


@app.post("/api/github/disconnect")
def github_disconnect():
    from github_pr import save_token
    save_token("")
    return jsonify({"ok": True})


@app.get("/api/github/repos")
def github_repos():
    from github_pr import PRError, dashboard_token, list_repos
    try:
        token = dashboard_token()
        if not token:
            raise PRError("Not connected. Connect GitHub first.")
        return jsonify(list_repos(token))
    except PRError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/github/use-cli")
def github_use_cli():
    """Connect using the machine's gh CLI login instead of a pasted token."""
    from github_pr import PRError, gh_cli_token, save_token, verify_token
    token = gh_cli_token()
    if not token:
        return jsonify({"error": "The GitHub CLI isn't logged in here."}), 400
    try:
        login = verify_token(token)
    except PRError as e:
        return jsonify({"error": f"That token didn't work: {e}"}), 400
    save_token(token)
    return jsonify({"login": login})


@app.get("/api/load")
def load_project():
    """Download / set up a project and list the files that can be attacked."""
    spec = request.args.get("project", "").strip()
    refresh = request.args.get("refresh") == "1"
    if not run_lock.acquire(blocking=False):
        return busy_response()

    def job(emit):
        try:
            project = resolve(spec, log=lambda m: emit("setup", message=m), refresh=refresh)
            loaded[spec] = project
            emit("project", spec=spec, **engine.project_info(project),
                 files=project.source_files(), suggested=project.default_files(),
                 has_replay=engine.load_latest_report(project.name) is not None)
        except (ProjectError, OSError) as e:
            emit("error", message=str(e))
        finally:
            run_lock.release()

    return stream_job(job)


@app.get("/api/run")
def run_live():
    spec = request.args.get("project", "").strip()
    files = [f for f in request.args.get("files", "").split(",") if f] or None
    bugs = max(1, min(int(request.args.get("bugs", 5)), 15))
    fix = request.args.get("fix", "1") == "1"
    if not run_lock.acquire(blocking=False):
        return busy_response()

    def job(emit):
        try:
            engine.run(loaded.get(spec, spec), emit, files=files, bugs_per_file=bugs, fix=fix)
        finally:
            run_lock.release()

    return stream_job(job)


@app.get("/api/pr")
def open_pr():
    """Open a GitHub pull request with the latest run's verified tests."""
    from github_pr import PRError, open_pull_request
    name = request.args.get("name", "")

    def job(emit):
        report = engine.load_latest_report(name)
        if not report:
            emit("error", message=f"No saved run for '{name}'.")
            return
        try:
            emit("pr", url=open_pull_request(report, log=lambda m: emit("setup", message=m)))
        except PRError as e:
            emit("error", message=str(e))

    return stream_job(job)


@app.get("/api/replay")
def replay():
    name = request.args.get("name", "")
    speed = float(request.args.get("speed", 1.0)) or 1.0
    report = engine.load_latest_report(name)

    def gen():
        pause = lambda s: time.sleep(s / speed)
        if not report:
            yield sse("error", {"message": f"No saved run for '{name}' yet. Run it first."})
            yield sse("end", {})
            return
        bugs = report["bugs"]
        project = report.get("project") or {"name": name, "source": "example", "url": None, "root": None}
        if any("context" not in b for b in bugs):   # older reports didn't store code context
            try:
                p = resolve(project.get("root") or name, log=lambda m: None)
                for b in bugs:
                    b.setdefault("context", engine.bug_context(b, p))
            except Exception:
                pass
        yield sse("start", {"project": project, "target": project["name"], "model": report.get("model"), "replay": True})
        pause(0.6)
        yield sse("baseline", {"passed": True, "tests": report.get("baseline_tests") or 0})
        pause(0.6)
        files = report.get("files") or sorted({b["file"] for b in bugs})
        yield sse("files", {"files": files, "available": len(files)})
        for f in files:
            yield sse("saboteur", {"file": f})
            pause(0.5)
        pause(0.8)
        yield sse("bugs", {"bugs": bugs})
        pause(0.8)
        for b in bugs:
            yield sse("bug_result", {"id": b["id"], "survived": b.get("survived_original_tests", False)})
            pause(0.45)
        yield sse("score_before", report["score_before"])
        if report.get("risk"):
            yield sse("risk", report["risk"])
        survivors = [b for b in bugs if b.get("fix")]
        if survivors:
            pause(1.2)
            yield sse("fixer_start", {"count": len(survivors)})
            for b in survivors:
                for n in range(1, (b["fix"].get("attempts") or 1) + 1):
                    yield sse("fix_attempt", {"id": b["id"], "attempt": n})
                    pause(0.5)
                yield sse("fix_result", {"id": b["id"], **b["fix"]})
                pause(0.4)
        if report.get("score_after"):
            pause(0.8)
            for b in bugs:
                if "survived_with_new_tests" in b:
                    yield sse("bug_after", {"id": b["id"], "survived": b["survived_with_new_tests"]})
                    pause(0.15)
            yield sse("score_after", {**report["score_after"],
                                      "tests_added": sum(1 for b in survivors if b["fix"]["status"] == "fixed"),
                                      "saved_to": f"generated_tests/{name}/{engine.GENERATED_NAME}",
                                      "test_path": report.get("test_path") or engine.GENERATED_NAME})
        yield sse("done", {"report_path": "replay", "seconds": report.get("seconds")})
        yield sse("end", {})

    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import logging
    import webbrowser

    import flask.cli

    url = "http://localhost:5050"
    flask.cli.show_server_banner = lambda *args, **kwargs: None  # hide Flask's startup noise
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    print(f"\nBugBait dashboard is running at {url}")
    print("Your browser should open by itself. If not, open that link.")
    print("Keep this terminal open while you use the dashboard.")
    print("When you are completely done, press Ctrl+C here to stop it.\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
