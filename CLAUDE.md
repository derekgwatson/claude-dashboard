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
├── hook_receiver.py    # Stdin→HTTP bridge (fire-and-forget via hook_sender.py)
├── hook_sender.py      # Background process that POSTs hook data to dashboard
├── start.py            # One-command setup: installs deps, configures hooks, starts server
├── setup_hooks.py      # Merges hook config into ~/.claude/settings.json
├── sync_client.py      # Background thread: pushes terminal state to cloud server
├── templates/
│   └── index.html      # Single-page dashboard
└── static/
    ├── app.js          # Terminal multiplexer + session cards + remote terminal viewer
    └── style.css       # Dark theme, red highlight for attention-needed sessions
```

### How It Works

1. `app.py` starts Flask in a daemon thread (serves API on port 8765) and opens a pywebview native window
2. Claude Code fires a hook event → runs `hook_receiver.py` via stdin
3. `hook_receiver.py` spawns `hook_sender.py` as a fire-and-forget background process (never blocks Claude's terminal)
4. `hook_sender.py` POSTs the JSON payload to `http://127.0.0.1:8765/hook`; auto-starts dashboard if needed
5. Flask processes the hook, upserts the session in SQLite, stores the raw event
6. Dashboard polls `/api/sessions` every 2s; attention-needed sessions sort to top

### Session Resume on Server Restart

When the dashboard server restarts, terminals are restored from the DB. If a Claude session ID was captured (via `SessionStart` hook), the terminal runs `claude --resume <session_id>` instead of a fresh `claude`, preserving full conversation context.

**How it works:**
- `SessionStart` hooks include `session_id` and `cwd`
- The dashboard matches the hook to a terminal by `cwd` and stores the `claude_session_id`
- On restart, `restore_terminals()` uses `--resume` for terminals that have a saved session ID

**Important:** Raw terminal scrollback is NOT replayed on restart (cursor-addressed escape sequences from Claude Code's UI cause scrambling). The terminal starts clean and Claude's `--resume` restores context.

### Cross-Machine Context via Compact Summaries

Claude sessions can't be resumed across machines (transcripts are project-path-scoped). Instead:
1. Run `/compact` in a Claude session to create a conversation summary
2. The sync client reads the summary from the transcript `.jsonl` and pushes it to the cloud DB
3. On another machine, remote terminals show an "Open locally (with context)" button
4. Clicking it creates a local terminal and auto-pastes the compact summary as context

### Hook Events Handled

Hook payloads have fields at the **top level** (not nested in a `body` sub-object):
- `session_id`, `hook_event_name`, `cwd`, `transcript_path`, `permission_mode`
- Stop: `last_assistant_message`, `stop_hook_active`
- UserPromptSubmit: `prompt`
- Notification: `notification_type`, `message`

| Event | Effect |
|-------|--------|
| `SessionStart` | Creates/updates session with cwd, repo, status=running. Links claude_session_id to terminal. |
| `Notification` (permission_prompt) | Sets status=permission_needed, needs_attention=1 |
| `Notification` (idle_prompt) | Sets status=waiting_input, needs_attention=1 |
| `Stop` | Sets status=waiting_input, needs_attention=1, stores last_assistant_message snippet |
| `UserPromptSubmit` | Sets status=running, needs_attention=0 |
| `SubagentStart` | Sets status=running |
| `SubagentStop` | Updates last_message |
| `SessionEnd` | Sets status=done |

### Session Discovery

On startup, `app.py` scans `~/.claude/sessions/*.json` to find existing Claude Code sessions. Uses `QueryFullProcessImageNameW` to verify PIDs are actually node/claude processes (not recycled PIDs). Purges stale sessions from the DB that no longer have a live session file.

### Window Focus ("Find" Button)

Uses `EnumWindows` to find the Windows Terminal window, then `SetForegroundWindow` to bring it to the foreground. Uses the Alt-key trick (`keybd_event`) to bypass the foreground-lock. Since WT hosts all tabs in one window, this brings WT forward — the dashboard labels tell the user which tab is which.

## Data Model

**sessions** — one row per Claude Code session (from hooks)
- `session_id` (PK), `pid`, `label`, `cwd`, `repo`, `status`, `needs_attention`, `last_message`, `updated_at`

**terminals** — one row per dashboard terminal (PTY)
- `tid` (PK), `label`, `cwd`, `task`, `command`, `launch_claude`, `claude_session_id`, `transcript_path`, `created_at`

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
