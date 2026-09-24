"""
app.py
Flask web server — accepts dynamic locations and date ranges, runs browser automation,
streams live Server-Sent Events (SSE) progress, and delivers PowerPoint download.
"""

import asyncio
import json
import os
import queue
import subprocess
import threading
import time
from datetime import datetime

from flask import Flask, Response, request, send_file, jsonify

from weather_automation import run_automation
from ppt_generator import generate_ppt
from whatsapp_notifier import send_whatsapp_message, format_whatsapp_summary

app = Flask(__name__, static_folder=".", static_url_path="")

# ── Build identity ────────────────────────────────────────────────────────────
def _get_build_info():
    # Render strips .git from deployed containers but injects RENDER_GIT_COMMIT
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    if commit:
        commit = commit[:7]   # short hash (first 7 chars)
    else:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=os.path.dirname(__file__),
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            commit = "dev-local"
    ts = datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
    return f"{commit} · {ts}"

BUILD_INFO = _get_build_info()

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Cache-Control"
    return response

@app.route("/ping")
def ping():
    """Ultra-lightweight keep-alive endpoint — no DB, no lock, instant 200."""
    return "pong", 200

@app.route("/get-data", methods=["OPTIONS"])
def get_data_options():
    return "", 204

# ── Job state (in-memory single-user tool) ──────────────────────────────────
_job_lock = threading.Lock()
_job_state = {
    "running": False,
    "messages": [],
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
def _run_in_thread(locations=None, start_date=None, end_date=None, whatsapp_target=None):
    async def _async_job():
        async def emit(msg: str):
            print(f"[Worker] {msg}", flush=True)
            _push(msg)

        try:
            result = await run_automation(
                locations=locations,
                start_date_str=start_date,
                end_date_str=end_date,
                emit=emit
            )
            _push("Generating PowerPoint presentation...")
            ppt_path = generate_ppt(result)
            _push(f"DONE:{ppt_path}")
            _finish(ppt_path=ppt_path)

            # If WhatsApp target specified, send notification + PPT attachment
            if whatsapp_target:
                try:
                    _push(f"Sending presentation to WhatsApp: {whatsapp_target}...")
                    public_base = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
                    filename = os.path.basename(ppt_path)
                    media_url = f"{public_base}/download/{filename}" if public_base else None
                    summary_text = format_whatsapp_summary(result)
                    if media_url:
                        summary_text += f"\n\n🔗 *Direct Download Link*:\n{media_url}"
                    send_res = send_whatsapp_message(whatsapp_target, summary_text, media_url=media_url)
                    _push(f"WhatsApp message dispatched: {send_res.get('status', 'sent')}")
                except Exception as e_wa:
                    print(f"[WhatsApp] Delivery failed: {e_wa}")
                    _push(f"WhatsApp delivery warning: {e_wa}")

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

    # Parse JSON payload if sent
    data = request.get_json(silent=True) or {}
    locations = data.get("locations")
    start_date = data.get("startDate")
    end_date = data.get("endDate")
    whatsapp_target = None
    if data.get("sendWhatsApp"):
        whatsapp_target = data.get("phone") or os.environ.get("TARGET_PHONE_NUMBER", "+917761866811")

    _reset_job()
    t = threading.Thread(
        target=_run_in_thread,
        args=(locations, start_date, end_date, whatsapp_target),
        daemon=True
    )
    t.start()
    return jsonify({"status": "started", "whatsapp_target": whatsapp_target})

@app.route("/progress")
def progress():
    """Server-Sent Events stream — client listens for real-time progress updates."""
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

            if not running:
                if ppt_path:
                    fname = os.path.basename(ppt_path)
                    yield f"data: {json.dumps({'done': True, 'filename': fname})}\n\n"
                elif error:
                    yield f"data: {json.dumps({'error': error})}\n\n"
                return

            time.sleep(0.3)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.route("/download")
def download():
    with _job_lock:
        ppt_path = _job_state.get("ppt_path")
    if not ppt_path or not os.path.exists(ppt_path):
        return jsonify({"error": "No report ready for download"}), 404
    return send_file(
        ppt_path,
        as_attachment=True,
        download_name=os.path.basename(ppt_path)
    )

@app.route("/download/<path:filename>")
def download_specific_file(filename):
    """Serve specific PPT presentation file publicly so Twilio WhatsApp can fetch it."""
    safe_name = os.path.basename(filename)
    file_path = os.path.join(os.path.dirname(__file__), safe_name)
    if os.path.exists(file_path):
        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_name,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
    return jsonify({"error": "File not found"}), 404

@app.route("/api/whatsapp-webhook", methods=["POST"])
def whatsapp_webhook():
    """
    Twilio WhatsApp Sandbox Webhook:
    When you text 'send now' or 'weather' to the Twilio sandbox number (+1 415 523 8886)
    from your WhatsApp (+91 7761866811), this endpoint triggers automation and responds.
    """
    from_number = request.form.get("From", "").strip()
    body = request.form.get("Body", "").strip().lower()
    print(f"[WhatsApp Webhook] Incoming message from {from_number}: '{body}'")

    triggers = ["send now", "send", "weather", "ppt", "wx", "now", "report", "update"]
    matched = any(t in body for t in triggers)

    if not matched:
        reply = (
            "👋 *Weather Automation Bot*\n\n"
            "To generate and receive today's Weather Presentation directly here, reply:\n"
            "👉 *'send now'*"
        )
        twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply}</Message></Response>'
        return Response(twiml, mimetype="application/xml")

    with _job_lock:
        if _job_state["running"]:
            reply = (
                "⏳ *Report already generating!*\n\n"
                "The automation is actively gathering weather data right now. "
                "Your PPT will be sent as soon as it completes in ~1-2 minutes."
            )
            twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply}</Message></Response>'
            return Response(twiml, mimetype="application/xml")

    target_phone = from_number or os.environ.get("TARGET_PHONE_NUMBER", "+917761866811")
    _reset_job()
    t = threading.Thread(
        target=_run_in_thread,
        args=(None, None, None, target_phone),
        daemon=True
    )
    t.start()

    reply = (
        "🌤️ *Weather Automation Initiated!*\n\n"
        "Collecting live data for Nashik, Mumbai, Pune, Ahmednagar & Aurangabad (AccuWeather, Windy & IMD)...\n\n"
        "Your PowerPoint briefing will be delivered here in ~2 minutes."
    )
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply}</Message></Response>'
    return Response(twiml, mimetype="application/xml")

