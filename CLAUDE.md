# Claude Reference Guide for claude-dashboard

Quick reference for working on this codebase.

## Project Overview

A native desktop app (pywebview + Flask) for monitoring multiple Claude Code sessions. Shows which sessions are running, waiting for input, or need permission. Runs as a native window, not a browser tab, so it can bring Windows Terminal to the foreground.

### Design Philosophy

**Keep it simple.** This is a local-only tool. No auth, no deployment, no frameworks beyond Flask + pywebview. If something can be done in 20 lines, don't make it 100.

## Architecture

```
claude-dashboard/
├── app.py              # Flask API (background thread) + pywebview native window
├── hook_receiver.py    # Stdin→HTTP bridge for Claude Code hooks (auto-starts dashboard)
├── start.py            # One-command setup: installs deps, configures hooks, starts server
├── setup_hooks.py      # Merges hook config into ~/.claude/settings.json
├── templates/
│   └── index.html      # Single-page dashboard
└── static/
    ├── app.js          # Polls /api/sessions every 2s, renders session cards
    └── style.css       # Dark theme, red highlight for attention-needed sessions
```

### How It Works

1. `app.py` starts Flask in a daemon thread (serves API on port 8765) and opens a pywebview native window
2. Claude Code fires a hook event → runs `hook_receiver.py` via stdin
3. `hook_receiver.py` POSTs the JSON payload to `http://127.0.0.1:8765/hook`
4. If the dashboard isn't running, `hook_receiver.py` auto-starts it
5. Flask processes the hook, upserts the session in SQLite, stores the raw event
6. Dashboard polls `/api/sessions` every 2s; attention-needed sessions sort to top

### Hook Events Handled

| Event | Effect |
|-------|--------|
| `SessionStart` | Creates/updates session with cwd, repo, status=running |
| `Notification` (permission_prompt) | Sets status=permission_needed, needs_attention=1 |
| `Notification` (idle_prompt) | Sets status=waiting_input, needs_attention=1 |
| `Stop` | Sets status=waiting_input, needs_attention=1 |
| `UserPromptSubmit` | Sets status=running, needs_attention=0 |
| `SubagentStart` | Sets status=running |
| `SubagentStop` | Updates last_message |
| `SessionEnd` | Sets status=done |

### Session Discovery

On startup, `app.py` scans `~/.claude/sessions/*.json` to find existing Claude Code sessions. Uses `QueryFullProcessImageNameW` to verify PIDs are actually node/claude processes (not recycled PIDs). Purges stale sessions from the DB that no longer have a live session file.

### Window Focus ("Find" Button)

Uses `EnumWindows` to find the Windows Terminal window, then `SetForegroundWindow` to bring it to the foreground. Uses the Alt-key trick (`keybd_event`) to bypass the foreground-lock. Since WT hosts all tabs in one window, this brings WT forward — the dashboard labels tell the user which tab is which.

## Data Model

**sessions** — one row per Claude Code session
- `session_id` (PK), `pid`, `label`, `cwd`, `repo`, `status`, `needs_attention`, `last_message`, `updated_at`

**events** — raw hook payloads for debugging
- `id` (PK), `session_id`, `hook_type`, `payload` (JSON), `created_at`

SQLite database at `dashboard.db` (gitignored). Created automatically on first run.

## API

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Dashboard HTML page |
| POST | `/hook` | Receive hook events |
| GET | `/api/sessions` | All sessions (sorted: attention first, then by updated_at) |
| PUT | `/api/sessions/<id>/label` | Update session label |
| POST | `/api/sessions/<id>/dismiss` | Clear attention flag, set status=idle |
| POST | `/api/sessions/<id>/focus` | Flash session's terminal window in taskbar |

## Setup

```bash
git clone ...
python start.py
```

Or manually:
```bash
pip install -r requirements.txt
python setup_hooks.py
python app.py
```

After setup, the dashboard auto-starts when any Claude Code session fires a hook.

## Common Gotchas

1. **Dashboard DB is gitignored** — each machine has its own. Sessions are discovered on startup.
2. **Hook paths are absolute** — `setup_hooks.py` writes the full path to `hook_receiver.py` into `~/.claude/settings.json`. Re-run if you move the repo.
3. **Debug mode is off** — Flask runs with `debug=False` to avoid the reloader killing background processes.
4. **Windows-specific code** — `pid_alive()` and `focus_window_by_pid()` have Windows/Unix branches. The find button only works on Windows.
