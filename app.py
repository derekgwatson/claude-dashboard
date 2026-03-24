"""Claude Session Dashboard — minimal local monitoring for Claude Code sessions."""

import ctypes
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "dashboard.db")
CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            pid          INTEGER DEFAULT 0,
            label        TEXT DEFAULT '',
            cwd          TEXT DEFAULT '',
            repo         TEXT DEFAULT '',
            status       TEXT DEFAULT 'running',
            needs_attention INTEGER DEFAULT 0,
            last_message TEXT DEFAULT '',
            updated_at   REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            hook_type  TEXT,
            payload    TEXT,
            created_at REAL
        );
    """)
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def repo_from_cwd(cwd):
    """Extract a short repo name from a working directory path."""
    if not cwd:
        return ""
    p = Path(cwd)
    # If it looks like a git worktree (.../repo/.worktrees/branch), go up
    parts = p.parts
    for i, part in enumerate(parts):
        if part == ".worktrees" and i > 0:
            return parts[i - 1]
    return p.name


def now_ts():
    return time.time()


def upsert_session(db, session_id, **fields):
    """Insert or update a session row. Only updates fields that are provided."""
    fields["updated_at"] = now_ts()

    existing = db.execute(
        "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()

    if existing:
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [session_id]
        db.execute(f"UPDATE sessions SET {sets} WHERE session_id = ?", vals)
    else:
        fields["session_id"] = session_id
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        db.execute(
            f"INSERT INTO sessions ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )
    db.commit()


def store_event(db, session_id, hook_type, payload):
    db.execute(
        "INSERT INTO events (session_id, hook_type, payload, created_at) VALUES (?, ?, ?, ?)",
        (session_id, hook_type, json.dumps(payload), now_ts()),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Session discovery (scan ~/.claude/sessions/ for active sessions)
# ---------------------------------------------------------------------------

def pid_alive(pid):
    """Check if a process with the given PID is still running."""
    if sys.platform == "win32":
        # Use Windows OpenProcess API — returns 0 if process doesn't exist
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def scan_existing_sessions():
    """Read session files from ~/.claude/sessions/ and seed the DB with active ones."""
    sessions_dir = os.path.join(CLAUDE_DIR, "sessions")
    if not os.path.isdir(sessions_dir):
        return

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    for filepath in glob.glob(os.path.join(sessions_dir, "*.json")):
        try:
            with open(filepath) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        pid = data.get("pid")
        session_id = data.get("sessionId", "")
        cwd = data.get("cwd", "").replace("\\", "/")
        started_at = data.get("startedAt", 0)

        if not session_id or not pid:
            continue

        # Only add sessions whose process is still alive
        if not pid_alive(pid):
            continue

        # Don't overwrite sessions we already know about (hook data is richer)
        existing = db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing:
            continue

        repo = repo_from_cwd(cwd)
        updated_at = started_at / 1000.0 if started_at > 1e12 else started_at

        db.execute(
            "INSERT INTO sessions (session_id, pid, cwd, repo, status, needs_attention, last_message, updated_at) "
            "VALUES (?, ?, ?, ?, 'waiting_input', 1, 'Discovered on startup — status unknown', ?)",
            (session_id, pid, cwd, repo, updated_at),
        )

    db.commit()
    db.close()


def find_pid_for_session(session_id):
    """Look up a PID from ~/.claude/sessions/ files by session_id."""
    sessions_dir = os.path.join(CLAUDE_DIR, "sessions")
    if not os.path.isdir(sessions_dir):
        return 0
    for filepath in glob.glob(os.path.join(sessions_dir, "*.json")):
        try:
            with open(filepath) as f:
                data = json.load(f)
            if data.get("sessionId") == session_id:
                return data.get("pid", 0)
        except (json.JSONDecodeError, OSError):
            continue
    return 0


# ---------------------------------------------------------------------------
# Window focus (Windows)
# ---------------------------------------------------------------------------

def focus_window_by_pid(pid):
    """Attach to the target process's console and bring its terminal tab to the foreground."""
    if sys.platform != "win32" or not pid:
        return False

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    try:
        # Detach from our own console so we can attach to the target's
        kernel32.FreeConsole()

        if not kernel32.AttachConsole(pid):
            return False

        hwnd = kernel32.GetConsoleWindow()
        kernel32.FreeConsole()

        if not hwnd:
            return False

        # Windows prevents background processes from stealing focus.
        # Best we can do: flash the taskbar icon to guide the user.
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.wintypes.UINT),
                ("hwnd", ctypes.wintypes.HWND),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("uCount", ctypes.wintypes.UINT),
                ("dwTimeout", ctypes.wintypes.DWORD),
            ]

        FLASHW_ALL = 0x03
        FLASHW_TIMERNOFG = 0x0C

        finfo = FLASHWINFO()
        finfo.cbSize = ctypes.sizeof(FLASHWINFO)
        finfo.hwnd = hwnd
        finfo.dwFlags = FLASHW_ALL | FLASHW_TIMERNOFG
        finfo.uCount = 3
        finfo.dwTimeout = 0
        user32.FlashWindowEx(ctypes.byref(finfo))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hook processing
