"""
Email -> Attachment -> Extraction -> Reply pipeline
====================================================
Reads unseen emails from an inbox, pulls out PDF/image attachments
(computer-printed or handwritten scans), extracts the data, and replies
to the same sender in the same thread with the extracted data.

Stages:
  1. fetch_unread_emails()       connect + list new messages
  2. extract_attachments()       pull PDF/image files out of a message
  3. get_text_from_attachment()  OCR/parse, printed vs handwritten branch
  4. structure_with_llm()        turn raw text into clean JSON
  5. send_reply()                reply in-thread to the original sender

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
import json
import sqlite3
import logging
from email.mime.text import MIMEText
from email.header import decode_header

import pdfplumber
import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Config — everything from environment variables
# --------------------------------------------------------------------------
IMAP_HOST    = os.environ.get("IMAP_HOST", "imap.gmail.com")
SMTP_HOST    = os.environ.get("SMTP_HOST", "smtp.gmail.com")
EMAIL_USER   = os.environ["EMAIL_USER"]
EMAIL_PASS   = os.environ["EMAIL_PASS"]   # Gmail App Password, not your real password
PROCESSED_DB = os.environ.get("PROCESSED_DB", "processed_messages.sqlite3")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


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
            (sender, subject, status, detail[:2000]),
        )


def recent_runs(limit=20):
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, sender, subject, status, detail FROM run_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(zip(["ts", "sender", "subject", "status", "detail"], r)) for r in rows]


# --------------------------------------------------------------------------
# Stage 1: fetch unread emails
# --------------------------------------------------------------------------
def fetch_unread_emails():
    """Yields (msg, message_id, sender_email, subject) for each unseen email."""
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(EMAIL_USER, EMAIL_PASS)
    imap.select("INBOX")

    status, data = imap.search(None, "UNSEEN")
    ids = data[0].split()
    log.info(f"Found {len(ids)} unread email(s).")

    # Only process the 5 most recent per run to avoid overload
    ids = ids[-5:]

    for num in ids:
        status, msg_data = imap.fetch(num, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        message_id = msg.get("Message-ID", "")
        sender_email = email.utils.parseaddr(msg.get("From"))[1]
        subject_raw, encoding = decode_header(msg.get("Subject", "No Subject"))[0]
        if isinstance(subject_raw, bytes):
            subject = subject_raw.decode(encoding or "utf-8", errors="ignore")
        else:
            subject = subject_raw

        if already_processed(message_id):
            log.info(f"Skipping already-processed {message_id}")
            continue

        yield msg, message_id, sender_email, subject

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

    # Scanned / image-only PDF — rasterize and OCR
    images = convert_from_bytes(file_bytes)
    return "\n".join(pytesseract.image_to_string(img) for img in images)


def get_text_from_image(file_bytes: bytes, handwritten_hint: bool = False) -> str:
    img = Image.open(io.BytesIO(file_bytes))

    if not handwritten_hint:
        return pytesseract.image_to_string(img)

    # Handwritten: Tesseract is unreliable. Route to a vision LLM instead.
    return extract_handwritten_via_vision_llm(file_bytes)


def extract_handwritten_via_vision_llm(image_bytes: bytes) -> str:
    """Send raw image to Claude Vision and ask for a plain transcription."""
    import base64
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    b64 = base64.b64encode(image_bytes).decode()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
                {
                    "type": "text",
                    "text": (
                        "Transcribe every word on this handwritten form exactly as written. "
                        "Output only the transcription, no commentary."
                    ),
                },
            ],
        }],
    )
    return response.content[0].text


# --------------------------------------------------------------------------
# Stage 4: structure raw text into JSON via LLM
# --------------------------------------------------------------------------
# Adjust fields to match the actual document your interviewer uses
EXTRACTION_SCHEMA = {
    "applicant_name":   "string or null",
    "date":             "string (YYYY-MM-DD) or null",
    "reference_number": "string or null",
    "account_number":   "string or null",
    "amount":           "number or null",
    "pan_number":       "string or null",
    "address":          "string or null",
    "raw_notes":        "anything that didn't fit the above fields",
}


def structure_with_llm(raw_text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = (
        "Extract the following fields from the document text below. "
        f"Return ONLY valid JSON matching this schema, no prose:\n{json.dumps(EXTRACTION_SCHEMA, indent=2)}\n\n"
        f"Document text:\n{raw_text}"
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "could not parse LLM output", "raw_output": raw}


# --------------------------------------------------------------------------
# Stage 5: reply in-thread to the original sender
# --------------------------------------------------------------------------
def send_reply(to_addr: str, subject: str, original_message_id: str, extracted_records: list):
    body_lines = ["Hello,\n\nHere is the data extracted from your attachment(s):\n"]
    for i, record in enumerate(extracted_records, 1):
        body_lines.append(f"--- Attachment {i}: {record.get('_source_file', '')} ---")
        clean = {k: v for k, v in record.items() if k != "_source_file"}
        body_lines.append(json.dumps(clean, indent=2))
        body_lines.append("")
    body_lines.append("\nThis is an automated response. Please do not reply to this email.")
    body = "\n".join(body_lines)

    reply = MIMEText(body)
    reply["From"]       = EMAIL_USER
    reply["To"]         = to_addr
    reply["Subject"]    = "Re: " + subject
    reply["In-Reply-To"] = original_message_id   # keeps it threaded
    reply["References"]  = original_message_id

    with smtplib.SMTP_SSL(SMTP_HOST, 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.sendmail(EMAIL_USER, [to_addr], reply.as_string())

    log.info(f"Replied to {to_addr} | subject: {subject}")


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
                        raw_text = get_text_from_image(content, handwritten_hint=False)
                        if len(raw_text.strip()) < 5:
                            raw_text = get_text_from_image(content, handwritten_hint=True)

                    record = structure_with_llm(raw_text)
                    record["_source_file"] = filename
                    extracted_records.append(record)
                except Exception as e:
                    log.error(f"Error processing {filename}: {e}")
                    extracted_records.append({"_source_file": filename, "error": str(e)})

            send_reply(sender_email, subject, message_id, extracted_records)
            mark_processed(message_id)
            results["processed"] += 1
            log_run(sender_email, subject, "replied", json.dumps(extracted_records)[:500])

    except Exception as e:
        log.error(f"run_once failed: {e}")
        results["errors"].append(str(e))

    return results


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