@app.route("/api/trigger-daily", methods=["GET", "POST"])
def trigger_daily():
    """Endpoint for cron services (GitHub Actions / cron-job.org) to trigger daily 06:30 AM IST report."""
    target_phone = request.args.get("phone") or os.environ.get("TARGET_PHONE_NUMBER", "+917761866811")
    with _job_lock:
        if _job_state["running"]:
            return jsonify({"status": "already_running"}), 429
    _reset_job()
    t = threading.Thread(
        target=_run_in_thread,
        args=(None, None, None, target_phone),
        daemon=True
    )
    t.start()
    return jsonify({"status": "started", "target": target_phone})

@app.route("/status")
def status():
    with _job_lock:
        return jsonify({
            "running": _job_state["running"],
            "ppt_path": _job_state["ppt_path"],
            "error": _job_state["error"],
            "build": BUILD_INFO
        })


@app.route("/version")
def version():
    return jsonify({
        "version": "v2.3-accu-windy-pure",
        "features": [
            "Pure AccuWeather Direct Engine (curl-cffi)",
            "Pure Windy Live Forecast & WebGL Interpolator",
            "Dynamic IMD GIS Warning Maps",
            "Multi-City & Multi-Day Date Range",
            "Anti-Overlap Slide 2 Layout Engine"
        ],
        "status": "ready"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting server on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)

