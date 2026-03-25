"""Claude Session Dashboard — session status monitor.

Run modes:
    python app.py              # Desktop mode (status dashboard, hook receiver)
    python app.py --server     # Server mode (cloud sync API only)
"""

import glob
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

# ---------------------------------------------------------------------------
# Mode detection (early, before conditional imports)
# ---------------------------------------------------------------------------

SERVER_MODE = "--server" in sys.argv or os.environ.get("SERVER_MODE", "") == "1"
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")

# Desktop-only imports (not available on Linux VPS)
if not SERVER_MODE:
    import ctypes
    import sync_client

app = Flask(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, "cloud.db" if SERVER_MODE else "dashboard.db")
SETTINGS_PATH = os.path.join(_DIR, "settings.json")
DEFAULT_SETTINGS = {"font_size": 16}
CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")


# ---------------------------------------------------------------------------
# Server-mode auth
# ---------------------------------------------------------------------------

if SERVER_MODE:
    @app.before_request
    def check_auth():
        if not SYNC_TOKEN:
            return
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {SYNC_TOKEN}":
            return jsonify({"error": "unauthorized"}), 401


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

    if SERVER_MODE:
        # Cloud sync tables only
        db.executescript("""
            CREATE TABLE IF NOT EXISTS machines (
                machine_id   TEXT PRIMARY KEY,
                hostname     TEXT DEFAULT '',
                last_seen    REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cloud_terminals (
                tid          TEXT,
                machine_id   TEXT,
                label        TEXT DEFAULT '',
                cwd          TEXT DEFAULT '',
                task         TEXT DEFAULT '',
                command      TEXT DEFAULT '',
                launch_claude INTEGER DEFAULT 1,
                scrollback   BLOB DEFAULT x'',
                compact_summary TEXT DEFAULT '',
                created_at   REAL DEFAULT 0,
                updated_at   REAL DEFAULT 0,
                alive        INTEGER DEFAULT 1,
                PRIMARY KEY (tid, machine_id)
            );
            CREATE TABLE IF NOT EXISTS cloud_settings (
                machine_id   TEXT PRIMARY KEY,
                data         TEXT DEFAULT '{}',
                updated_at   REAL DEFAULT 0
            );
        """)
    else:
        # Desktop tables
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
# Toast notifications (Windows)
# ---------------------------------------------------------------------------

_last_toast_time = 0
_TOAST_COOLDOWN = 10  # seconds between toasts to avoid spam


def send_toast(title, message):
    """Send a Windows toast notification, rate-limited."""
    global _last_toast_time
    now = time.time()
    if now - _last_toast_time < _TOAST_COOLDOWN:
        return
    _last_toast_time = now

    def _do_toast():
        try:
            from winotify import Notification
            toast = Notification(
                app_id="Claude Dashboard",
                title=title,
                msg=message,
            )
            toast.show()
        except Exception:
            pass

    threading.Thread(target=_do_toast, daemon=True).start()


# ---------------------------------------------------------------------------
# Hook processing
# ---------------------------------------------------------------------------

