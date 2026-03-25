#!/usr/bin/env python3
"""Hook receiver script for Claude Code.

Reads JSON from stdin and POSTs it to the dashboard server.
If the dashboard isn't running, the hook is silently dropped.
"""

import io
import json
import sys
import urllib.request

HOOK_URL = "http://127.0.0.1:8765/hook"


def main():
    try:
        # Force UTF-8 reading — Claude Code sends JSON as UTF-8
        raw = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8").read()
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
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
