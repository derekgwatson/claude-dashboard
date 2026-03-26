"""Sync client — pushes local terminal state to the cloud and pulls remote terminals.

Runs as a background daemon thread inside app.py. Does nothing if sync is not configured.
"""

import base64
import json
import logging
import os
import socket
import sqlite3
import threading
import time
import zlib

import requests

log = logging.getLogger(__name__)

# How often to push/pull (seconds)
SYNC_INTERVAL = 30

# Module-level state
_sync_url = ""
_sync_token = ""
_machine_id = ""
_hostname = socket.gethostname()
_db_path = ""
_scrollback_dir = ""
_running = False
_last_sync = 0.0
_last_error = ""


def configure(settings_path, db_path, scrollback_dir):
    """Load sync config from settings.json. Call once at startup."""
    global _sync_url, _sync_token, _machine_id, _db_path, _scrollback_dir

    _db_path = db_path
    _scrollback_dir = scrollback_dir

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        settings = {}

    _sync_url = settings.get("sync_url", "").rstrip("/")
    _sync_token = settings.get("sync_token", "")
    _machine_id = settings.get("machine_id", "")

    if not _machine_id:
        import uuid
        _machine_id = uuid.uuid4().hex[:8]
        settings["machine_id"] = _machine_id
        try:
            with open(settings_path, "w") as f:
                json.dump(settings, f)
        except OSError:
            pass

    return bool(_sync_url)


def is_configured():
    return bool(_sync_url and _machine_id)


def get_status():
    """Return sync status for the frontend."""
    return {
        "configured": is_configured(),
        "sync_url": _sync_url,
        "machine_id": _machine_id,
        "hostname": _hostname,
        "last_sync": _last_sync,
        "last_error": _last_error,
        "running": _running,
    }


def _headers():
    h = {"Content-Type": "application/json"}
    if _sync_token:
        h["Authorization"] = f"Bearer {_sync_token}"
    return h


def _push():
    """Push local terminal state to the cloud."""
    global _last_sync, _last_error

    db = sqlite3.connect(_db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM terminals").fetchall()
    db.close()

    term_list = []
    for row in rows:
        tid = row["tid"]
        # Read and compress scrollback
        sb_path = os.path.join(_scrollback_dir, f"{tid}.log")
        scrollback_b64 = ""
        if os.path.exists(sb_path):
            try:
                with open(sb_path, "rb") as f:
                    raw = f.read()
                compressed = zlib.compress(raw, level=6)
                scrollback_b64 = base64.b64encode(compressed).decode("ascii")
            except OSError:
                pass

        # Extract compact summary from transcript if available
        compact_summary = ""
        transcript_path = row["transcript_path"] if "transcript_path" in row.keys() else ""
        if transcript_path:
            try:
                from app import extract_compact_summary
                compact_summary = extract_compact_summary(transcript_path)
            except Exception:
                pass

        term_list.append({
            "tid": tid,
            "label": row["label"],
            "cwd": row["cwd"],
            "task": row["task"] or "",
            "command": row["command"] or "",
            "launch_claude": row["launch_claude"],
            "created_at": row["created_at"],
            "updated_at": time.time(),
            "scrollback": scrollback_b64,
            "compact_summary": compact_summary,
        })

    # Also send session state so the server can trigger push notifications
    session_list = []
    try:
        db2 = sqlite3.connect(_db_path)
        db2.row_factory = sqlite3.Row
        session_rows = db2.execute(
            "SELECT session_id, status, needs_attention, label, repo, last_message, cwd "
            "FROM sessions WHERE status != 'done'"
        ).fetchall()
        db2.close()
        for row in session_rows:
            session_list.append({
                "session_id": row["session_id"],
                "status": row["status"],
                "needs_attention": row["needs_attention"],
                "label": row["label"] or row["repo"] or "",
                "last_message": row["last_message"] or "",
                "cwd": row["cwd"] or "",
            })
    except Exception:
        pass

    payload = {
        "machine_id": _machine_id,
        "hostname": _hostname,
        "terminals": term_list,
        "sessions": session_list,
    }

    resp = requests.post(
        f"{_sync_url}/api/terminals/sync",
        json=payload,
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    _last_sync = time.time()
    _last_error = ""


def pull_remote_terminals():
    """Fetch terminals from other machines. Returns a list of dicts."""
    if not is_configured():
        return []

    try:
        resp = requests.get(
            f"{_sync_url}/api/terminals",
            params={"exclude": _machine_id},
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("Failed to pull remote terminals: %s", e)
        return []


def pull_remote_scrollback(tid, machine_id):
    """Fetch scrollback for a specific remote terminal. Returns decoded text."""
    if not is_configured():
        return ""

    try:
        resp = requests.get(
            f"{_sync_url}/api/terminals/{tid}/scrollback",
            params={"machine_id": machine_id},
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return ""
        compressed = base64.b64decode(data["scrollback"])
        return zlib.decompress(compressed).decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("Failed to pull scrollback for %s: %s", tid, e)
        return ""


def _sync_loop():
    """Background loop: push every SYNC_INTERVAL seconds."""
    global _running, _last_error

    _running = True
    while _running:
        try:
            _push()
        except Exception as e:
            _last_error = str(e)
            log.warning("Sync push failed: %s", e)

        time.sleep(SYNC_INTERVAL)


def start():
    """Start the background sync thread. No-op if sync isn't configured."""
    if not is_configured():
        return

    # Do an initial push immediately
    try:
        _push()
    except Exception as e:
        log.warning("Initial sync push failed: %s", e)

    t = threading.Thread(target=_sync_loop, daemon=True, name="sync-client")
    t.start()
    log.info("Sync client started — pushing to %s every %ds", _sync_url, SYNC_INTERVAL)


def stop():
    global _running
    _running = False
