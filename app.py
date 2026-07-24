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
<title>Email Attachment Extractor</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #f5f5f5;
    min-height: 100vh;
    color: #222;
    font-size: 14px;
  }

  header {
    background: #1c1c1c;
    color: #fff;
    padding: 0 40px;
    height: 52px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header h1 { font-size: 15px; font-weight: 600; letter-spacing: 0.2px; }
  .status-dot {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    color: #aaa;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #4caf50; }

  main { max-width: 960px; margin: 32px auto; padding: 0 24px; }

  /* description */
  .description {
    background: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    padding: 20px 24px;
    margin-bottom: 24px;
    line-height: 1.6;
    color: #444;
  }
  .description h2 { font-size: 13px; font-weight: 700; color: #111; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
  .description p  { font-size: 13px; }

  /* pipeline */
  .pipeline {
    display: flex;
    align-items: stretch;
    gap: 2px;
    margin-top: 16px;
  }
  .step {
    flex: 1;
    background: #f9f9f9;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 10px 12px;
    text-align: center;
  }
  .step .num  { font-size: 11px; color: #999; margin-bottom: 3px; }
  .step .lbl  { font-size: 12px; font-weight: 600; color: #222; }
  .step .sub  { font-size: 11px; color: #888; margin-top: 2px; }
  .arrow { color: #bbb; padding: 0 4px; display: flex; align-items: center; font-size: 16px; }

  /* action */
  .action-row {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 20px;
  }
  .btn {
    background: #1c1c1c;
    color: #fff;
    border: none;
    padding: 9px 20px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.2px;
    transition: background 0.15s;
  }
  .btn:hover { background: #333; }
  .note { font-size: 12px; color: #999; margin-left: auto; }

  .alert {
    padding: 10px 14px;
    border-radius: 4px;
    font-size: 13px;
    border-left: 3px solid;
  }
  .alert.ok  { background: #f0faf0; border-color: #4caf50; color: #2e7d32; }
  .alert.err { background: #fff5f5; border-color: #f44336; color: #c62828; }

  /* result card */
  .card {
    background: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    overflow: hidden;
  }
  .card-header {
    padding: 12px 20px;
    border-bottom: 1px solid #e0e0e0;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #555;
    background: #fafafa;
  }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left;
    padding: 10px 16px;
    font-size: 11px;
    font-weight: 700;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid #ebebeb;
    background: #fafafa;
  }
  td {
    padding: 12px 16px;
    font-size: 13px;
    vertical-align: top;
    color: #333;
  }
  .tag {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }
  .tag.replied { background: #e8f5e9; color: #2e7d32; }
  .tag.error   { background: #ffebee; color: #c62828; }
  pre {
    background: #f5f5f5;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    padding: 10px 12px;
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 140px;
    overflow: auto;
    font-family: 'Consolas', 'Courier New', monospace;
  }
  .empty {
    padding: 40px 20px;
    text-align: center;
    color: #999;
    font-size: 13px;
  }
</style>
</head>
<body>

<header>
  <h1>Email Attachment Extractor</h1>
  <div class="status-dot"><span class="dot"></span> Running</div>
</header>

<main>

  <!-- how it works -->
  <div class="description">
    <h2>How it works</h2>
    <p>
      This service monitors a Gmail inbox every 2 minutes. When an email arrives with a PDF or image
      attachment, it extracts the text using OCR (pdfplumber for digital PDFs, Tesseract for scanned
      images), parses structured fields using regex (name, email, phone, dates, amounts, PAN, reference
      numbers) and sends an automated reply to the original sender with the extracted data in the same
      thread. Each message is processed exactly once.
    </p>
    <div class="pipeline" style="margin-top:16px;">
      <div class="step"><div class="num">1</div><div class="lbl">Fetch Email</div><div class="sub">IMAP / Gmail</div></div>
      <div class="arrow">&#8250;</div>
      <div class="step"><div class="num">2</div><div class="lbl">Read Attachment</div><div class="sub">PDF or Image</div></div>
      <div class="arrow">&#8250;</div>
      <div class="step"><div class="num">3</div><div class="lbl">OCR Extraction</div><div class="sub">pdfplumber / Tesseract</div></div>
      <div class="arrow">&#8250;</div>
      <div class="step"><div class="num">4</div><div class="lbl">Field Parsing</div><div class="sub">Regex, no API</div></div>
      <div class="arrow">&#8250;</div>
      <div class="step"><div class="num">5</div><div class="lbl">Auto Reply</div><div class="sub">Same thread</div></div>
    </div>
  </div>

  <!-- trigger -->
  <div class="action-row">
    <form method="POST" action="/run">
      <button class="btn" type="submit">Run Now</button>
    </form>
    {% if message %}
    <div class="alert {{ 'err' if 'Error' in message or 'error' in message else 'ok' }}">{{ message }}</div>
    {% endif %}
    <span class="note">Inbox: <strong>{{ inbox }}</strong> &nbsp; Scans automatically every 2 min</span>
  </div>

  <!-- latest result -->
  <div class="card">
    <div class="card-header">Latest Processed Email</div>
    {% if latest %}
    <table>
      <thead>
        <tr>
          <th>Received (UTC)</th>
          <th>From</th>
          <th>Subject</th>
          <th>Result</th>
          <th>Extracted Fields</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>{{ latest.ts }}</td>
          <td>{{ latest.sender }}</td>
          <td>{{ latest.subject }}</td>
          <td><span class="tag {{ latest.status }}">{{ latest.status }}</span></td>
          <td><pre>{{ latest.detail }}</pre></td>
        </tr>
      </tbody>
    </table>
    {% else %}
    <div class="empty">
      No emails have been processed yet. Send an email with a PDF or image attachment
      to <strong>{{ inbox }}</strong> and click <strong>Run Now</strong>.
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
