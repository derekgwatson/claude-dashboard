# Claude Session Dashboard

A lightweight status monitor for multiple Claude Code sessions. See which sessions are running, waiting for input, or need permission — at a glance.

![Dashboard](https://img.shields.io/badge/local-only-blue) ![Python 3](https://img.shields.io/badge/python-3.x-green)

## Features

- **Live session monitoring** — polls every 2 seconds
- **Attention highlighting** — sessions needing input or permission get a red border
- **Toast notifications** — Windows desktop notifications when a session needs attention
- **Auto-discovery** — finds existing Claude Code sessions on startup
- **Session labels** — double-click a session name to rename it
- **PWA installable** — runs as a standalone desktop window from Chrome
- **Cross-machine sync** — see remote sessions via cloud sync (optional)

## Quick Start

```bash
git clone <this-repo>
cd claude-dashboard
python start.py
```

Opens at http://127.0.0.1:8765. Install as a PWA from Chrome for a standalone window.

`start.py` installs dependencies and configures Claude Code hooks. After that, just run `python app.py` to start the dashboard.

## How It Works

Claude Code's [hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) fire shell commands on events. This dashboard:

1. Registers a hook receiver for key events (`SessionStart`, `Stop`, `Notification`, etc.)
2. `hook_receiver.py` reads event JSON from stdin and POSTs it to the local Flask server
3. Flask tracks session state in SQLite and serves a polling dashboard
4. A background thread rescans `~/.claude/sessions/` every 15s to catch stale statuses

Sessions run in your own terminal windows — the dashboard is status-only, no embedded terminals.

## Session States

| Status | Color | Meaning |
|--------|-------|---------|
| **Working** | Blue | Claude is actively working |
| **Waiting for Input** | Amber | Claude finished, waiting for your next prompt |
| **Permission Needed** | Red (flashing) | Claude needs you to approve a tool use |
| **Done** | Green | Session completed |

## Manual Setup

```bash
pip install -r requirements.txt
python setup_hooks.py    # configures ~/.claude/settings.json
python app.py            # starts the dashboard
```

## Requirements

- Python 3.x
- Windows (for toast notifications via winotify)
- Claude Code with hooks support

## Tech Stack

- **Flask** — local web server
- **SQLite** — session and event storage
- **winotify** — Windows toast notifications
- **Vanilla HTML/CSS/JS** — no frontend frameworks
