"""BugBait web dashboard.

    python app.py            then open http://localhost:5050

"Run" calls Muse Spark live. "Replay" re-animates the last saved run with no API calls,
which is handy for recording a demo.
"""

import json
import queue
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

import engine

app = Flask(__name__, static_folder="static")
run_lock = threading.Lock()


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/targets")
def targets():
    return jsonify([
        {"name": t, "has_replay": engine.load_latest_report(t) is not None}
        for t in engine.list_targets()
    ])


@app.get("/api/run")
def run_live():
    target = request.args.get("target", "calculator")
    bugs = max(1, min(int(request.args.get("bugs", 5)), 15))
    fix = request.args.get("fix", "1") == "1"

    if not run_lock.acquire(blocking=False):
        return Response(sse("error", {"message": "A run is already in progress."}) + sse("end", {}),
                        mimetype="text/event-stream")

    events = queue.Queue()

    def worker():
        try:
            engine.run(target, lambda e, **d: events.put((e, d)), bugs_per_file=bugs, fix=fix)
        except Exception as e:
            events.put(("error", {"message": f"Unexpected error: {e}"}))
        finally:
            events.put(("end", {}))
            run_lock.release()

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            try:
                event, data = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield sse(event, data)
            if event == "end":
                break

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/replay")
def replay():
    target = request.args.get("target", "calculator")
    speed = float(request.args.get("speed", 1.0)) or 1.0
    report = engine.load_latest_report(target)

    def stream():
        pause = lambda s: time.sleep(s / speed)
        if not report:
            yield sse("error", {"message": f"No saved run for '{target}' yet. Click Run first."})
            yield sse("end", {})
            return
        bugs = report["bugs"]
        for b in bugs:  # older reports didn't store code context
            if "context" not in b:
                try:
                    b["context"] = engine.bug_context(b, target)
                except Exception:
                    b["context"] = None
        yield sse("start", {"target": target, "model": report.get("model"), "replay": True})
        pause(0.6)
        tests = report.get("baseline_tests")
        if not tests:
            with engine.Sandbox(target) as sb:
                _, out = sb.run_tests()
            m = engine.re.search(r"(\d+) passed", out)
            tests = int(m.group(1)) if m else 0
        yield sse("baseline", {"passed": True, "tests": tests})
        pause(0.8)
        for f in sorted({b["file"] for b in bugs}):
            yield sse("saboteur", {"file": f})
        pause(1.6)
        yield sse("bugs", {"bugs": bugs})
        pause(0.8)
        for b in bugs:
            yield sse("bug_result", {"id": b["id"], "survived": b.get("survived_original_tests", False)})
            pause(0.45)
        yield sse("score_before", report["score_before"])
        survivors = [b for b in bugs if b.get("fix")]
        if survivors:
            pause(1.2)
            yield sse("fixer_start", {"count": len(survivors)})
            for b in survivors:
                for n in range(1, b["fix"].get("attempts", 1) + 1):
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
                                      "saved_to": f"generated_tests/{target}/{engine.GENERATED_NAME}"})
        yield sse("done", {"report_path": "replay", "seconds": report.get("seconds")})
        yield sse("end", {})

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})


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
