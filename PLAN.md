# Native Qt Rewrite Plan

## Why

The current pywebview + xterm.js architecture can't handle Claude Code's TUI redraws — the browser rendering pipeline (DOM → CSS → JS) causes chaotic scrolling. A native Qt app with a proper terminal widget renders directly via the OS graphics pipeline, eliminating the problem.

## Architecture

Replace the web frontend with PySide6. Keep Flask running in a background thread for hook reception (port 8765). Replace xterm.js with a custom QWidget backed by **pyte** (in-memory VT100 emulator) + **pywinpty** (already in use).

```
claude-dashboard/
├── app_qt.py              # Main entry — QApplication + MainWindow
├── flask_server.py         # Minimal Flask (hook receiver + sync routes only)
├── terminal_widget.py      # QWidget: pyte renderer + pywinpty bridge
├── sidebar.py              # QWidget: session tabs, favorites, controls
├── project_picker.py       # QDialog: project selection
├── db.py                   # SQLite helpers (extracted from app.py)
├── session_manager.py      # Hook processing, session discovery
├── terminal_manager.py     # PTY lifecycle, scrollback, restore
├── settings.py             # Settings load/save (same settings.json format)
├── sync_client.py          # Unchanged
├── hook_receiver.py        # Unchanged
├── hook_sender.py          # Unchanged
├── setup_hooks.py          # Unchanged
├── start.py                # Updated to launch app_qt.py
└── app.py                  # Kept for server mode only
```

## Key Technical Decisions

### Terminal Widget (terminal_widget.py, ~350 lines)
- **pyte.HistoryScreen** holds the character grid in memory
- **pyte.Stream** parses VT100/ANSI escape sequences
- **QPainter** renders the character grid directly (no DOM)
- **pywinpty.PtyProcess** provides the PTY (same as current)
- Background reader thread feeds PTY output to pyte via Qt signal
- 16ms coalesce timer for repaints (~60fps, no burst chaos)
- Keyboard input: translate QKeyEvent → VT100 sequences → write to PTY
- Resize: QWidget.resizeEvent recalculates rows/cols from font metrics, resizes pyte + PTY

### Why pyte works
pywinpty uses ConPTY on Windows 10+, which outputs standard VT100 sequences. Same data xterm.js currently processes, just rendered natively instead of through a browser.

### Flask stays for hooks
Claude Code hooks POST to `http://127.0.0.1:8765/hook`. Flask runs as a daemon thread, handles hook reception and DB writes. Qt main thread reads DB on a 2-second timer for status dots. No shared mutable state issues (SQLite WAL mode handles concurrent access).

### No WebSocket needed
The terminal widget talks directly to the PTY via pywinpty. No WebSocket bridge, no scrollback replay protocol, no reconnection logic.

## Module Breakdown

### terminal_widget.py — Core Terminal Renderer
```
TerminalWidget(QWidget):
  - screen: pyte.HistoryScreen(cols, rows, history=5000)
  - stream: pyte.Stream(screen)
  - pty: PtyProcess
  - feed(data) — stream.feed(data), schedule repaint
  - paintEvent() — iterate screen.buffer, QPainter.drawText per cell
  - keyPressEvent() — translate to escape sequences, pty.write()
  - resizeEvent() — recalc cols/rows, screen.resize(), pty.setwinsize()
  - wheelEvent() — scroll through history
  - Color map from current theme (#0d1117 bg, #e6edf3 fg, etc.)
```

### sidebar.py — Session Sidebar
```
Sidebar(QWidget):
  - Header: title + "+" button
  - Favorites: clickable list (from settings.json)
  - Terminal tabs: list with status dot, label, close button, description toggle
  - Font size control: -/size/+
  - Status legend: colored dots with labels
  - Shortcuts reference

Signals: terminal_selected, terminal_close_requested, new_terminal_requested, font_size_changed
Status polling: QTimer(2000) reads DB, updates dots
```

### project_picker.py — Project Selection
```
ProjectPicker(QDialog):
  - QLineEdit search with filter-as-you-type
  - QListWidget with project items (star, name, path, open badge)
  - Enter/click selects, Escape closes
  - Supports raw path entry
  - Reuses discover_projects() from session_manager
```

