#!/usr/bin/env python3
"""Set up Claude Code hooks to point to this dashboard's hook_receiver.py.

Merges hook config into ~/.claude/settings.json, preserving existing settings.
"""

import json
import os
import sys

HOOK_RECEIVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_receiver.py")
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")

HOOK_EVENTS = [
    "SessionStart", "Notification", "SubagentStart", "SubagentStop",
    "Stop", "UserPromptSubmit", "SessionEnd",
]


def main():
    # Load existing settings
    settings = {}
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)

    hooks = settings.setdefault("hooks", {})

    command = f"python {HOOK_RECEIVER.replace(os.sep, '/')}"
    hook_entry = {
        "matcher": "",
        "hooks": [{"type": "command", "command": command}],
    }

    for event in HOOK_EVENTS:
        existing = hooks.get(event, [])
        # Don't add if already configured for this receiver
        already = False
        for entry in existing:
            # Check new format (matcher + hooks array)
            for h in entry.get("hooks", []):
                if "hook_receiver.py" in h.get("command", ""):
                    already = True
            # Check old format (bare type + command) and migrate
            if "hook_receiver.py" in entry.get("command", ""):
                already = True
        if already:
            continue
        existing.append(hook_entry)
        hooks[event] = existing

    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"Hooks configured in {SETTINGS_PATH}")
    print(f"Receiver: {command}")


if __name__ == "__main__":
    main()
