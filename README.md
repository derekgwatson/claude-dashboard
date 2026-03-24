# Claude Session Dashboard

A minimal local dashboard for monitoring multiple Claude Code sessions. See which sessions are running, waiting for input, or need permission — all in one browser tab.

![Dashboard](https://img.shields.io/badge/local-only-blue) ![Python 3](https://img.shields.io/badge/python-3.x-green)

## Features

- **Live session monitoring** — auto-refreshes every 2 seconds
- **Attention highlighting** — sessions needing input or permission sort to the top with red highlight
- **Auto-discovery** — finds existing Claude Code sessions on startup
- **Auto-start** — dashboard launches automatically when a Claude session starts
- **Editable labels** — click session names to add your own descriptions
- **Find button** — flashes the session's terminal window in the taskbar (Windows)
- **Hook-driven** — uses Claude Code's hook system for real-time status updates

## Quick Start

```bash
git clone <this-repo>
cd claude-dashboard
python start.py
```

That's it. Opens at http://127.0.0.1:8765

`start.py` handles everything: installs dependencies, configures Claude Code hooks, and starts the server.

After initial setup, the dashboard auto-starts whenever a Claude Code session begins — no need to launch it manually.

## How It Works

Claude Code supports [hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) that run shell commands on events like session start, tool use, and notifications. This dashboard:

1. Registers a hook receiver for key events (`SessionStart`, `Stop`, `Notification`, etc.)
2. The hook receiver (`hook_receiver.py`) reads event JSON from stdin and POSTs it to the local Flask server
3. The Flask server tracks session state in SQLite and serves a simple polling dashboard

## Session States

| Status | Meaning | Highlighted? |
|--------|---------|:---:|
| **Running** | Claude is actively working | No |
| **Waiting for Input** | Claude finished and is waiting for your next prompt | Yes |
| **Permission Needed** | Claude needs you to approve a tool use | Yes |
| **Idle** | Dismissed / no recent activity | No |
| **Done** | Session has ended | No (dimmed) |

## Manual Setup

If you prefer not to use `start.py`:

```bash
pip install -r requirements.txt
python setup_hooks.py    # configures ~/.claude/settings.json
python app.py            # starts the dashboard
```

## Requirements

- Python 3.x
- Flask
- Claude Code with hooks support

## Tech Stack

- **Flask** — local web server
- **SQLite** — session and event storage
- **Vanilla HTML/CSS/JS** — no frontend frameworks
