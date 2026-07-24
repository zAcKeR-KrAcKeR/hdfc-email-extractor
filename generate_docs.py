"""
Generates docs/Email_Attachment_Extractor_Documentation.pdf
Run once: python generate_docs.py
"""
from fpdf import FPDF
import os

os.makedirs("docs", exist_ok=True)

class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(80, 80, 80)
        self.cell(0, 8, "Email Attachment Extractor - Technical Documentation", align="R")
        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def title_block(self, text):
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(28, 28, 28)
        self.ln(4)
        self.cell(0, 12, text)
        self.ln(12)
        self.set_draw_color(28, 28, 28)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def section(self, text):
        self.ln(4)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(28, 28, 28)
        self.cell(0, 8, text)
        self.ln(8)
        self.set_draw_color(180, 180, 180)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(60, 60, 60)
        self.multi_cell(0, 6, text)
        self.ln(2)

    def code(self, text):
        self.set_fill_color(245, 245, 245)
        self.set_font("Courier", "", 9)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 5.5, text, fill=True, border=1)
        self.ln(2)

    def step_row(self, num, title, detail):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(28, 28, 28)
        self.set_fill_color(240, 240, 240)
        self.cell(10, 7, str(num), fill=True, align="C")
        self.cell(50, 7, title, fill=True)
        self.set_font("Helvetica", "", 10)
        self.set_fill_color(250, 250, 250)
        self.cell(0, 7, detail, fill=True)
        self.ln(7)
        self.ln(1)

    def kv(self, key, val):
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(28, 28, 28)
        self.cell(52, 6, key)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(60, 60, 60)
        self.multi_cell(self.epw - 52, 6, val)


pdf = PDF()
pdf.set_margins(10, 15, 10)
pdf.set_auto_page_break(auto=True, margin=18)
pdf.add_page()

# -- Cover --
pdf.title_block("Email Attachment Extractor")
pdf.set_font("Helvetica", "", 11)
pdf.set_text_color(80, 80, 80)
pdf.cell(0, 7, "Automated PDF / Image Extraction and Reply Pipeline")
pdf.ln(7)
pdf.set_font("Helvetica", "", 9)
pdf.cell(0, 6, "Live demo : https://hdfc-email-extractor.onrender.com")
pdf.ln(6)
pdf.cell(0, 6, "Source    : https://github.com/zAcKeR-KrAcKeR/hdfc-email-extractor")
pdf.ln(6)
pdf.ln(4)

# -- Overview --
pdf.section("1. Overview")
pdf.body(
    "This system monitors a Gmail inbox via IMAP, detects incoming emails that carry PDF or "
    "image attachments, extracts structured data from those attachments using OCR and regex "
    "pattern matching, and automatically replies to the original sender in the same email thread "
    "with the extracted fields. No LLM or third-party AI API is required.\n\n"
    "The pipeline is designed around five discrete, independently testable stages so that any "
    "individual component (the email reader, the OCR extractor, the field parser, or the SMTP "
    "sender) can be swapped or tested in isolation."
)

# -- Pipeline --
pdf.section("2. Pipeline Architecture")
pdf.body("Each incoming email passes through the following five stages in sequence:")
pdf.ln(1)
steps = [
    ("1", "Fetch Email",
     "Connect via IMAP SSL. Search last 3 days. Process up to 5 per run."),
    ("2", "Read Attachment",
     "Walk the MIME tree. Accept .pdf .png .jpg .jpeg .tiff .bmp."),
    ("3", "OCR Extraction",
     "Digital PDFs: pdfplumber. Scanned PDFs / images: Tesseract OCR."),
    ("4", "Field Parsing",
     "Regex patterns: name, email, phone, date, amount, PAN, account, skills."),
    ("5", "Auto Reply",
     "In-Reply-To + References headers keep it threaded. SMTP port 587."),
]
for s in steps:
    pdf.step_row(*s)
pdf.ln(2)

# -- Idempotency --
pdf.section("3. Idempotency")
pdf.body(
    "Each processed Message-ID is recorded in a local SQLite database "
    "(processed_messages.sqlite3). Before processing any email the pipeline checks this ledger. "
    "If the Message-ID is already present the email is skipped. This guarantees that a re-run "
    "or crash-recovery never sends duplicate replies, even if the service restarts mid-batch."
)

# -- Extracted Fields --
pdf.section("4. Extracted Fields")
pdf.body("The regex parser attempts to extract the following fields from every document:")
fields = [
    ("name",             "Labeled name field (Name:, Applicant:, Candidate:)"),
    ("email",            "Email address matching standard RFC 5322 pattern"),
    ("phone",            "10-digit Indian or international phone number"),
    ("date",             "Common formats: DD/MM/YYYY, YYYY-MM-DD, Month DD YYYY"),
    ("amount",           "Currency values preceded by Rs., INR, USD, $ or keywords"),
    ("pan_number",       "Indian PAN: 5 uppercase letters + 4 digits + 1 uppercase letter"),
    ("account_number",   "Labeled account number field"),
    ("reference_number", "Labeled reference / ref / ref no field"),
    ("skills",           "Text following a Skills: or Technologies: heading"),
    ("raw_text_preview", "First 300 characters of extracted text for audit purposes"),
]
for k, v in fields:
    pdf.kv(f"  {k}", v)
