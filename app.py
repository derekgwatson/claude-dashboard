"""Claude Session Dashboard — terminal multiplexer + session monitor."""

import atexit
import ctypes
import glob
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request
from flask_sock import Sock
from winpty import PtyProcess

app = Flask(__name__)
sock = Sock(app)
DB_PATH = os.path.join(os.path.dirname(__file__), "dashboard.db")
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")
DEFAULT_SETTINGS = {"font_size": 16}
CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")

# In-memory terminal store: tid -> {pty, label, cwd, created_at, scrollback}
terminals = {}
terminals_lock = threading.Lock()
SCROLLBACK_MAX = 200_000  # characters to keep per terminal


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


SCROLLBACK_DIR = os.path.join(os.path.dirname(__file__), "scrollback")


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
        CREATE TABLE IF NOT EXISTS terminals (
            tid          TEXT PRIMARY KEY,
            label        TEXT DEFAULT '',
            cwd          TEXT DEFAULT '',
            task         TEXT DEFAULT '',
            command      TEXT DEFAULT '',
            launch_claude INTEGER DEFAULT 1,
            created_at   REAL DEFAULT 0
        );
    """)
    db.close()
    os.makedirs(SCROLLBACK_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def repo_from_cwd(cwd):
    """Extract a short repo name from a working directory path."""
    if not cwd:
        return ""
    p = Path(cwd)
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
# Session discovery
# ---------------------------------------------------------------------------

def pid_alive(pid):
    """Check if a process with the given PID is still running and is a Claude-related process."""
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                exe = buf.value.lower()
                if "node" in exe or "claude" in exe:
                    return True
                return False
            return False
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def scan_existing_sessions():
    """Read session files from ~/.claude/sessions/ and seed the DB with active ones."""
    sessions_dir = os.path.join(CLAUDE_DIR, "sessions")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    live_session_ids = set()
    session_file_data = {}

    if os.path.isdir(sessions_dir):
        for filepath in glob.glob(os.path.join(sessions_dir, "*.json")):
            try:
                with open(filepath) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            pid = data.get("pid")
            session_id = data.get("sessionId", "")
            if not session_id or not pid:
                continue

            if pid_alive(pid):
                live_session_ids.add(session_id)
                session_file_data[session_id] = data

    existing_rows = db.execute("SELECT session_id, status FROM sessions").fetchall()
    for row in existing_rows:
        sid = row["session_id"]
        if sid not in live_session_ids and row["status"] not in ("done",):
            db.execute(
                "UPDATE sessions SET status = 'done', needs_attention = 0, updated_at = ? WHERE session_id = ?",
                (now_ts(), sid),
            )

    for session_id, data in session_file_data.items():
        existing = db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if existing:
            continue

        cwd = data.get("cwd", "").replace("\\", "/")
        started_at = data.get("startedAt", 0)
        repo = repo_from_cwd(cwd)
        updated_at = started_at / 1000.0 if started_at > 1e12 else started_at

        db.execute(
            "INSERT INTO sessions (session_id, pid, cwd, repo, status, needs_attention, last_message, updated_at) "
            "VALUES (?, ?, ?, ?, 'running', 0, 'Discovered on startup', ?)",
            (session_id, data.get("pid", 0), cwd, repo, updated_at),
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
# Hook processing
# ---------------------------------------------------------------------------

def process_hook(payload):
    """Process an incoming hook payload and update session state."""
    db = get_db()

    session_id = payload.get("session_id", "unknown")
    hook_type = payload.get("type", "")
    body = payload.get("body", {}) if isinstance(payload.get("body"), dict) else {}

    store_event(db, session_id, hook_type, payload)

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
# Terminal management
# ---------------------------------------------------------------------------

def _save_terminal_meta(tid, label, cwd, task="", command="", launch_claude=True, created_at=0):
    """Persist terminal metadata to SQLite."""
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "INSERT OR REPLACE INTO terminals (tid, label, cwd, task, command, launch_claude, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tid, label, cwd, task, command or "", 1 if launch_claude else 0, created_at),
    )
    db.commit()
    db.close()


def _delete_terminal_meta(tid):
    """Remove terminal metadata from SQLite."""
    db = sqlite3.connect(DB_PATH)
    db.execute("DELETE FROM terminals WHERE tid = ?", (tid,))
    db.commit()
    db.close()
    # Remove scrollback file
    sb_path = os.path.join(SCROLLBACK_DIR, f"{tid}.log")
    if os.path.exists(sb_path):
        os.remove(sb_path)


def _save_scrollback(tid, scrollback_chunks):
    """Write scrollback to a file."""
    sb_path = os.path.join(SCROLLBACK_DIR, f"{tid}.log")
    with open(sb_path, "w", encoding="utf-8", errors="replace") as f:
        for chunk in scrollback_chunks:
            f.write(chunk)


def _load_scrollback(tid):
    """Read scrollback from file, return as a single string or empty."""
    sb_path = os.path.join(SCROLLBACK_DIR, f"{tid}.log")
    if not os.path.exists(sb_path):
        return ""
    with open(sb_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def find_terminal_by_cwd(cwd):
    """Return terminal ID if one already exists for this cwd, else None."""
    normalized = os.path.normpath(cwd).lower()
    with terminals_lock:
        for tid, t in terminals.items():
            if os.path.normpath(t["cwd"]).lower() == normalized and t["pty"].isalive():
                return tid
    return None


def discover_projects():
    """Return a list of known project directories from recent sessions and git repos."""
    projects = {}  # normalized path -> display info

    # 1. Recent Claude session cwds
    sessions_dir = os.path.join(CLAUDE_DIR, "sessions")
    if os.path.isdir(sessions_dir):
        for filepath in glob.glob(os.path.join(sessions_dir, "*.json")):
            try:
                with open(filepath) as f:
                    data = json.load(f)
                cwd = data.get("cwd", "")
                if cwd and os.path.isdir(cwd):
                    key = os.path.normpath(cwd).lower()
                    projects[key] = {"path": cwd, "name": os.path.basename(cwd)}
            except (json.JSONDecodeError, OSError):
                continue

    # 2. Siblings of the dashboard's own directory (likely the projects folder)
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(parent):
        try:
            for name in sorted(os.listdir(parent)):
                full = os.path.join(parent, name)
                if os.path.isdir(full):
                    key = os.path.normpath(full).lower()
                    if key not in projects:
                        projects[key] = {"path": full, "name": name}
        except OSError:
            pass

    return sorted(projects.values(), key=lambda p: p["name"].lower())


def create_terminal(label="", cwd=None, launch_claude=True, command=None, tid=None, old_scrollback=""):
    """Spawn a new PTY running PowerShell and return its terminal ID.

    If *command* is given, it is typed into the shell after startup instead of
    (or in addition to) ``claude``.  When *launch_claude* is False and no
    *command* is given, the terminal just opens a bare shell.

    Pass *tid* to reuse a specific terminal ID (for restoring from DB).
    Pass *old_scrollback* to prepend saved output from a previous session.
    """
    if cwd is None:
        cwd = os.path.expanduser("~")
    if tid is None:
        tid = uuid.uuid4().hex[:12]
    pty = PtyProcess.spawn(
        "powershell.exe -NoLogo",
        cwd=cwd,
        dimensions=(30, 120),
    )
    actual_label = label or os.path.basename(cwd)
    term = {
        "pty": pty,
        "label": actual_label,
        "cwd": cwd,
        "created_at": time.time(),
        "scrollback": [],      # list of output chunks
        "scrollback_len": 0,   # total chars tracked
        "subscribers": [],     # list of WebSocket objects to forward output to
        "task": "",            # user description of what they're working on
        "command": command or "",
        "launch_claude": launch_claude,
    }

    # Prepend old scrollback from previous session
    if old_scrollback:
        term["scrollback"].append(old_scrollback)
        term["scrollback_len"] = len(old_scrollback)

    with terminals_lock:
        terminals[tid] = term

    # Persist metadata to DB
    _save_terminal_meta(tid, actual_label, cwd, command=command, launch_claude=launch_claude, created_at=term["created_at"])

    # Background reader: buffers output, forwards to clients, periodically flushes scrollback
    last_flush = [time.time()]

    def _bg_reader():
        while pty.isalive():
            try:
                data = pty.read(4096)
                if data:
                    with terminals_lock:
                        _append_scrollback(term, data)
                        subs = list(term["subscribers"])
                    for ws in subs:
                        try:
                            ws.send(data)
                        except Exception:
                            with terminals_lock:
                                if ws in term["subscribers"]:
                                    term["subscribers"].remove(ws)
                    # Flush scrollback to disk every 10 seconds
                    now = time.time()
                    if now - last_flush[0] > 10:
                        last_flush[0] = now
                        with terminals_lock:
                            chunks = list(term["scrollback"])
                        try:
                            _save_scrollback(tid, chunks)
                        except Exception:
                            pass
            except EOFError:
                break
            except Exception:
                time.sleep(0.01)
        # Final flush on exit
        with terminals_lock:
            chunks = list(term["scrollback"])
        try:
            _save_scrollback(tid, chunks)
        except Exception:
            pass

    threading.Thread(target=_bg_reader, daemon=True).start()

    # Auto-launch command after shell is ready
    startup_cmd = command if command else ("claude" if launch_claude else None)
    if startup_cmd:
        def _auto_cmd():
            time.sleep(1.5)  # wait for PowerShell prompt
            pty.write(startup_cmd + "\r\n")
        threading.Thread(target=_auto_cmd, daemon=True).start()

    return tid


def close_terminal(tid):
    """Kill a PTY and remove it from the store."""
    with terminals_lock:
        term = terminals.pop(tid, None)
    if term and term["pty"].isalive():
        term["pty"].close(force=True)
    _delete_terminal_meta(tid)


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
    db = get_db()
    db.execute(
        "UPDATE sessions SET needs_attention = 0, status = 'idle', updated_at = ? WHERE session_id = ?",
        (now_ts(), session_id),
    )
    db.commit()
    return jsonify({"ok": True})


# --- Terminal API ---

@app.route("/api/projects")
def api_projects():
    """List known project directories for the project picker."""
    projects = discover_projects()
    # Mark which ones already have a terminal open
    for p in projects:
        p["has_terminal"] = find_terminal_by_cwd(p["path"]) is not None
    return jsonify(projects)


@app.route("/api/terminals", methods=["GET", "POST"])
def api_terminals():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        label = data.get("label", "")
        cwd = data.get("cwd", None)
        command = data.get("command", None)
        launch_claude = data.get("launch_claude", True)

        # If a terminal already exists for this cwd (and no custom command), return it
        if cwd and not command:
            existing = find_terminal_by_cwd(cwd)
            if existing:
                return jsonify({"ok": True, "terminal_id": existing, "existing": True})

        tid = create_terminal(label=label, cwd=cwd, launch_claude=launch_claude, command=command)
        with terminals_lock:
            actual_label = terminals[tid]["label"]
        return jsonify({"ok": True, "terminal_id": tid, "label": actual_label})

    # GET
    with terminals_lock:
        result = []
        for tid, t in terminals.items():
            result.append({
                "terminal_id": tid,
                "label": t["label"],
                "cwd": t["cwd"],
                "alive": t["pty"].isalive(),
                "created_at": t["created_at"],
                "task": t.get("task", ""),
            })
    return jsonify(result)


@app.route("/api/terminals/<tid>/label", methods=["PUT"])
def api_rename_terminal(tid):
    data = request.get_json(silent=True) or {}
    new_label = data.get("label", "")
    with terminals_lock:
        if tid in terminals:
            terminals[tid]["label"] = new_label or terminals[tid]["label"]
    # Persist to DB
    db = sqlite3.connect(DB_PATH)
    db.execute("UPDATE terminals SET label = ? WHERE tid = ?", (new_label, tid))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/terminals/<tid>/task", methods=["PUT"])
def api_update_task(tid):
    data = request.get_json(silent=True) or {}
    task = data.get("task", "")
    with terminals_lock:
        if tid in terminals:
            terminals[tid]["task"] = task
    # Persist to DB
    db = sqlite3.connect(DB_PATH)
    db.execute("UPDATE terminals SET task = ? WHERE tid = ?", (task, tid))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/terminals/<tid>", methods=["DELETE"])
def api_close_terminal(tid):
    close_terminal(tid)
    return jsonify({"ok": True})


@app.route("/api/file-picker", methods=["POST"])
def api_file_picker():
    """Open a native file dialog and return the selected path."""
    import tkinter as tk
    from tkinter import filedialog

    data = request.get_json(silent=True) or {}
    cwd = data.get("cwd", os.path.expanduser("~"))

    # tkinter must run on a thread with a message loop
    result = {}

    def _pick():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(initialdir=cwd)
        result["path"] = path
        root.destroy()

    t = threading.Thread(target=_pick)
    t.start()
    t.join(timeout=60)

    path = result.get("path", "")
    if not path:
        return jsonify({"ok": False})
    return jsonify({"ok": True, "path": path})


# Well-known folders to search when resolving a dropped filename to a full path
_SEARCH_FOLDERS = [
    os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots"),
    os.path.join(os.path.expanduser("~"), "Downloads"),
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Pictures"),
    os.path.join(os.path.expanduser("~"), "Documents"),
]


@app.route("/api/resolve-file", methods=["POST"])
def api_resolve_file():
    """Given a filename, find its full path in well-known folders."""
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"ok": False})

    for folder in _SEARCH_FOLDERS:
        candidate = os.path.join(folder, filename)
        if os.path.isfile(candidate):
            return jsonify({"ok": True, "path": candidate})

    return jsonify({"ok": False})


@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        try:
            with open(SETTINGS_PATH) as f:
                settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            settings = dict(DEFAULT_SETTINGS)
        settings.update(data)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f)
        return jsonify(settings)

    # GET
    try:
        with open(SETTINGS_PATH) as f:
            return jsonify(json.load(f))
    except (OSError, json.JSONDecodeError):
        return jsonify(dict(DEFAULT_SETTINGS))


@app.route("/api/latest-screenshot")
def api_latest_screenshot():
    """Return the path to the most recent file in the Screenshots folder."""
    screenshots = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    if not os.path.isdir(screenshots):
        return jsonify({"ok": False})

    files = []
    for name in os.listdir(screenshots):
        full = os.path.join(screenshots, name)
        if os.path.isfile(full):
            files.append((os.path.getmtime(full), full))

    if not files:
        return jsonify({"ok": False})

    files.sort(reverse=True)
    return jsonify({"ok": True, "path": files[0][1]})


def _append_scrollback(term, data):
    """Append output to a terminal's scrollback buffer, trimming if needed."""
    term["scrollback"].append(data)
    term["scrollback_len"] += len(data)
    # Trim oldest chunks when over limit
    while term["scrollback_len"] > SCROLLBACK_MAX and len(term["scrollback"]) > 1:
        removed = term["scrollback"].pop(0)
        term["scrollback_len"] -= len(removed)


