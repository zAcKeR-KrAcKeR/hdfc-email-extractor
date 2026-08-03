#!/usr/bin/env bash
set -e

if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq 2>/dev/null || true
    apt-get install -y -qq tesseract-ocr poppler-utils 2>/dev/null || true
fi

pip install -r requirements.txt