pdf.ln(3)

pdf.add_page()

# -- Setup --
pdf.section("5. Setup and Configuration")
pdf.body("All configuration is supplied via environment variables. Nothing is hardcoded.")
pdf.ln(1)
vars_ = [
    ("IMAP_HOST",  "IMAP server hostname",                        "imap.gmail.com"),
    ("SMTP_HOST",  "SMTP server hostname",                        "smtp.gmail.com"),
    ("EMAIL_USER", "Gmail address to monitor",                    "your@gmail.com"),
    ("EMAIL_PASS", "Gmail App Password (16 chars, Google Account > Security > App Passwords)",
     "xxxx xxxx xxxx xxxx"),
]
pdf.set_fill_color(240, 240, 240)
pdf.set_font("Helvetica", "B", 9)
pdf.cell(38, 7, "Variable",    fill=True, border=1)
pdf.cell(90, 7, "Description", fill=True, border=1)
pdf.cell(0,  7, "Example",     fill=True, border=1)
pdf.ln(7)
pdf.set_font("Helvetica", "", 9)
pdf.set_fill_color(255, 255, 255)
for k, d, ex in vars_:
    h = max(6, len(d)//55 * 6 + 6)
    pdf.cell(38, 6, k,  border=1)
    pdf.cell(90, 6, d,  border=1)
    pdf.cell(0,  6, ex, border=1)
    pdf.ln(6)
pdf.ln(4)

pdf.section("6. Gmail Prerequisites")
pdf.body(
    "1. Enable 2-Step Verification on the Gmail account.\n"
    "2. Go to myaccount.google.com/apppasswords and generate an App Password. "
    "Use the 16-character key as EMAIL_PASS (not your real Gmail password).\n"
    "3. In Gmail Settings > See all settings > Forwarding and POP/IMAP, "
    "set IMAP access to Enabled."
)

pdf.section("7. Running Locally")
pdf.body("Install Python 3.10+, then:")
pdf.code(
    "pip install -r requirements.txt\n"
    "cp .env.example .env          # fill in your values\n"
    "python app.py                 # starts Flask on http://localhost:5000"
)
pdf.body(
    "Open http://localhost:5000. The dashboard shows the pipeline diagram, the monitored "
    "inbox address, and the latest processed email with extracted fields. "
    "Click Run Now to trigger an immediate inbox scan without waiting for the 2-minute auto-scan."
)

pdf.section("8. Deploying to Render")
pdf.body(
    "The repository includes a render.yaml blueprint. Steps:\n"
    "1. Push the repo to GitHub.\n"
    "2. Go to dashboard.render.com > New > Blueprint > select the repo.\n"
    "3. Add environment variables (IMAP_HOST, SMTP_HOST, EMAIL_USER, EMAIL_PASS) "
    "in the service Environment tab.\n"
    "4. Render builds and deploys automatically.\n\n"
    "Render does not block outbound SMTP (port 587 STARTTLS), so email replies "
    "are delivered without any additional network configuration."
)

pdf.section("9. Web Dashboard")
pdf.body(
    "The Flask web server exposes three endpoints:\n\n"
    "  GET  /        Renders the dashboard: pipeline diagram, monitored inbox, "
    "and the most recently processed email with extracted fields.\n\n"
    "  POST /run     Triggers an immediate inbox scan. "
    "Returns the dashboard with a result summary.\n\n"
    "  GET  /status  Returns {\"status\": \"ok\"} used as a health-check by Render."
)

pdf.section("10. Security Notes")
pdf.body(
    "- All credentials are loaded from environment variables. Nothing is hardcoded.\n"
    "- The .env file and SQLite database are excluded from version control via .gitignore.\n"
    "- Gmail App Passwords are scoped to a single application and can be revoked independently.\n"
    "- Extracted data is stored only in the SQLite run_log table and is not transmitted "
    "to any third party.\n"
    "- Replies use In-Reply-To and References headers, sent only to the original sender."
)

pdf.section("11. Technology Stack")
stack = [
    ("Flask",       "Web framework and dashboard UI"),
    ("APScheduler", "Background scheduler (inbox scan every 2 minutes)"),
    ("imaplib",     "Python standard library - IMAP email fetching"),
    ("smtplib",     "Python standard library - SMTP email sending"),
    ("pdfplumber",  "PDF text layer extraction for digital PDFs"),
    ("pytesseract", "OCR for scanned PDFs and image attachments (optional)"),
    ("SQLite",      "Idempotency ledger and run log"),
    ("Render",      "Cloud hosting - always-on, no SMTP restrictions"),
    ("gunicorn",    "Production WSGI server"),
]
for name, desc in stack:
    pdf.kv(f"  {name}", desc)

pdf.output("docs/Email_Attachment_Extractor_Documentation.pdf")
print("Generated: docs/Email_Attachment_Extractor_Documentation.pdf")