@sock.route("/ws/terminal/<tid>")
def terminal_ws(ws, tid):
    """WebSocket bridge: xterm.js <-> PTY."""
    with terminals_lock:
        term = terminals.get(tid)
    if not term:
        return

    pty = term["pty"]

    # Replay scrollback then subscribe to live output
    with terminals_lock:
        for chunk in term["scrollback"]:
            ws.send(chunk)
        term["subscribers"].append(ws)

    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            try:
                payload = json.loads(msg)
            except (json.JSONDecodeError, TypeError):
                continue
            if payload.get("type") == "input":
                pty.write(payload.get("data", ""))
            elif payload.get("type") == "resize":
                cols = payload.get("cols", 120)
                rows = payload.get("rows", 30)
                pty.setwinsize(rows, cols)
    except Exception:
        pass
    finally:
        with terminals_lock:
            if ws in term.get("subscribers", []):
                term["subscribers"].remove(ws)


# ---------------------------------------------------------------------------
# Terminal restore
# ---------------------------------------------------------------------------

def restore_terminals():
    """Recreate terminals from the DB after a server restart."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM terminals ORDER BY created_at").fetchall()
    db.close()

    for row in rows:
        tid = row["tid"]
        cwd = row["cwd"]
        if not os.path.isdir(cwd):
            _delete_terminal_meta(tid)
            continue

        old_scrollback = _load_scrollback(tid)
        command = row["command"] or None
        launch_claude = bool(row["launch_claude"])

        create_terminal(
            label=row["label"],
            cwd=cwd,
            launch_claude=launch_claude,
            command=command,
            tid=tid,
            old_scrollback=old_scrollback,
        )

        # Restore task after creation
        with terminals_lock:
            if tid in terminals:
                terminals[tid]["task"] = row["task"] or ""

    count = len(rows)
    if count:
        print(f"Restored {count} terminal(s) from previous session")


# ---------------------------------------------------------------------------
# Shutdown — flush all scrollback so it survives restart
# ---------------------------------------------------------------------------

def _flush_all_scrollback():
    with terminals_lock:
        snapshot = [(tid, list(t["scrollback"])) for tid, t in terminals.items()]
    for tid, chunks in snapshot:
        try:
            _save_scrollback(tid, chunks)
        except Exception:
            pass

atexit.register(_flush_all_scrollback)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    scan_existing_sessions()
    restore_terminals()
    print("Claude Session Dashboard running at http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False)
