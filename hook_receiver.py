#!/usr/bin/env python3
"""Hook receiver script for Claude Code.

Reads JSON from stdin and POSTs it to the dashboard server.
Designed to fail silently and return quickly so it never blocks Claude Code.
"""

import json
import sys
import urllib.request

DASHBOARD_URL = "http://127.0.0.1:8765/hook"


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        payload = json.loads(raw)

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            DASHBOARD_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        # Fail silently — never block Claude Code
        pass


if __name__ == "__main__":
    main()
