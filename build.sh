#!/usr/bin/env bash
set -e
apt-get update -qq
apt-get install -y -qq tesseract-ocr poppler-utils
pip install -r requirements.txt
