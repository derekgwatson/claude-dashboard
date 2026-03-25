# Claude Reference Guide for claude-dashboard

Quick reference for working on this codebase.

## Project Overview

A lightweight Flask web app for monitoring multiple Claude Code sessions. Shows which sessions are running, waiting for input, or need permission. Displays session cards with status dots — no embedded terminals. Sessions run in separate PowerShell/terminal windows; the dashboard just shows their status via hooks.

### Design Philosophy

**Keep it simple.** This is a local-only status monitor. No auth, no deployment, no terminal embedding. If something can be done in 20 lines, don't make it 100.

## Architecture

```
claude-dashboard/
├── app.py              # Flask API + session monitor
├── hook_receiver.py    # Stdin→HTTP bridge (fire-and-forget via hook_sender.py)
├── hook_sender.py      # Background process that POSTs hook data to dashboard
├── start.py            # One-command setup: installs deps, configures hooks, starts server
├── setup_hooks.py      # Merges hook config into ~/.claude/settings.json
├── sync_client.py      # Background thread: pushes session state to cloud server
├── templates/
│   └── index.html      # Single-page dashboard
└── static/
    ├── app.js          # Session cards + polling + remote terminals
    └── style.css       # Dark theme, red highlight for attention-needed sessions
```

### How It Works

1. `app.py` starts Flask on port 8765 and serves the dashboard
2. Claude Code fires a hook event → runs `hook_receiver.py` via stdin
3. `hook_receiver.py` spawns `hook_sender.py` as a fire-and-forget background process (never blocks Claude's terminal)
4. `hook_sender.py` POSTs the JSON payload to `http://127.0.0.1:8765/hook`; auto-starts dashboard if needed
5. Flask processes the hook, upserts the session in SQLite, stores the raw event
6. Dashboard polls `/api/sessions` every 2s; attention-needed sessions highlighted in red
7. Windows toast notification fires when a session needs attention

### Hook Events Handled

Hook payloads have fields at the **top level** (not nested in a `body` sub-object):
- `session_id`, `hook_event_name`, `cwd`, `transcript_path`, `permission_mode`
- Stop: `last_assistant_message`, `stop_hook_active`
- UserPromptSubmit: `prompt`
- Notification: `notification_type`, `message`

| Event | Effect |
|-------|--------|
| `SessionStart` | Creates/updates session with cwd, repo, status=running |
| `Notification` (permission_prompt) | Sets status=permission_needed, needs_attention=1, toast |
| `Notification` (idle_prompt) | Sets status=waiting_input, needs_attention=1, toast |
| `Stop` | Sets status=waiting_input, needs_attention=1, toast |
| `UserPromptSubmit` | Sets status=running, needs_attention=0 |
| `SubagentStart` | Sets status=running |
| `SubagentStop` | Updates last_message |
| `SessionEnd` | Sets status=done |

### Session Discovery

On startup, `app.py` scans `~/.claude/sessions/*.json` to find existing Claude Code sessions. Uses `QueryFullProcessImageNameW` to verify PIDs are actually node/claude processes (not recycled PIDs). Purges stale sessions from the DB that no longer have a live session file.

### Cross-Machine Context via Compact Summaries

Claude sessions can't be resumed across machines (transcripts are project-path-scoped). Instead:
1. Run `/compact` in a Claude session to create a conversation summary
2. The sync client reads the summary from the transcript `.jsonl` and pushes it to the cloud DB
3. On another machine, remote terminals show in the sidebar

## Data Model

**sessions** — one row per Claude Code session (from hooks)
- `session_id` (PK), `pid`, `label`, `cwd`, `repo`, `status`, `needs_attention`, `last_message`, `updated_at`

**events** — raw hook payloads for debugging
- `id` (PK), `session_id`, `hook_type`, `payload` (JSON), `created_at`

**cloud_terminals** (server mode only) — synced terminal state from all machines
- `(tid, machine_id)` (PK), `label`, `cwd`, `task`, `command`, `launch_claude`, `scrollback`, `compact_summary`, `created_at`, `updated_at`, `alive`

SQLite database at `dashboard.db` (gitignored). Created automatically on first run.

## API

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/` | Dashboard HTML page |
| POST | `/hook` | Receive hook events |
| GET | `/api/sessions` | All sessions (sorted: attention first, then by updated_at) |
| PUT | `/api/sessions/<id>/label` | Update session label |
| POST | `/api/sessions/<id>/dismiss` | Clear attention flag, set status=idle |
| GET | `/api/settings` | Get dashboard settings |
| PUT | `/api/settings` | Update dashboard settings |

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
3. **Debug mode is off** — Flask runs with `debug=False`.
4. **Windows-specific code** — `pid_alive()` has Windows/Unix branches. Toast notifications are Windows-only (winotify).
5. **No embedded terminals** — this is a status-only dashboard. Claude sessions run in separate terminal windows.
