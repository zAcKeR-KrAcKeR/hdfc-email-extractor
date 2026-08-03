import os
import sys
import urllib.request
import shutil
import stat
import logging

log = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
TESSERACT_EXE = os.path.join(BIN_DIR, "tesseract")
TESSDATA_DIR = os.path.join(BASE_DIR, "tessdata")
ENG_PATH = os.path.join(TESSDATA_DIR, "eng.traineddata")

STATIC_TESSERACT_URL = "https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-5.5.3/tesseract.x86_64"
TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata"


def setup_ocr() -> str:
    """Ensure a working tesseract executable and traineddata are available."""
    # 1. Check system path first
    sys_bin = shutil.which("tesseract") or "/usr/bin/tesseract"
    if os.path.exists(sys_bin):
        log.info(f"Using system Tesseract binary: {sys_bin}")
        return sys_bin

    # 2. Check if local static tesseract exists
    if os.path.exists(TESSERACT_EXE) and os.path.exists(ENG_PATH):
        os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR
        return TESSERACT_EXE

    os.makedirs(BIN_DIR, exist_ok=True)
    os.makedirs(TESSDATA_DIR, exist_ok=True)

    # 3. Download static tesseract binary if on Linux
    if sys.platform.startswith("linux"):
        if not os.path.exists(TESSERACT_EXE):
            log.info("Downloading static Tesseract binary for Linux...")
            req = urllib.request.Request(STATIC_TESSERACT_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as resp, open(TESSERACT_EXE, "wb") as f:
                f.write(resp.read())
            # Make executable
            os.chmod(TESSERACT_EXE, os.stat(TESSERACT_EXE).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            log.info("Static Tesseract binary downloaded and marked executable.")

    # 4. Download eng.traineddata if missing
    if not os.path.exists(ENG_PATH):
        log.info("Downloading eng.traineddata language model...")
        req = urllib.request.Request(TESSDATA_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, open(ENG_PATH, "wb") as f:
            f.write(resp.read())
        log.info("eng.traineddata downloaded successfully.")

    os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR
    return TESSERACT_EXE if os.path.exists(TESSERACT_EXE) else sys_bin


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("OCR Binary Path:", setup_ocr())