def process_hook(payload):
    """Process an incoming hook payload and update session state.

    Claude Code hook payloads have fields at the top level:
      - session_id, hook_event_name, cwd, permission_mode
      - For Stop: last_assistant_message, stop_hook_active
      - For UserPromptSubmit: prompt
      - For Notification: type (notification subtype), title, message, etc.
    """
    db = get_db()

    session_id = payload.get("session_id", "unknown")
    # Support both old ("type") and current ("hook_event_name") field names
    hook_type = payload.get("hook_event_name", "") or payload.get("type", "")
    cwd = payload.get("cwd", "")

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
    notify = False  # whether to send a toast

    if hook_type == "SessionStart":
        fields["cwd"] = cwd
        fields["repo"] = repo_from_cwd(cwd)
        fields["status"] = "running"
        fields["needs_attention"] = 0
        fields["last_message"] = "Session started"
        fields["pid"] = find_pid_for_session(session_id)

    elif hook_type == "Notification":
        notification_type = payload.get("notification_type", "")
        message = payload.get("message", "")

        if notification_type == "permission_prompt":
            fields["status"] = "permission_needed"
            fields["needs_attention"] = 1
            fields["last_message"] = message or "Permission required"
            notify = True
        elif notification_type == "idle_prompt":
            fields["status"] = "waiting_input"
            fields["needs_attention"] = 1
            fields["last_message"] = message or "Waiting for input"
            notify = True
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
        msg = payload.get("last_assistant_message", "")
        fields["last_message"] = (msg[:100] + "...") if len(msg) > 100 else msg or "Finished — waiting for input"
        notify = True

    elif hook_type == "UserPromptSubmit":
        fields["status"] = "running"
        fields["needs_attention"] = 0
        prompt = payload.get("prompt", "")
        # Ensure clean UTF-8 before truncating
        prompt = prompt.encode("utf-8", errors="replace").decode("utf-8")
        snippet = (prompt[:80] + "...") if len(prompt) > 80 else prompt
        fields["last_message"] = snippet or "Processing prompt..."

    elif hook_type == "SessionEnd":
        fields["status"] = "done"
        fields["needs_attention"] = 0
        fields["last_message"] = "Session ended"

    if fields:
        # Sanitize any surrogate characters that sneak in from hook payloads
        for k, v in fields.items():
            if isinstance(v, str):
                fields[k] = v.encode("utf-8", errors="replace").decode("utf-8")
        upsert_session(db, session_id, **fields)

    # Toast notification for attention-needed events
    if notify:
        repo = fields.get("repo", "")
        label = ""
        row = db.execute("SELECT label, repo FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row:
            label = row["label"] or row["repo"] or ""
            if not repo:
                repo = row["repo"]
        title = label or repo or "Claude session"
        send_toast(title, fields.get("last_message", "Needs attention"))

    return session_id


# ---------------------------------------------------------------------------
# Desktop-mode routes
# ---------------------------------------------------------------------------

if not SERVER_MODE:

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/sw.js")
    def service_worker():
        return app.send_static_file("sw.js")

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
        try:
            with open(SETTINGS_PATH) as f:
                return jsonify(json.load(f))
        except (OSError, json.JSONDecodeError):
            return jsonify(dict(DEFAULT_SETTINGS))

    @app.route("/api/sync/status")
    def api_sync_status():
        return jsonify(sync_client.get_status())

    @app.route("/api/remote-terminals")
    def api_remote_terminals():
        return jsonify(sync_client.pull_remote_terminals())

    @app.route("/api/remote-terminals/<tid>/scrollback")
    def api_remote_scrollback(tid):
        machine_id = request.args.get("machine_id", "")
        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400
        text = sync_client.pull_remote_scrollback(tid, machine_id)
        return jsonify({"ok": bool(text), "scrollback": text})


# ---------------------------------------------------------------------------
# Server-mode routes (cloud sync API)
# ---------------------------------------------------------------------------

if SERVER_MODE:
    import base64 as _b64

    @app.route("/api/health")
    def api_health():
        return jsonify({"ok": True})

    @app.route("/api/terminals/sync", methods=["POST"])
    def api_cloud_sync():
        """Receive a batch of terminal states from a machine."""
        data = request.get_json(silent=True) or {}
        machine_id = data.get("machine_id", "")
        hostname = data.get("hostname", "")
        term_list = data.get("terminals", [])

        if not machine_id:
            return jsonify({"error": "machine_id required"}), 400

        db = get_db()
        now = time.time()

        db.execute(
            "INSERT INTO machines (machine_id, hostname, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(machine_id) DO UPDATE SET hostname=excluded.hostname, last_seen=excluded.last_seen",
            (machine_id, hostname, now),
        )
        db.execute("UPDATE cloud_terminals SET alive = 0 WHERE machine_id = ?", (machine_id,))

        for t in term_list:
            tid = t.get("tid", "")
            if not tid:
                continue
            sb_b64 = t.get("scrollback", "")
            sb_blob = _b64.b64decode(sb_b64) if sb_b64 else b""
            db.execute(
                """INSERT INTO cloud_terminals (tid, machine_id, label, cwd, task, command,
                        launch_claude, scrollback, compact_summary, created_at, updated_at, alive)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(tid, machine_id) DO UPDATE SET
                        label=excluded.label, cwd=excluded.cwd, task=excluded.task,
                        command=excluded.command, launch_claude=excluded.launch_claude,
                        scrollback=excluded.scrollback, compact_summary=excluded.compact_summary,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at, alive=1""",
                (tid, machine_id, t.get("label", ""), t.get("cwd", ""), t.get("task", ""),
                 t.get("command", ""), t.get("launch_claude", 1),
                 sb_blob, t.get("compact_summary", ""),
                 t.get("created_at", 0), t.get("updated_at", now)),
            )

        db.commit()
        return jsonify({"ok": True, "synced": len(term_list)})

    @app.route("/api/terminals")
    def api_cloud_terminals():
        """List terminals, optionally filtered. Query: machine_id, exclude, alive_only."""
        db = get_db()
        mid = request.args.get("machine_id", "")
        exclude = request.args.get("exclude", "")
        alive_only = request.args.get("alive_only", "0") == "1"

        q = "SELECT t.*, m.hostname FROM cloud_terminals t LEFT JOIN machines m ON t.machine_id = m.machine_id WHERE 1=1"
        p = []
        if mid:
            q += " AND t.machine_id = ?"; p.append(mid)
        if exclude:
            q += " AND t.machine_id != ?"; p.append(exclude)
        if alive_only:
            q += " AND t.alive = 1"
        q += " ORDER BY t.updated_at DESC"

        rows = db.execute(q, p).fetchall()
        result = []
        for r in rows:
            entry = {
                "tid": r["tid"], "machine_id": r["machine_id"],
                "hostname": r["hostname"] or r["machine_id"],
                "label": r["label"], "cwd": r["cwd"], "task": r["task"],
                "command": r["command"], "launch_claude": r["launch_claude"],
                "created_at": r["created_at"], "updated_at": r["updated_at"],
                "alive": r["alive"],
            }
            summary = r["compact_summary"] if "compact_summary" in r.keys() else ""
            entry["has_summary"] = bool(summary)
            entry["compact_summary"] = summary
            result.append(entry)
        return jsonify(result)

    @app.route("/api/terminals/<tid>/scrollback")
    def api_cloud_scrollback(tid):
        """Return base64+zlib scrollback for a terminal."""
        mid = request.args.get("machine_id", "")
        if not mid:
            return jsonify({"error": "machine_id required"}), 400
        db = get_db()
        row = db.execute(
            "SELECT scrollback FROM cloud_terminals WHERE tid = ? AND machine_id = ?", (tid, mid)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "scrollback": _b64.b64encode(row["scrollback"]).decode("ascii")})

    @app.route("/api/machines")
    def api_machines():
        db = get_db()
        rows = db.execute("SELECT * FROM machines ORDER BY last_seen DESC").fetchall()
        return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Periodic session rescan (catches stale statuses)
