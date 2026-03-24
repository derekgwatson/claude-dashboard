#!/usr/bin/env python3
"""Background worker for hook_receiver.py.

Reads JSON from stdin and POSTs it to the dashboard server.
If the dashboard isn't running, auto-starts it.
This runs as a detached process so it never blocks Claude Code.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_URL = "http://127.0.0.1:8765"
HOOK_URL = DASHBOARD_URL + "/hook"


def dashboard_running():
    try:
        urllib.request.urlopen(DASHBOARD_URL + "/api/sessions", timeout=1)
        return True
    except Exception:
        return False


def start_dashboard():
    if sys.platform == "win32":
        subprocess.Popen(
            [sys.executable, os.path.join(DIR, "app.py")],
            creationflags=0x00000008,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            [sys.executable, os.path.join(DIR, "app.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        payload = json.loads(raw)

        if not dashboard_running():
            start_dashboard()
            time.sleep(1)

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            HOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
