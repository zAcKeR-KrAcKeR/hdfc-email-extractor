"""
Flask web server — gives the pipeline a public URL.
Endpoints:
  GET  /           dashboard showing recent runs
  POST /run        trigger one inbox scan manually
  GET  /status     JSON health check (used by Railway health probe)
"""

import threading
import json
from flask import Flask, jsonify, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

from email_data_extractor import run_once, recent_runs

app = Flask(__name__)

# ---- simple in-memory lock so two /run calls don't overlap ----
_running = threading.Lock()

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email Extractor Dashboard</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 16px; }
  h1   { color: #1a1a2e; }
  .btn { background:#0f3460; color:#fff; border:none; padding:10px 22px;
         border-radius:6px; cursor:pointer; font-size:15px; }
  .btn:hover { background:#16213e; }
  table { width:100%; border-collapse:collapse; margin-top:24px; }
  th,td { text-align:left; padding:10px 12px; border-bottom:1px solid #e0e0e0; }
  th    { background:#f5f5f5; }
  .replied  { color: #2e7d32; font-weight:600; }
  .skipped  { color: #9e9d24; }
  .error    { color: #c62828; }
  pre { background:#f8f8f8; padding:8px; border-radius:4px;
        font-size:12px; white-space:pre-wrap; word-break:break-all; max-height:120px; overflow:auto; }
</style>
</head>
<body>
<h1>📧 Email Extractor Dashboard</h1>
<p>Scans inbox every <strong>2 minutes</strong> for unread emails with PDF/image attachments,
   extracts data via OCR + LLM, and replies automatically.</p>

<form method="POST" action="/run">
  <button class="btn" type="submit">▶ Run Now</button>
</form>

{% if message %}
<p style="margin-top:16px; padding:12px; background:#e8f5e9; border-radius:6px;">{{ message }}</p>
{% endif %}

<h2>Recent Runs</h2>
{% if runs %}
<table>
  <tr><th>Time</th><th>Sender</th><th>Subject</th><th>Status</th><th>Detail</th></tr>
  {% for r in runs %}
  <tr>
    <td>{{ r.ts }}</td>
    <td>{{ r.sender }}</td>
    <td>{{ r.subject }}</td>
    <td class="{{ r.status }}">{{ r.status }}</td>
    <td><pre>{{ r.detail }}</pre></td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p>No runs yet. Click <em>Run Now</em> or wait for the scheduler.</p>
{% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    runs = recent_runs(20)
    return render_template_string(DASHBOARD_HTML, runs=runs, message=None)


@app.route("/run", methods=["POST"])
def trigger_run():
    if not _running.acquire(blocking=False):
        runs = recent_runs(20)
        return render_template_string(
            DASHBOARD_HTML, runs=runs, message="A run is already in progress — try again shortly."
        )
    try:
        result = run_once()
        msg = f"Done. Processed: {result['processed']} | Skipped: {result['skipped']} | Errors: {len(result['errors'])}"
        if result["errors"]:
            msg += " — " + "; ".join(result["errors"])
    finally:
        _running.release()

    runs = recent_runs(20)
    return render_template_string(DASHBOARD_HTML, runs=runs, message=msg)


@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "ok"})


# --------------------------------------------------------------------------
# Background scheduler: scan inbox every 2 minutes automatically
# --------------------------------------------------------------------------
def scheduled_run():
    if _running.acquire(blocking=False):
        try:
            run_once()
        finally:
            _running.release()


scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_run, "interval", minutes=2, id="inbox_scan")
scheduler.start()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