### app_qt.py — Main Window
```
MainWindow(QMainWindow):
  - QSplitter: sidebar (240px) | QStackedWidget of TerminalWidgets
  - Path bar (QLabel) above terminal stack
  - Keyboard shortcuts via QShortcut (Ctrl+Shift+S/F/V)
  - File drag-and-drop on terminal widget

Startup sequence:
  1. init_db()
  2. scan_existing_sessions()
  3. Start Flask daemon thread
  4. Create MainWindow
  5. restore_terminals()
  6. Start sync_client if configured
  7. app.exec()
```

### db.py, session_manager.py, terminal_manager.py, settings.py
Extracted from current app.py. Same logic, split into focused modules. Key change in terminal_manager: instead of WebSocket subscribers, each terminal has a `widget` reference to its TerminalWidget.

### flask_server.py — Minimal Flask
Only keeps:
- `POST /hook` — hook reception
- `GET /api/sessions` — needed by sync_client
- `GET /api/terminals` — needed by sync_client
- Removes: all WebSocket routes, file picker, screenshot, settings, project routes

## Code Reuse

**Unchanged:** hook_receiver.py, hook_sender.py, setup_hooks.py, sync_client.py

**Extract & adapt:** app.py → db.py, session_manager.py, terminal_manager.py, flask_server.py

**New:** terminal_widget.py, sidebar.py, project_picker.py, app_qt.py, settings.py

**Retire:** templates/index.html, static/app.js, static/style.css (keep for server mode reference)

## Implementation Phases

### Phase 1: MVP
Working Qt app that launches Claude sessions in terminal tabs with status dots.

1. `db.py` — extract DB helpers
2. `session_manager.py` — extract hook processing + session discovery
3. `terminal_manager.py` — extract PTY lifecycle
4. `terminal_widget.py` — pyte-based terminal renderer
5. `flask_server.py` — minimal Flask for hooks
6. `sidebar.py` — basic tab list with status dots and close
7. `app_qt.py` — main window wiring it all together
8. `settings.py` — font size persistence

**MVP includes:** terminal rendering, keyboard input, multiple tabs, status dots, terminal close, font size, resize.

**MVP defers:** project picker, favorites, label rename, task notes, remote terminals, drag-drop, shortcuts, scrollback persistence, terminal restore.

### Phase 2: Feature Parity
1. Project picker dialog
2. Double-click label rename
3. Description/task notes toggle
4. Favorites section
5. Scrollback persistence + terminal restore on restart
6. Path bar
7. Keyboard shortcuts (Ctrl+Shift+S, F, V)
8. File drag-and-drop

### Phase 3: Polish
1. Remote terminals section
2. Sync client integration
3. Text selection + copy
4. Window focus ("Find" button)
5. Update start.py
6. Smooth scrolling

## Dependencies

```
# requirements.txt (updated)
PySide6
pyte
flask==3.1.*
pywinpty==2.0.14; sys_platform == "win32"
requests==2.32.*
```

Remove: flask-sock (no WebSockets), pywebview (no browser)

## Risks

1. **pyte escape sequence coverage** — pyte handles VT100/VT220/xterm well; Claude Code uses standard ANSI via ConPTY. Test early with a live session.
2. **QPainter performance** — coalesce repaints to 60fps, only paint visible portion. Use QPixmap buffer if needed.
3. **Key translation** — Qt key events → VT100 sequences has known mappings. Start with basics, add special keys incrementally.
4. **Thread safety** — Flask daemon thread writes to SQLite. Qt main thread reads on timer. SQLite WAL handles this. terminals dict protected by existing lock.

## Verification

1. Launch app_qt.py, create a terminal tab
2. Verify PowerShell prompt appears, keyboard input works
3. Type `claude` — verify Claude Code's TUI renders correctly
4. Trigger a permission prompt — verify no scrolling chaos
5. Check status dots update (green while running, amber when waiting)
6. Switch between multiple terminal tabs
7. Resize the window — verify terminal reflows
8. Close and relaunch — verify terminal restore works
