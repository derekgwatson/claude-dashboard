#!/usr/bin/env python3
"""Hook receiver script for Claude Code.

Reads JSON from stdin and POSTs it directly to the dashboard server.
Uses a short timeout so it doesn't block Claude Code's terminal.
If the dashboard isn't running, spawns it in the background.
"""

import json
import os
import subprocess
import sys
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_URL = "http://127.0.0.1:8765"
HOOK_URL = DASHBOARD_URL + "/hook"


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        payload = json.loads(raw)
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            HOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            # Dashboard probably not running — start it and retry once
            if sys.platform == "win32":
                subprocess.Popen(
                    [sys.executable, os.path.join(DIR, "app.py")],
                    creationflags=0x00000008,  # DETACHED_PROCESS
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
            import time
            time.sleep(2)
            try:
                urllib.request.urlopen(req, timeout=2)
            except Exception:
                pass
    except Exception:
        pass


if __name__ == "__main__":
    main()
