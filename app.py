"""
app.py
Flask web server — single POST /get-data endpoint with SSE progress stream.
"""

import asyncio
import json
import os
import queue
import threading
import time
from datetime import datetime

from flask import Flask, Response, request, send_file, jsonify

from weather_automation import run_automation
from ppt_generator import generate_ppt

app = Flask(__name__, static_folder=".", static_url_path="")

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Cache-Control"
    return response

@app.route("/get-data", methods=["OPTIONS"])
def get_data_options():
    return "", 204

# ── Job state (simple in-memory, single-user tool) ────────────────────────────
_job_lock = threading.Lock()
_job_state = {
    "running": False,
    "messages": [],   # list of SSE messages
    "ppt_path": None,
    "error": None,
}


def _reset_job():
    with _job_lock:
        _job_state["running"] = True
        _job_state["messages"] = []
        _job_state["ppt_path"] = None
        _job_state["error"] = None


def _push(msg: str):
    with _job_lock:
        _job_state["messages"].append(msg)


def _finish(ppt_path: str = None, error: str = None):
    with _job_lock:
        _job_state["running"] = False
        _job_state["ppt_path"] = ppt_path
        _job_state["error"] = error


# ── Background worker ─────────────────────────────────────────────────────────
def _run_in_thread():
    """Run the async automation in a dedicated thread/event-loop."""
    async def _async_job():
        async def emit(msg: str):
            _push(msg)

        try:
            result = await run_automation(emit)
            _push("Creating PowerPoint…")
            ppt_path = generate_ppt(
                result["date_str"],
                result["weather_data"],
                result["imd_image"],
            )
            _push(f"DONE:{ppt_path}")
            _finish(ppt_path=ppt_path)
        except Exception as e:
            _push(f"ERROR:{e}")
            _finish(error=str(e))

    asyncio.run(_async_job())


INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file(INDEX_HTML_PATH)


@app.route("/get-data", methods=["POST"])
def get_data():
    with _job_lock:
        if _job_state["running"]:
            return jsonify({"error": "Job already running"}), 429

    _reset_job()
    t = threading.Thread(target=_run_in_thread, daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/progress")
def progress():
    """Server-Sent Events stream — client polls this to get live status."""
    def event_stream():
        sent_count = 0
        while True:
            with _job_lock:
                msgs = _job_state["messages"]
                new_msgs = msgs[sent_count:]
                running = _job_state["running"]
                ppt_path = _job_state["ppt_path"]
                error = _job_state["error"]

            for m in new_msgs:
                sent_count += 1
                yield f"data: {json.dumps({'msg': m})}\n\n"

            # Job finished
            if not running:
                if ppt_path:
                    fname = os.path.basename(ppt_path)
                    yield f"data: {json.dumps({'done': True, 'filename': fname})}\n\n"
                elif error:
                    yield f"data: {json.dumps({'error': error})}\n\n"
                return

            time.sleep(0.4)

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download")
def download():
    with _job_lock:
        ppt_path = _job_state.get("ppt_path")
    if not ppt_path or not os.path.exists(ppt_path):
        return jsonify({"error": "No file ready"}), 404
    return send_file(ppt_path, as_attachment=True,
                     download_name=os.path.basename(ppt_path))


@app.route("/status")
def status():
    with _job_lock:
        return jsonify({
            "running": _job_state["running"],
            "ppt_path": _job_state["ppt_path"],
            "error": _job_state["error"],
        })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
