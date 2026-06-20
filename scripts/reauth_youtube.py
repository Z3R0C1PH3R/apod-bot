#!/usr/bin/env python3
"""Refresh the local YouTube OAuth token used by upload.py."""

import os
import sys
from pathlib import Path

# Required for localhost http redirect during OAuth
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Google import Create_Service
from utils import setup_logging

CLIENT_SECRET_FILE = (
    "client_secret_972392739439-jusfhmj94lvc65co2gnruul7geg6imnf.apps.googleusercontent.com.json"
)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

if __name__ == "__main__":
    setup_logging()
    print("Opening browser for Google login...")
    print("Leave this terminal open until you see 'YouTube token saved'.")
    print("Do not copy the localhost URL — the script handles it automatically.\n")
    service = Create_Service(CLIENT_SECRET_FILE, "youtube", "v3", SCOPES)
    if service is None:
        raise SystemExit("YouTube authentication failed")
    print("YouTube token saved to token_youtube_v3.pickle")
