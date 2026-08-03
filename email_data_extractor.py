"""
Email -> Attachment -> Extraction -> Reply pipeline
====================================================
Reads unseen emails from an inbox, pulls out PDF/image attachments,
extracts data using OCR + regex, and replies in the same thread.

Stages:
  1. fetch_unread_emails()   connect + list new messages
  2. extract_attachments()   pull PDF/image files out of a message
  3. get_text()              OCR/pdfplumber to get raw text
  4. parse_fields()          regex extraction — no LLM needed
  5. send_reply()            reply in-thread to the original sender

Install:
    pip install -r requirements.txt
    # + system packages: tesseract-ocr, poppler-utils
"""

import imaplib
import smtplib
import email
import email.utils
import os
import io
import re
import json
import sqlite3
import logging
from email.mime.text import MIMEText
from email.header import decode_header

import pdfplumber
import socket

# Force IPv4 resolution to prevent [Errno 101] Network is unreachable on cloud environments (Render)
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

try:

    import pytesseract
    from pdf2image import convert_from_bytes
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Config — everything from environment variables
# --------------------------------------------------------------------------
IMAP_HOST    = os.environ.get("IMAP_HOST", "imap.gmail.com")
SMTP_HOST    = os.environ.get("SMTP_HOST", "smtp.gmail.com")
EMAIL_USER   = os.environ.get("EMAIL_USER", "")
EMAIL_PASS   = os.environ.get("EMAIL_PASS", "")
PROCESSED_DB = os.environ.get("PROCESSED_DB", "processed_messages.sqlite3")



# --------------------------------------------------------------------------
# Idempotency — never double-reply the same message
# --------------------------------------------------------------------------
def _db():
    conn = sqlite3.connect(PROCESSED_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed "
        "(message_id TEXT PRIMARY KEY, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_log "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        " sender TEXT, subject TEXT, status TEXT, detail TEXT)"
    )
    return conn


def already_processed(message_id: str) -> bool:
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)
        ).fetchone()
    return row is not None


def mark_processed(message_id: str):
    with _db() as conn:
        conn.execute("INSERT OR IGNORE INTO processed VALUES (?, CURRENT_TIMESTAMP)", (message_id,))


def log_run(sender, subject, status, detail=""):
    with _db() as conn:
        conn.execute(
            "INSERT INTO run_log (sender, subject, status, detail) VALUES (?,?,?,?)",
            (sender, subject, status, detail[:10000]),
        )


def recent_runs(limit=20):
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, sender, subject, status, detail FROM run_log "
            "WHERE status = 'replied' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(zip(["ts", "sender", "subject", "status", "detail"], r)) for r in rows]


