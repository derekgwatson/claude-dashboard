#!/usr/bin/env python3
"""Hook receiver script for Claude Code.

Reads JSON from stdin and hands it off to a background process that POSTs
it to the dashboard server. Returns immediately so it never blocks Claude
Code's terminal rendering.
"""

import json
import os
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
SENDER = os.path.join(DIR, "hook_sender.py")


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        # Validate JSON before spawning
        json.loads(raw)

        # Fire and forget — hand off to background process
        kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen([sys.executable, SENDER], **kwargs)
        proc.stdin.write(raw.encode("utf-8"))
        proc.stdin.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