# ---------------------------------------------------------------------------

def process_hook(payload):
    """Process an incoming hook payload and update session state."""
    db = get_db()

    session_id = payload.get("session_id", "unknown")
    hook_type = payload.get("type", "")
    body = payload.get("body", {}) if isinstance(payload.get("body"), dict) else {}

    store_event(db, session_id, hook_type, payload)

    # Try to attach PID if we don't have one yet
    existing = db.execute(
        "SELECT pid FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not existing or not existing["pid"]:
        pid = find_pid_for_session(session_id)
        if pid:
            db.execute("UPDATE sessions SET pid = ? WHERE session_id = ?", (pid, session_id))
            db.commit()

    fields = {}

    if hook_type == "SessionStart":
        cwd = body.get("cwd", "")
        fields["cwd"] = cwd
        fields["repo"] = repo_from_cwd(cwd)
        fields["status"] = "running"
        fields["needs_attention"] = 0
        fields["last_message"] = "Session started"
        fields["pid"] = find_pid_for_session(session_id)

    elif hook_type == "Notification":
        notification_type = body.get("notification_type", "")
        message = body.get("message", "")

        if notification_type == "permission_prompt":
            fields["status"] = "permission_needed"
            fields["needs_attention"] = 1
            fields["last_message"] = message or "Permission required"
        elif notification_type == "idle_prompt":
            fields["status"] = "waiting_input"
            fields["needs_attention"] = 1
            fields["last_message"] = message or "Waiting for input"
        else:
            fields["last_message"] = message or f"Notification: {notification_type}"

    elif hook_type == "SubagentStart":
        fields["status"] = "running"
        fields["needs_attention"] = 0
        fields["last_message"] = "Subagent started"

    elif hook_type == "SubagentStop":
        fields["last_message"] = "Subagent finished"

    elif hook_type == "Stop":
        fields["status"] = "waiting_input"
        fields["needs_attention"] = 1
        fields["last_message"] = "Finished — waiting for input"

    elif hook_type == "UserPromptSubmit":
        fields["status"] = "running"
        fields["needs_attention"] = 0
        fields["last_message"] = "Processing prompt..."

    elif hook_type == "SessionEnd":
        fields["status"] = "done"
        fields["needs_attention"] = 0
        fields["last_message"] = "Session ended"

    if fields:
        upsert_session(db, session_id, **fields)

    return session_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/hook", methods=["POST"])
def hook():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "no JSON body"}), 400
    session_id = process_hook(payload)
    return jsonify({"ok": True, "session_id": session_id})


@app.route("/api/sessions")
def api_sessions():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM sessions ORDER BY needs_attention DESC, updated_at DESC"
    ).fetchall()
    sessions = [dict(r) for r in rows]
    return jsonify(sessions)


@app.route("/api/sessions/<session_id>/label", methods=["PUT"])
def update_label(session_id):
    data = request.get_json(silent=True) or {}
    label = data.get("label", "")
    db = get_db()
    db.execute(
        "UPDATE sessions SET label = ? WHERE session_id = ?", (label, session_id)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/sessions/<session_id>/dismiss", methods=["POST"])
def dismiss_session(session_id):
    """Clear the needs_attention flag and set status back to idle."""
    db = get_db()
    db.execute(
        "UPDATE sessions SET needs_attention = 0, status = 'idle', updated_at = ? WHERE session_id = ?",
        (now_ts(), session_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/sessions/<session_id>/focus", methods=["POST"])
def focus_session(session_id):
    """Bring the terminal window for this session to the foreground."""
    db = get_db()
    row = db.execute(
        "SELECT pid FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row or not row["pid"]:
        return jsonify({"ok": False, "error": "no PID for session"}), 404
    success = focus_window_by_pid(row["pid"])
    return jsonify({"ok": success})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    scan_existing_sessions()
    print("Claude Session Dashboard running at http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False)
