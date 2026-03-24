#!/usr/bin/env python3
"""One-command setup and start for Claude Session Dashboard.

Usage: python start.py

- Checks/installs dependencies
- Configures Claude Code hooks (if not already set)
- Starts the dashboard server
"""

import importlib
import json
import os
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def check_deps():
    """Install missing dependencies."""
    try:
        importlib.import_module("flask")
    except ImportError:
        print("Installing dependencies...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r",
                               os.path.join(DIR, "requirements.txt"), "-q"])


def check_hooks():
    """Set up hooks if not already configured."""
    settings = {}
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

    hooks = settings.get("hooks", {})
    already = any(
        any("hook_receiver.py" in h.get("command", "") for h in hooks.get(event, []))
        for event in ["SessionStart", "Stop"]
    )

    if not already:
        print("Configuring Claude Code hooks...")
        subprocess.check_call([sys.executable, os.path.join(DIR, "setup_hooks.py")])
    else:
        print("Hooks already configured.")


def main():
    check_deps()
    check_hooks()
    print()
    subprocess.call([sys.executable, os.path.join(DIR, "app.py")])


if __name__ == "__main__":
    main()