def latest_run():
    """Return only the single most recent replied email."""
    with _db() as conn:
        row = conn.execute(
            "SELECT ts, sender, subject, status, detail FROM run_log "
            "WHERE status = 'replied' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row:
        return dict(zip(["ts", "sender", "subject", "status", "detail"], row))
    return None


# --------------------------------------------------------------------------
# Stage 1: fetch emails received in the last 3 days that haven't been processed
# --------------------------------------------------------------------------
def fetch_unread_emails():
    """Yields (msg, message_id, sender_email, subject) for each unprocessed email."""
    import datetime
    email_user = os.environ.get("EMAIL_USER", EMAIL_USER)
    email_pass = os.environ.get("EMAIL_PASS", EMAIL_PASS)
    imap_host = os.environ.get("IMAP_HOST", IMAP_HOST)

    if not email_user or not email_pass:
        log.error("EMAIL_USER or EMAIL_PASS environment variables are missing.")
        raise ValueError("EMAIL_USER and EMAIL_PASS environment variables must be configured on Render.")

    imap = imaplib.IMAP4_SSL(imap_host)
    imap.login(email_user, email_pass)
    imap.select("INBOX")

    # Fetch all email IDs received in the last 7 days
    since = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%d-%b-%Y")
    status, data = imap.search(None, f'(SINCE "{since}")')

    ids = data[0].split() if data and data[0] else []
    log.info(f"Found {len(ids)} total email(s) in the last 7 days.")

    yielded_count = 0
    # Process from newest to oldest
    for num in reversed(ids):
        status, msg_data = imap.fetch(num, "(RFC822)")
        if not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
            continue
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        message_id = msg.get("Message-ID", "")
        if not message_id or already_processed(message_id):
            continue

        sender_email = email.utils.parseaddr(msg.get("From"))[1]
        subject_header = msg.get("Subject", "No Subject")
        decoded_parts = decode_header(subject_header)
        subject_raw, encoding = decoded_parts[0] if decoded_parts else ("No Subject", None)
        if isinstance(subject_raw, bytes):
            subject = subject_raw.decode(encoding or "utf-8", errors="ignore")
        else:
            subject = str(subject_raw)

        yield msg, message_id, sender_email, subject
        yielded_count += 1
        if yielded_count >= 5:
            break

    imap.close()
    imap.logout()



# --------------------------------------------------------------------------
# Stage 2: pull attachments out, classify by type
# --------------------------------------------------------------------------
def extract_attachments(msg):
    """Returns list of (filename, file_bytes, kind) where kind is 'pdf' or 'image'."""
    attachments = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue

        content = part.get_payload(decode=True)
        lower = filename.lower()
        if lower.endswith(".pdf"):
            attachments.append((filename, content, "pdf"))
        elif lower.endswith((".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
            attachments.append((filename, content, "image"))

    return attachments


# --------------------------------------------------------------------------
# Stage 3: extract raw text
# --------------------------------------------------------------------------
def get_text_from_pdf(file_bytes: bytes) -> str:
    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    text = "\n".join(text_parts).strip()

    if len(text) > 20:
        return text  # real text layer — fast and exact

    # Scanned / image-only PDF — needs Tesseract
    if not OCR_AVAILABLE:
        return "[scanned PDF — OCR not available on this server]"
    images = convert_from_bytes(file_bytes)
    return "\n".join(pytesseract.image_to_string(img) for img in images)


def get_text_from_image(file_bytes: bytes) -> str:
    if not OCR_AVAILABLE:
        return "[image attachment — OCR not available on this server]"
    img = Image.open(io.BytesIO(file_bytes))
    return pytesseract.image_to_string(img)


# --------------------------------------------------------------------------
# Stage 4: regex-based field extraction — no LLM, no API key needed
# --------------------------------------------------------------------------
def parse_fields(raw_text: str) -> dict:
    """Extract common document fields using regex patterns."""

    def first(pattern, flags=re.IGNORECASE):
        m = re.search(pattern, raw_text, flags)
        return m.group(1).strip() if m else None

    # Email
    email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", raw_text)

    # Phone — Indian (10 digit) or international
    phone_match = re.search(r"(\+?[0-9]{1,3}[\s\-]?)?(\(?\d{3,5}\)?[\s\-]?\d{3,5}[\s\-]?\d{3,5})", raw_text)

    # Date — DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, Month DD YYYY
    date_match = re.search(
        r"\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}[\/\-]\d{2}[\/\-]\d{2}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
        raw_text, re.IGNORECASE
    )

    # Amount — currency symbol or keywords followed by number
    amount_match = re.search(
        r"(?:Rs\.?|INR|USD|\$|₹)\s*([\d,]+(?:\.\d{1,2})?)|"
        r"(?:amount|total|balance|fee|charges)[^\d]{0,10}([\d,]+(?:\.\d{1,2})?)",
        raw_text, re.IGNORECASE
    )
    amount = None
    if amount_match:
        raw_amt = amount_match.group(1) or amount_match.group(2)
        if raw_amt:
            amount = raw_amt.replace(",", "")

    # PAN number — Indian format: 5 letters, 4 digits, 1 letter
    pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", raw_text)

    # Account / reference number — labeled
    account  = first(r"(?:account\s*(?:no|number|#)[:\s]+)([A-Z0-9\-]{5,20})")
    ref      = first(r"(?:ref(?:erence)?\s*(?:no|number|#|id)?[:\s]+)([A-Z0-9\-]{4,20})")

    # Name — labeled field
    name     = first(r"(?:name|applicant|candidate)[:\s]+([A-Za-z]+(?: [A-Za-z]+){1,4})")

    # Skills section (useful for resumes)
    skills_match = re.search(r"(?:skills?|technologies)[:\s\n]+([^\n]{10,300})", raw_text, re.IGNORECASE)

    return {
        "name":             name,
        "email":            email_match.group(0) if email_match else None,
        "phone":            phone_match.group(0).strip() if phone_match else None,
        "date":             date_match.group(0) if date_match else None,
        "amount":           amount,
        "pan_number":       pan_match.group(1) if pan_match else None,
        "account_number":   account,
        "reference_number": ref,
        "skills":           skills_match.group(1).strip() if skills_match else None,
        "raw_text_preview": raw_text[:300].replace("\n", " "),
    }


# --------------------------------------------------------------------------
# Stage 5: reply in-thread to the original sender
# --------------------------------------------------------------------------
def send_reply(to_addr: str, subject: str, original_message_id: str, extracted_records: list):
    email_user = os.environ.get("EMAIL_USER", EMAIL_USER)
    email_pass = os.environ.get("EMAIL_PASS", EMAIL_PASS)
    smtp_host = os.environ.get("SMTP_HOST", SMTP_HOST)

    body_lines = ["Hello,\n\nHere is the text extracted from your attachment(s):\n"]
    for i, record in enumerate(extracted_records, 1):
        filename = record.get("_source_file", f"Attachment {i}")
        raw_text = record.get("_raw_text", record.get("error", "Could not extract text."))
        body_lines.append(f"--- {filename} ---")
        body_lines.append(raw_text)
        body_lines.append("")
    body_lines.append("\nThis is an automated response. Please do not reply to this email.")
    body = "\n".join(body_lines)

    resend_api_key = os.environ.get("RESEND_API_KEY")
    if resend_api_key:
        import urllib.request
        try:
            req_data = json.dumps({
                "from": email_user,
                "to": [to_addr],
                "subject": "Re: " + subject,
                "text": body,
                "headers": {
                    "In-Reply-To": original_message_id,
                    "References": original_message_id
                }
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=req_data,
                headers={
                    "Authorization": f"Bearer {resend_api_key}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info(f"Replied via Resend API to {to_addr} | subject: {subject}")
                return
        except Exception as e:
            log.warning(f"Resend API send failed: {e} — falling back to standard SMTP...")

    reply = MIMEText(body)
    reply["From"]       = email_user
    reply["To"]         = to_addr
    reply["Subject"]    = "Re: " + subject
    reply["In-Reply-To"] = original_message_id   # keeps it threaded
    reply["References"]  = original_message_id

    import time, ssl

    last_err = None
    # Try port 465 SSL first (fastest on Render), then 587 STARTTLS as fallback
    attempts = [(465, True), (587, False), (465, True)]
    for port, use_ssl in attempts:
        try:
            if use_ssl:
                ctx = ssl.create_default_context()
                conn = smtplib.SMTP_SSL(smtp_host, port, context=ctx, timeout=15)
            else:
                conn = smtplib.SMTP(smtp_host, port, timeout=15)
                conn.ehlo()
                conn.starttls()
                conn.ehlo()
            with conn:
                conn.login(email_user, email_pass)
                conn.sendmail(email_user, [to_addr], reply.as_string())
            log.info(f"Replied to {to_addr} | subject: {subject}")
            return
        except Exception as e:
            last_err = e
            log.warning(f"SMTP attempt port {port} failed: {e} — retrying...")
            time.sleep(1)
    raise RuntimeError(f"All SMTP attempts failed: {last_err}")




# --------------------------------------------------------------------------
# Orchestration — one full pass over the inbox
# --------------------------------------------------------------------------
def run_once() -> dict:
    results = {"processed": 0, "skipped": 0, "errors": []}

    try:
        for msg, message_id, sender_email, subject in fetch_unread_emails():
            attachments = extract_attachments(msg)
            if not attachments:
                mark_processed(message_id)
                results["skipped"] += 1
                log_run(sender_email, subject, "skipped", "no attachments")
                continue

            extracted_records = []
            for filename, content, kind in attachments:
                try:
                    if kind == "pdf":
                        raw_text = get_text_from_pdf(content)
                    else:
                        raw_text = get_text_from_image(content)

                    record = parse_fields(raw_text)
                    record["_source_file"] = filename
                    record["_raw_text"] = raw_text  # kept for reply body
                    extracted_records.append(record)
                except Exception as e:
                    log.error(f"Error processing {filename}: {e}")
                    extracted_records.append({"_source_file": filename, "error": str(e)})

            send_reply(sender_email, subject, message_id, extracted_records)
            mark_processed(message_id)
            results["processed"] += 1
            full_text = "\n\n".join(
                f"[{r.get('_source_file','')}]\n{r.get('_raw_text', r.get('error',''))}"
                for r in extracted_records
            )
            log_run(sender_email, subject, "replied", full_text)

    except Exception as e:
        log.error(f"run_once failed: {e}")
        results["errors"].append(str(e))

    return results


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
