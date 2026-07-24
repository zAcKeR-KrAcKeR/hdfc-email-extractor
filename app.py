"""
Flask web server — gives the pipeline a public URL.
Endpoints:
  GET  /           dashboard showing recent runs
  POST /run        trigger one inbox scan manually
  GET  /status     JSON health check (used by Railway health probe)
"""

import threading
from flask import Flask, jsonify, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

from email_data_extractor import run_once, latest_run

app = Flask(__name__)

# ---- simple in-memory lock so two /run calls don't overlap ----
_running = threading.Lock()

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email Data Extractor</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #f0f4f8;
    min-height: 100vh;
    color: #1a202c;
  }

  /* ── top nav ── */
  nav {
    background: #1e3a5f;
    padding: 0 32px;
    display: flex;
    align-items: center;
    gap: 12px;
    height: 56px;
    box-shadow: 0 2px 8px rgba(0,0,0,.25);
  }
  nav .logo { font-size: 22px; }
  nav h1 { color: #fff; font-size: 17px; font-weight: 600; letter-spacing: .3px; }
  nav .badge {
    margin-left: auto;
    background: #2ecc71;
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: .5px;
  }

  /* ── layout ── */
  main { max-width: 1050px; margin: 36px auto; padding: 0 20px; }

  /* ── stat cards ── */
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
  }
  .card {
    background: #fff;
    border-radius: 12px;
    padding: 20px 24px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    border-left: 4px solid #1e3a5f;
  }
  .card.green  { border-left-color: #2ecc71; }
  .card.yellow { border-left-color: #f39c12; }
  .card.red    { border-left-color: #e74c3c; }
  .card .num { font-size: 32px; font-weight: 700; line-height: 1; }
  .card .lbl { font-size: 12px; color: #718096; margin-top: 4px; text-transform: uppercase; letter-spacing: .5px; }

  /* ── action bar ── */
  .action-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: #1e3a5f;
    color: #fff;
    border: none;
    padding: 11px 24px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    transition: background .15s;
  }
  .btn:hover { background: #16305a; }
  .btn svg { width:16px; height:16px; fill:currentColor; }

  .toast {
    flex: 1;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
  }
  .toast.ok  { background: #d4edda; color: #155724; }
  .toast.err { background: #f8d7da; color: #721c24; }

  /* ── table ── */
  .table-wrap {
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    overflow: hidden;
  }
  .table-header {
    padding: 16px 20px;
    border-bottom: 1px solid #e2e8f0;
    font-weight: 600;
    font-size: 14px;
    color: #2d3748;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  table { width: 100%; border-collapse: collapse; }
  th {
    background: #f7fafc;
    text-align: left;
    padding: 11px 16px;
    font-size: 11px;
    font-weight: 700;
    color: #718096;
    text-transform: uppercase;
    letter-spacing: .6px;
    border-bottom: 1px solid #e2e8f0;
  }
  td {
    padding: 12px 16px;
    border-bottom: 1px solid #f0f4f8;
    font-size: 13px;
    vertical-align: top;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f7fafc; }

  .pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .4px;
  }
  .pill.replied { background: #d4edda; color: #155724; }
  .pill.skipped { background: #fff3cd; color: #856404; }
  .pill.error   { background: #f8d7da; color: #721c24; }

  pre {
    background: #f7fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 100px;
    overflow: auto;
    margin-top: 4px;
  }

  .empty {
    text-align: center;
    padding: 48px 20px;
    color: #a0aec0;
    font-size: 14px;
  }
  .empty .icon { font-size: 40px; margin-bottom: 12px; }

  /* ── how it works ── */
  .pipeline {
    display: flex;
    align-items: center;
    gap: 0;
    margin-bottom: 28px;
    flex-wrap: wrap;
  }
  .step {
    background: #fff;
    border-radius: 10px;
    padding: 14px 18px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    font-size: 12px;
    text-align: center;
    min-width: 130px;
    flex: 1;
  }
  .step .icon { font-size: 22px; margin-bottom: 4px; }
  .step .title { font-weight: 700; font-size: 12px; color: #2d3748; }
  .step .sub { color: #718096; font-size: 11px; margin-top: 2px; }
  .arrow { color: #cbd5e0; font-size: 20px; padding: 0 4px; }
</style>
</head>
<body>

<nav>
  <span class="logo">✉️</span>
  <h1>Email Data Extractor</h1>
  <span class="badge">● Live</span>
</nav>

<main>

  <!-- stat cards -->
  {% set replied = runs | selectattr('status','equalto','replied') | list | length %}
  {% set skipped = runs | selectattr('status','equalto','skipped') | list | length %}
  {% set errors  = runs | selectattr('status','equalto','error')   | list | length %}
  <div class="cards">
    <div class="card">
      <div class="num">{{ runs | length }}</div>
      <div class="lbl">Total Processed</div>
    </div>
    <div class="card green">
      <div class="num">{{ replied }}</div>
      <div class="lbl">Replied</div>
    </div>
    <div class="card yellow">
      <div class="num">{{ skipped }}</div>
      <div class="lbl">Skipped (no attachment)</div>
    </div>
    <div class="card red">
      <div class="num">{{ errors }}</div>
      <div class="lbl">Errors</div>
    </div>
  </div>

  <!-- pipeline steps -->
  <div class="pipeline">
    <div class="step"><div class="icon">📥</div><div class="title">Fetch Email</div><div class="sub">IMAP / Gmail</div></div>
    <div class="arrow">›</div>
    <div class="step"><div class="icon">📎</div><div class="title">Extract Attachment</div><div class="sub">PDF / Image</div></div>
    <div class="arrow">›</div>
    <div class="step"><div class="icon">🔍</div><div class="title">OCR / Vision</div><div class="sub">Printed or Handwritten</div></div>
    <div class="arrow">›</div>
    <div class="step"><div class="icon">🔎</div><div class="title">Regex Parse</div><div class="sub">No API needed</div></div>
    <div class="arrow">›</div>
    <div class="step"><div class="icon">📤</div><div class="title">Reply</div><div class="sub">Same thread</div></div>
  </div>

  <!-- action bar -->
  <div class="action-bar">
    <form method="POST" action="/run">
      <button class="btn" type="submit">
        <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
        Run Now
      </button>
    </form>
    {% if message %}
    <div class="toast {{ 'err' if 'Error' in message or 'error' in message else 'ok' }}">{{ message }}</div>
    {% endif %}
    <span style="margin-left:auto;font-size:12px;color:#a0aec0;">Auto-scans every 2 minutes</span>
  </div>

  <!-- latest email card -->
  <div class="table-wrap">
    <div class="table-header">
      📋 Latest Processed Email
    </div>
    {% if latest %}
    <table>
      <thead>
        <tr>
          <th>Time (UTC)</th>
          <th>Sender</th>
          <th>Subject</th>
          <th>Status</th>
          <th>Extracted Data</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td style="white-space:nowrap;color:#718096;">{{ latest.ts }}</td>
          <td>{{ latest.sender }}</td>
          <td>{{ latest.subject }}</td>
          <td><span class="pill {{ latest.status }}">{{ latest.status }}</span></td>
          <td><pre>{{ latest.detail }}</pre></td>
        </tr>
      </tbody>
    </table>
    {% else %}
    <div class="empty">
      <div class="icon">📭</div>
      <div>No emails processed yet. Send an email with a PDF/image attachment to <strong>{{ inbox }}</strong>, then click <strong>Run Now</strong>.</div>
    </div>
    {% endif %}
  </div>

</main>
</body>
</html>
"""


import os as _os

@app.route("/", methods=["GET"])
def index():
    return render_template_string(DASHBOARD_HTML, latest=latest_run(),
                                  inbox=_os.environ.get("EMAIL_USER", ""), message=None)


@app.route("/run", methods=["POST"])
def trigger_run():
    if not _running.acquire(blocking=False):
        return render_template_string(DASHBOARD_HTML, latest=latest_run(),
                                      inbox=_os.environ.get("EMAIL_USER", ""),
                                      message="A run is already in progress — try again shortly.")
    try:
        result = run_once()
        msg = f"Done. Processed: {result['processed']} | Skipped: {result['skipped']} | Errors: {len(result['errors'])}"
        if result["errors"]:
            msg += " — " + "; ".join(result["errors"])
    finally:
        _running.release()

    return render_template_string(DASHBOARD_HTML, latest=latest_run(),
                                  inbox=_os.environ.get("EMAIL_USER", ""), message=msg)


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