# ---------------------------------------------------------------------------

RESCAN_INTERVAL = 15  # seconds


def _periodic_rescan():
    """Background thread: re-check session files to fix stale statuses.

    Claude Code doesn't fire a hook when the user approves a permission
    prompt, so a session can stay stuck at "permission_needed" forever.
    This thread re-scans ~/.claude/sessions/ and marks stale attention
    sessions as "running" if they're still alive, or "done" if they've
    exited.
    """
    while True:
        time.sleep(RESCAN_INTERVAL)
        try:
            sessions_dir = os.path.join(CLAUDE_DIR, "sessions")
            if not os.path.isdir(sessions_dir):
                continue

            # Build set of session IDs that have a session file with a live PID
            live_sessions = {}
            for filepath in glob.glob(os.path.join(sessions_dir, "*.json")):
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    sid = data.get("sessionId", "")
                    pid = data.get("pid", 0)
                    if sid and pid and pid_alive(pid):
                        live_sessions[sid] = data
                except (json.JSONDecodeError, OSError):
                    continue

            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            now = now_ts()

            # Fix stale attention states: if a session needs attention but
            # has been in that state for >30s and is still alive, it's
            # probably running (user approved the permission in their terminal)
            stale_rows = db.execute(
                "SELECT session_id, status, updated_at FROM sessions "
                "WHERE needs_attention = 1 AND status IN ('permission_needed', 'waiting_input') "
                "AND updated_at < ?",
                (now - 30,),
            ).fetchall()

            for row in stale_rows:
                sid = row["session_id"]
                if sid in live_sessions:
                    # Still alive — assume running, keep existing message
                    db.execute(
                        "UPDATE sessions SET status = 'running', needs_attention = 0, "
                        "updated_at = ? WHERE session_id = ?",
                        (now, sid),
                    )
                else:
                    # Process gone — mark done
                    db.execute(
                        "UPDATE sessions SET status = 'done', needs_attention = 0, "
                        "last_message = 'Session ended', updated_at = ? "
                        "WHERE session_id = ?",
                        (now, sid),
                    )

            # Mark non-done sessions as done if:
            # - their process is gone, OR
            # - they haven't received a hook in 10 minutes (orphaned PID)
            active_rows = db.execute(
                "SELECT session_id, updated_at FROM sessions WHERE status NOT IN ('done')"
            ).fetchall()
            for row in active_rows:
                sid = row["session_id"]
                stale = (now - row["updated_at"]) > 600  # 10 minutes without a hook
                if sid not in live_sessions or stale:
                    db.execute(
                        "UPDATE sessions SET status = 'done', needs_attention = 0, "
                        "last_message = 'Session ended', updated_at = ? "
                        "WHERE session_id = ?",
                        (now, sid),
                    )

            db.commit()
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Always init DB at import time (needed for gunicorn)
init_db()

if __name__ == "__main__":
    if SERVER_MODE:
        port = int(os.environ.get("PORT", 9876))
        auth_status = "token required" if SYNC_TOKEN else "NO AUTH (set SYNC_TOKEN)"
        print(f"Cloud sync server running on http://0.0.0.0:{port}  [{auth_status}]")
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        scan_existing_sessions()

        # Start background rescan thread
        threading.Thread(target=_periodic_rescan, daemon=True).start()

        # Start cloud sync if configured
        sync_enabled = sync_client.configure(SETTINGS_PATH, DB_PATH, "")
        if sync_enabled:
            sync_client.start()
            print(f"Cloud sync enabled — machine_id={sync_client._machine_id}")
        else:
            print("Cloud sync not configured (set sync_url in settings.json to enable)")

        print("Claude Session Dashboard running at http://127.0.0.1:8765")
        app.run(host="127.0.0.1", port=8765, debug=False)
