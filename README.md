# Email Attachment Extractor

An automated pipeline that monitors a Gmail inbox, reads PDF and image attachments, extracts structured data using OCR and regex, and replies to the sender in the same email thread — no LLM or external API required.

---

## How it works

1. **Fetch** — connects to Gmail via IMAP and checks for unread emails (up to 5 per scan)
2. **Read Attachment** — identifies PDF and image files (PNG, JPG, TIFF, BMP)
3. **OCR Extraction** — uses `pdfplumber` for digital PDFs with a text layer; falls back to `Tesseract` OCR for scanned or image-based files
4. **Field Parsing** — extracts common fields using regex: name, email, phone, date, amount, PAN number, account number, reference number, skills
5. **Auto Reply** — sends the extracted data back to the original sender in the same thread using the `In-Reply-To` header

Each message is processed exactly once (idempotency via SQLite).

---

## Project structure

```
.
├── app.py                   # Flask web server + APScheduler background job
├── email_data_extractor.py  # Core 5-stage pipeline
├── requirements.txt         # Python dependencies
├── Procfile                 # gunicorn start command for Railway
├── railway.json             # Railway deployment config
├── nixpacks.toml            # Installs tesseract + poppler system packages
├── .env.example             # Environment variable template (no values)
└── .gitignore               # Excludes .env and the SQLite database
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in your values. Never commit `.env`.

| Variable | Description |
|---|---|
| `IMAP_HOST` | IMAP server (default: `imap.gmail.com`) |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `EMAIL_USER` | Gmail address to monitor |
| `EMAIL_PASS` | Gmail App Password (16 characters, from Google Account > Security > App Passwords) |

> **Gmail setup:** Enable 2-Step Verification on your Google account, then generate an App Password at `myaccount.google.com/apppasswords`. Use that password as `EMAIL_PASS`, not your real Gmail password. Also enable IMAP under Gmail Settings > See all settings > Forwarding and POP/IMAP.

---

## Local setup

**System requirements:** Python 3.10+, Tesseract OCR, Poppler

```bash
# macOS
brew install tesseract poppler

# Ubuntu / Debian
sudo apt install tesseract-ocr poppler-utils

# Install Python dependencies
pip install -r requirements.txt

# Copy and fill environment variables
cp .env.example .env

# Run
python app.py
```

Open `http://localhost:5000` to see the dashboard.

---

## Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) and create a new project from the GitHub repo
3. Add the environment variables under the **Variables** tab
4. Railway reads `nixpacks.toml` to install Tesseract and Poppler automatically
5. Go to **Settings > Networking > Generate Domain** for a public URL

The app scans the inbox automatically every 2 minutes. Use the **Run Now** button on the dashboard to trigger an immediate scan.

---

## Extracted fields

The regex parser attempts to extract the following fields from any document:

- `name` — labeled name field
- `email` — email address pattern
- `phone` — 10-digit or international phone number
- `date` — common date formats (DD/MM/YYYY, YYYY-MM-DD, Month DD YYYY)
- `amount` — currency values (Rs., INR, USD, $, ₹)
- `pan_number` — Indian PAN format (AAAAA9999A)
- `account_number` — labeled account number
- `reference_number` — labeled reference/ref number
- `skills` — skills section (useful for resume processing)
- `raw_text_preview` — first 300 characters of extracted text

Fields that are not found in the document are returned as `null`.

---

## Security notes

- Credentials are loaded exclusively from environment variables — nothing is hardcoded
- The `.env` file and the SQLite database (`processed_messages.sqlite3`) are excluded from version control via `.gitignore`
- Each email is marked as processed after reply, so re-runs never send duplicate replies
