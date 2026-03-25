// Terminal multiplexer for Claude sessions

const terminalTabs = document.getElementById("terminal-tabs");
const terminalContainer = document.getElementById("terminal-container");
const emptyState = document.getElementById("empty-state");
const pickerOverlay = document.getElementById("picker-overlay");
const pickerList = document.getElementById("picker-list");
const pickerSearch = document.getElementById("picker-search");
const pathBar = document.getElementById("terminal-path-bar");

// Active terminals: tid -> { term, fitAddon, ws, tabEl, wrapEl }
const terminals = {};
let activeTerminalId = null;
let currentFontSize = 16;
let favorites = [];

const fontSizeValue = document.getElementById("font-size-value");
const favoritesEl = document.getElementById("favorites");

async function loadSettings() {
    try {
        const resp = await fetch("/api/settings");
        const settings = await resp.json();
        if (settings.font_size) {
            currentFontSize = settings.font_size;
            fontSizeValue.textContent = currentFontSize;
        }
        favorites = settings.favorites || [];
        renderFavorites();
    } catch (e) {}
}

async function saveFavorites() {
    await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ favorites }),
    }).catch(() => {});
}

function renderFavorites() {
    if (!favoritesEl) return;
    if (favorites.length === 0) {
        favoritesEl.innerHTML = "";
        return;
    }
    favoritesEl.innerHTML =
        '<div class="fav-label">Favorites</div>' +
        favorites
            .map((f, i) => {
                const icon = f.command ? ">" : "~";
                return `<div class="fav-item" data-index="${i}">
                    <span class="fav-icon">${icon}</span>
                    <span class="fav-name">${escHtml(f.name)}</span>
                    <button class="fav-remove" data-index="${i}">&times;</button>
                </div>`;
            })
            .join("");
}

favoritesEl?.addEventListener("click", (e) => {
    if (e.target.classList.contains("fav-remove")) {
        e.stopPropagation();
        const idx = parseInt(e.target.dataset.index);
        favorites.splice(idx, 1);
        saveFavorites();
        renderFavorites();
        return;
    }
    const item = e.target.closest(".fav-item");
    if (!item) return;
    const fav = favorites[parseInt(item.dataset.index)];
    launchFavorite(fav);
});

async function launchFavorite(fav) {
    const body = { label: fav.name };
    if (fav.cwd) body.cwd = fav.cwd;
    if (fav.command) {
        body.command = fav.command;
        body.launch_claude = false;
    }
    const resp = await fetch("/api/terminals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (data.existing && terminals[data.terminal_id]) {
        switchTerminal(data.terminal_id);
    } else {
        connectTerminal(data.terminal_id, data.label || fav.name, fav.cwd);
    }
}

function addFavorite(name, cwd, command) {
    // Don't add duplicates
    const exists = favorites.some(
        (f) => f.name === name || (f.cwd && f.cwd === cwd && !f.command)
    );
    if (exists) return;
    const fav = { name };
    if (cwd) fav.cwd = cwd;
    if (command) fav.command = command;
    favorites.push(fav);
    saveFavorites();
    renderFavorites();
}

function isFavorite(cwd) {
    return favorites.some((f) => f.cwd && f.cwd === cwd && !f.command);
}

function applyFontSize(size) {
    currentFontSize = Math.max(10, Math.min(28, size));
    fontSizeValue.textContent = currentFontSize;
    for (const t of Object.values(terminals)) {
        t.term.options.fontSize = currentFontSize;
        t.fitAddon.fit();
    }
    fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ font_size: currentFontSize }),
    }).catch(() => {});
}

document.getElementById("font-decrease")?.addEventListener("click", () => applyFontSize(currentFontSize - 1));
document.getElementById("font-increase")?.addEventListener("click", () => applyFontSize(currentFontSize + 1));

// ---------------------------------------------------------------------------
// Project picker
// ---------------------------------------------------------------------------

let cachedProjects = [];

async function openPicker() {
    pickerOverlay.classList.remove("hidden");
    pickerSearch.value = "";
    pickerSearch.focus();
    await loadProjects();
    renderProjects();
}

function closePicker() {
    pickerOverlay.classList.add("hidden");
}

async function loadProjects() {
    try {
        const resp = await fetch("/api/projects");
        cachedProjects = await resp.json();
    } catch (e) {
        cachedProjects = [];
    }
}

function renderProjects() {
    const filter = pickerSearch.value.toLowerCase();
    const filtered = cachedProjects.filter(
        (p) => p.name.toLowerCase().includes(filter) || p.path.toLowerCase().includes(filter)
    );

    if (filtered.length === 0) {
        const looksLikePath = filter.includes("/") || filter.includes("\\") || /^[a-z]:/i.test(filter);
        if (looksLikePath && filter.length > 2) {
            const raw = pickerSearch.value;  // preserve original case
            pickerList.innerHTML = `<div class="picker-item" data-path="${escHtml(raw)}">
                <span class="picker-name">Open: ${escHtml(raw)}</span>
                <span class="picker-path">Launch Claude in this directory</span>
            </div>`;
        } else {
            pickerList.innerHTML = '<div class="picker-empty">No matching projects</div>';
        }
        return;
    }

    pickerList.innerHTML = filtered
        .map((p) => {
            const open = p.has_terminal ? " open" : "";
            const badge = p.has_terminal ? '<span class="picker-badge">open</span>' : "";
            const starred = isFavorite(p.path);
            const star = `<button class="picker-star${starred ? " starred" : ""}" data-path="${escHtml(p.path)}" data-name="${escHtml(p.name)}" title="Toggle favorite">${starred ? "\u2605" : "\u2606"}</button>`;
            return `<div class="picker-item${open}" data-path="${escHtml(p.path)}">
                ${star}
                <span class="picker-name">${escHtml(p.name)}</span>
                ${badge}
                <span class="picker-path">${escHtml(p.path)}</span>
            </div>`;
        })
        .join("");
}

pickerSearch?.addEventListener("input", renderProjects);
pickerSearch?.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePicker();
});

pickerOverlay?.addEventListener("click", (e) => {
    if (e.target === pickerOverlay) closePicker();
});

document.getElementById("picker-close")?.addEventListener("click", closePicker);

pickerList?.addEventListener("click", (e) => {
    const star = e.target.closest(".picker-star");
    if (star) {
        e.stopPropagation();
        const path = star.dataset.path;
        const name = star.dataset.name;
        if (isFavorite(path)) {
            favorites = favorites.filter((f) => !(f.cwd === path && !f.command));
            saveFavorites();
        } else {
            addFavorite(name, path);
        }
        renderFavorites();
        renderProjects();
        return;
    }
    const item = e.target.closest(".picker-item");
    if (!item) return;
    const cwd = item.dataset.path;
    closePicker();
    launchSession(cwd);
});

async function launchSession(cwd) {
    const resp = await fetch("/api/terminals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cwd }),
    });
    const data = await resp.json();

    if (data.existing && terminals[data.terminal_id]) {
        switchTerminal(data.terminal_id);
    } else {
        connectTerminal(data.terminal_id, data.label, cwd);
    }
}

// ---------------------------------------------------------------------------
// Event delegation for terminal tabs
// ---------------------------------------------------------------------------

let clickTimer = null;

terminalTabs?.addEventListener("click", (e) => {
    if (e.target.classList.contains("tab-close")) {
        const tab = e.target.closest(".tab");
        if (tab) closeTerminal(tab.dataset.tid);
        return;
    }
    if (e.target.classList.contains("tab-desc-toggle")) {
        const tab = e.target.closest(".tab");
        if (!tab) return;
        const desc = tab.querySelector(".tab-desc");
        desc.classList.toggle("hidden");
        e.target.textContent = desc.classList.contains("hidden") ? "\u25BE" : "\u25B4";
        // Populate textarea with saved task
        const tid = tab.dataset.tid;
        const textarea = desc.querySelector(".tab-desc-input");
        if (terminals[tid] && !textarea.value) {
            textarea.value = terminals[tid].task || "";
        }
        if (!desc.classList.contains("hidden")) textarea.focus();
        return;
    }
    if (e.target.classList.contains("tab-label-edit")) return;
    if (e.target.classList.contains("tab-desc-input")) return;
    const tab = e.target.closest(".tab");
    if (!tab) return;
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => switchTerminal(tab.dataset.tid), 200);
});

terminalTabs?.addEventListener("dblclick", (e) => {
    clearTimeout(clickTimer);
    const labelEl = e.target.closest(".tab-label");
    if (!labelEl) return;
    const tab = labelEl.closest(".tab");
    const tid = tab.dataset.tid;

    switchTerminal(tid);

    const input = document.createElement("input");
    input.className = "tab-label-edit";
    input.value = labelEl.textContent;
    labelEl.replaceWith(input);
    input.focus();
    input.select();

    const finish = () => {
        const newLabel = input.value.trim() || "Terminal";
        const span = document.createElement("span");
        span.className = "tab-label";
        span.textContent = newLabel;
        input.replaceWith(span);
        renameTerminal(tid, newLabel);
    };
    input.addEventListener("blur", finish);
    input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") input.blur();
        if (ev.key === "Escape") { input.value = labelEl.textContent; input.blur(); }
    });
});

// ---------------------------------------------------------------------------
// Terminal management
// ---------------------------------------------------------------------------

function connectTerminal(tid, label, cwd) {
    if (terminals[tid]) {
        switchTerminal(tid);
        return;
    }

    const term = new Terminal({
        cursorBlink: true,
        scrollback: 5000,
        fastScrollModifier: "alt",
        fontSize: currentFontSize,
        fontFamily: "'Cascadia Code', 'Consolas', monospace",
        theme: {
            background: "#0d1117",
            foreground: "#e6edf3",
            cursor: "#58a6ff",
            selectionBackground: "#264f78",
            black: "#0d1117",
            red: "#ff7b72",
            green: "#3fb950",
            yellow: "#d29922",
            blue: "#58a6ff",
            magenta: "#bc8cff",
            cyan: "#39c5cf",
            white: "#e6edf3",
        },
    });

    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);

    const wrapEl = document.createElement("div");
    wrapEl.className = "terminal-wrap";
    // Start visible so xterm can measure correct dimensions before scrollback replay
    terminalContainer.appendChild(wrapEl);

    term.open(wrapEl);
    fitAddon.fit();

    const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/terminal/${tid}`);

    // Batch incoming PTY data and flush once per animation frame.
    // During large bursts (Claude TUI redraws), hide the terminal briefly
    // so the user sees a quick blink instead of chaotic scrolling.
    let writeBuf = "";
    let writeRaf = 0;
    let burstBytes = 0;
    let burstTimer = 0;
    const BURST_THRESHOLD = 4000;  // bytes in a short window to trigger hide
    const BURST_SETTLE = 150;      // ms of quiet before showing again

    ws.onmessage = (event) => {
        writeBuf += event.data;
        burstBytes += event.data.length;

        // If a lot of data arrives quickly, hide terminal until it settles
        if (burstBytes > BURST_THRESHOLD) {
            wrapEl.style.opacity = "0";
        }
        clearTimeout(burstTimer);
        burstTimer = setTimeout(() => {
            burstBytes = 0;
            wrapEl.style.opacity = "1";
        }, BURST_SETTLE);

        if (!writeRaf) {
            writeRaf = requestAnimationFrame(() => {
                term.write(writeBuf);
                writeBuf = "";
                writeRaf = 0;
            });
        }
    };

    ws.onopen = () => {
        setTimeout(() => {
            if (terminals[tid]) {
                terminals[tid].fitAddon.fit();
                const { cols, rows } = terminals[tid].term;
                const liveWs = terminals[tid].ws;
                if (liveWs && liveWs.readyState === WebSocket.OPEN) {
                    liveWs.send(JSON.stringify({ type: "resize", cols, rows }));
                }
            }
        }, 100);
    };

    ws.onclose = () => {
        term.write("\r\n\x1b[33m[server disconnected — reconnecting...]\x1b[0m\r\n");
        attemptReconnect(tid);
    };

    term.onData((data) => {
        const liveWs = terminals[tid]?.ws;
        if (liveWs && liveWs.readyState === WebSocket.OPEN) {
            liveWs.send(JSON.stringify({ type: "input", data }));
        }
    });

    // Ctrl+Shift+C = copy, Ctrl+Shift+V = paste, Ctrl+Shift+O = file picker
    term.attachCustomKeyEventHandler((e) => {
        if (e.type !== "keydown") return true;
        if (e.ctrlKey && e.shiftKey && e.key === "C") {
            e.preventDefault();
            const sel = term.getSelection();
            if (sel) navigator.clipboard.writeText(sel);
            return false;
        }
        if (e.ctrlKey && e.shiftKey && e.key === "V") {
            e.preventDefault();
            navigator.clipboard.readText().then((text) => {
                const liveWs = terminals[tid]?.ws;
                if (text && liveWs && liveWs.readyState === WebSocket.OPEN) {
                    liveWs.send(JSON.stringify({ type: "input", data: text }));
                }
            });
            return false;
        }
        if (e.ctrlKey && e.shiftKey && e.key === "F") {
            e.preventDefault();
            pickFile(tid);
            return false;
        }
        if (e.ctrlKey && e.shiftKey && e.key === "S") {
            e.preventDefault();
            pasteLatestScreenshot(tid);
            return false;
        }
        return true;
    });

    term.onResize(({ cols, rows }) => {
        const liveWs = terminals[tid]?.ws;
        if (liveWs && liveWs.readyState === WebSocket.OPEN) {
            liveWs.send(JSON.stringify({ type: "resize", cols, rows }));
        }
    });

    // Drop files: resolve filename to full path and paste it
    wrapEl.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
    });
    wrapEl.addEventListener("drop", async (e) => {
        e.preventDefault();
        if (!e.dataTransfer.files.length) return;
        for (const file of e.dataTransfer.files) {
            try {
                const resp = await fetch("/api/resolve-file", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ filename: file.name }),
                });
                const data = await resp.json();
                const liveWs2 = terminals[tid]?.ws;
                if (data.ok && liveWs2 && liveWs2.readyState === WebSocket.OPEN) {
                    const path = data.path.includes(" ") ? `"${data.path}"` : data.path;
                    liveWs2.send(JSON.stringify({ type: "input", data: path + " " }));
                }
            } catch (e) {
                // ignore
            }
        }
        term.focus();
    });

    const tabLabel = label || `Terminal ${Object.keys(terminals).length + 1}`;
    const tabEl = document.createElement("div");
    tabEl.className = "tab";
    tabEl.dataset.tid = tid;
    tabEl.innerHTML = `
        <div class="tab-header">
            <span class="tab-status" data-tid="${escHtml(tid)}"></span>
            <span class="tab-label">${escHtml(tabLabel)}</span>
            <button class="tab-desc-toggle" title="Toggle description">&#9662;</button>
            <button class="tab-close">&times;</button>
        </div>
        <div class="tab-desc hidden">
            <textarea class="tab-desc-input" placeholder="Notes..." rows="2"></textarea>
        </div>
    `;
    terminalTabs.appendChild(tabEl);

    terminals[tid] = { term, fitAddon, ws, tabEl, wrapEl, task: "", cwd: cwd || "" };
    switchTerminal(tid);
    try { localStorage.setItem("activeTerminalId", tid); } catch (e) {}
}

function switchTerminal(tid) {
    if (activeTerminalId && terminals[activeTerminalId]) {
        terminals[activeTerminalId].wrapEl.style.display = "none";
        terminals[activeTerminalId].tabEl.classList.remove("active");
    }

    activeTerminalId = tid;
    const t = terminals[tid];
    t.wrapEl.style.display = "block";
    t.tabEl.classList.add("active");
    emptyState.style.display = "none";
    if (pathBar) pathBar.textContent = t.cwd || "";
    try { localStorage.setItem("activeTerminalId", tid); } catch (e) {}

    requestAnimationFrame(() => { requestAnimationFrame(() => {
        t.fitAddon.fit();
        if (!document.activeElement || !document.activeElement.classList.contains("tab-label-edit")
            && !document.activeElement.classList.contains("tab-desc-input")) {
            t.term.focus();
        }
    }); });
}

async function closeTerminal(tid) {
    const t = terminals[tid];
    if (!t) return;

    t.ws.close();
    t.term.dispose();
    t.wrapEl.remove();
    t.tabEl.remove();
    delete terminals[tid];

    await fetch(`/api/terminals/${tid}`, { method: "DELETE" }).catch(() => {});

    const remaining = Object.keys(terminals);
    if (remaining.length > 0) {
        switchTerminal(remaining[remaining.length - 1]);
    } else {
        activeTerminalId = null;
        emptyState.style.display = "flex";
    }
}

async function renameTerminal(tid, label) {
    await fetch(`/api/terminals/${tid}/label`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
    }).catch(() => {});
}

async function pickFile(tid) {
    const t = terminals[tid];
    if (!t) return;
    try {
        const resp = await fetch("/api/file-picker", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
        });
        const data = await resp.json();
        if (data.ok && data.path && t.ws.readyState === WebSocket.OPEN) {
            const path = data.path.includes(" ") ? `"${data.path}"` : data.path;
            t.ws.send(JSON.stringify({ type: "input", data: path }));
        }
        t.term.focus();
    } catch (e) {
        // ignore
    }
}

async function pasteLatestScreenshot(tid) {
    const t = terminals[tid];
    if (!t) return;
    try {
        const resp = await fetch("/api/latest-screenshot");
        const data = await resp.json();
        if (data.ok && data.path && t.ws.readyState === WebSocket.OPEN) {
            const path = data.path.includes(" ") ? `"${data.path}"` : data.path;
            t.ws.send(JSON.stringify({ type: "input", data: path }));
        }
        t.term.focus();
    } catch (e) {
        // ignore
    }
}

// ---------------------------------------------------------------------------
// Auto-reconnect WebSocket on server disconnect
// ---------------------------------------------------------------------------

function attemptReconnect(tid) {
    const t = terminals[tid];
    if (!t) return;

    let delay = 1000;
    const maxDelay = 10000;

    function tryConnect() {
        // Check if server is back by pinging the terminals API
        fetch("/api/terminals").then(resp => {
            if (!resp.ok) throw new Error("not ready");
            return resp.json();
        }).then(list => {
            const match = list.find(x => x.terminal_id === tid && x.alive);
            if (!match) {
                // Terminal no longer exists on server
                t.term.write("\r\n\x1b[90m[terminal closed on server]\x1b[0m\r\n");
                return;
            }

            // Server is back — clean reconnect, Claude --resume handles context
            const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
            const newWs = new WebSocket(`${wsProto}//${location.host}/ws/terminal/${tid}?replay=0`);

            newWs.onmessage = (event) => {
                t.term.write(event.data);
            };

            newWs.onopen = () => {
                t.term.reset();
                t.ws = newWs;
                setTimeout(() => {
                    t.fitAddon.fit();
                    const { cols, rows } = t.term;
                    newWs.send(JSON.stringify({ type: "resize", cols, rows }));
                }, 100);
            };

            newWs.onclose = () => {
                t.term.write("\r\n\x1b[33m[server disconnected — reconnecting...]\x1b[0m\r\n");
                attemptReconnect(tid);
            };

        }).catch(() => {
            // Server not back yet — retry with backoff
            delay = Math.min(delay * 1.5, maxDelay);
            setTimeout(tryConnect, delay);
        });
    }

    setTimeout(tryConnect, delay);
}

window.addEventListener("resize", () => {
    if (activeTerminalId && terminals[activeTerminalId]) {
        terminals[activeTerminalId].fitAddon.fit();
    }
});

function escHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.getElementById("new-term-btn")?.addEventListener("click", openPicker);
document.getElementById("new-term-cta")?.addEventListener("click", openPicker);

async function reconnectTerminals() {
    const savedActive = localStorage.getItem("activeTerminalId");
    try {
        const resp = await fetch("/api/terminals");
        const list = await resp.json();
        for (const t of list) {
            if (t.alive) {
                connectTerminal(t.terminal_id, t.label, t.cwd);
                if (t.task && terminals[t.terminal_id]) {
                    terminals[t.terminal_id].task = t.task;
                }
            }
        }
        // Restore previously active terminal
        if (savedActive && terminals[savedActive]) {
            switchTerminal(savedActive);
        }
    } catch (e) {
        // ignore
    }
}

loadSettings().then(() => reconnectTerminals()).then(() => {
    loadSyncStatus();
    loadRemoteTerminals();
    // Poll remote terminals every 30s
    setInterval(loadRemoteTerminals, 30000);
});

// Task description — save per terminal via sidebar textarea
terminalTabs?.addEventListener("input", (e) => {
    if (!e.target.classList.contains("tab-desc-input")) return;
    const tab = e.target.closest(".tab");
    if (!tab) return;
    const tid = tab.dataset.tid;
    if (terminals[tid]) terminals[tid].task = e.target.value;
});

terminalTabs?.addEventListener("focusout", (e) => {
    if (!e.target.classList.contains("tab-desc-input")) return;
    const tab = e.target.closest(".tab");
    if (!tab) return;
    const tid = tab.dataset.tid;
    fetch(`/api/terminals/${tid}/task`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: e.target.value }),
    }).catch(() => {});
});

terminalTabs?.addEventListener("keydown", (e) => {
    if (!e.target.classList.contains("tab-desc-input")) return;
    if (e.key === "Escape") {
        e.preventDefault();
        const tab = e.target.closest(".tab");
        if (tab && terminals[tab.dataset.tid]) {
            terminals[tab.dataset.tid].term.focus();
        }
    }
});

// ---------------------------------------------------------------------------
// Poll session status for sidebar indicators
// ---------------------------------------------------------------------------

const STATUS_LABELS = {
    running: "Working",
    waiting_input: "Waiting for input",
    permission_needed: "Needs permission",
    done: "Done",
};

async function pollTerminalStatuses() {
    try {
        const resp = await fetch("/api/terminal-statuses");
        const statuses = await resp.json();  // {tid: status, ...}
        for (const [tid, status] of Object.entries(statuses)) {
            const dot = document.querySelector(`.tab-status[data-tid="${CSS.escape(tid)}"]`);
            if (!dot) continue;
            dot.className = "tab-status";
            if (status) {
                dot.classList.add(`status-${status}`);
                dot.title = STATUS_LABELS[status] || status;
            } else {
                dot.title = "";
            }
        }
    } catch (e) {}
}

setInterval(pollTerminalStatuses, 2000);

// ---------------------------------------------------------------------------
// Remote terminals (from other machines via cloud sync)
// ---------------------------------------------------------------------------

const remoteSection = document.getElementById("remote-section");
const syncStatusEl = document.getElementById("sync-status");
let remoteTerminals = [];  // cached list from server
let activeRemoteId = null; // "machine_id:tid" if viewing a remote terminal

async function loadSyncStatus() {
    try {
        const resp = await fetch("/api/sync/status");
        const status = await resp.json();
        if (!syncStatusEl) return;
        if (!status.configured) {
            syncStatusEl.innerHTML = "";
            return;
        }
        const ago = status.last_sync
            ? Math.round((Date.now() / 1000 - status.last_sync)) + "s ago"
            : "never";
        const err = status.last_error ? ` <span class="sync-err" title="${escHtml(status.last_error)}">!</span>` : "";
        syncStatusEl.innerHTML = `<div class="sync-info">Sync: ${escHtml(status.hostname)}${err}<span class="sync-ago">${ago}</span></div>`;
    } catch (e) {}
}

async function loadRemoteTerminals() {
    if (!remoteSection) return;
    try {
        const resp = await fetch("/api/remote-terminals");
        remoteTerminals = await resp.json();
    } catch (e) {
        remoteTerminals = [];
    }

    if (remoteTerminals.length === 0) {
        remoteSection.innerHTML = "";
        return;
    }

    // Group by machine
    const byMachine = {};
    for (const t of remoteTerminals) {
        const key = t.machine_id;
        if (!byMachine[key]) byMachine[key] = { hostname: t.hostname, terminals: [] };
        byMachine[key].terminals.push(t);
    }

    let html = '<div class="remote-label">Remote</div>';
    for (const [machineId, info] of Object.entries(byMachine)) {
        html += `<div class="remote-machine">${escHtml(info.hostname)}</div>`;
        for (const t of info.terminals) {
            const remoteId = `${t.machine_id}:${t.tid}`;
            const active = activeRemoteId === remoteId ? " active" : "";
            const alive = t.alive ? '<span class="remote-alive"></span>' : "";
            html += `<div class="remote-tab${active}" data-remote-id="${escHtml(remoteId)}" data-tid="${escHtml(t.tid)}" data-machine="${escHtml(t.machine_id)}">
                <span class="remote-tab-label">${alive}${escHtml(t.label || t.cwd)}</span>
            </div>`;
        }
    }
    remoteSection.innerHTML = html;

    // Also refresh sync status
    loadSyncStatus();
}

remoteSection?.addEventListener("click", (e) => {
    const tab = e.target.closest(".remote-tab");
    if (!tab) return;
    const tid = tab.dataset.tid;
    const machineId = tab.dataset.machine;
    const remoteId = tab.dataset.remoteId;
    viewRemoteTerminal(tid, machineId, remoteId);
});

async function viewRemoteTerminal(tid, machineId, remoteId) {
    // Deactivate local terminal
    if (activeTerminalId && terminals[activeTerminalId]) {
        terminals[activeTerminalId].wrapEl.style.display = "none";
        terminals[activeTerminalId].tabEl.classList.remove("active");
    }
    activeTerminalId = null;
    activeRemoteId = remoteId;

    // Update tab highlighting
    remoteSection.querySelectorAll(".remote-tab").forEach(el => el.classList.remove("active"));
    const activeTab = remoteSection.querySelector(`[data-remote-id="${CSS.escape(remoteId)}"]`);
    if (activeTab) activeTab.classList.add("active");

    // Remove any existing remote viewer
    const existing = terminalContainer.querySelector(".remote-viewer");
    if (existing) existing.remove();

    // Fetch scrollback
    const viewerEl = document.createElement("div");
    viewerEl.className = "remote-viewer";
    terminalContainer.appendChild(viewerEl);
    emptyState.style.display = "none";

    const term = new Terminal({
        cursorBlink: false,
        scrollback: 50000,
        fontSize: currentFontSize,
        fontFamily: "'Cascadia Code', 'Consolas', monospace",
        disableStdin: true,
        theme: {
            background: "#0d1117",
            foreground: "#e6edf3",
            cursor: "#0d1117", // hide cursor
            selectionBackground: "#264f78",
            black: "#0d1117",
            red: "#ff7b72",
            green: "#3fb950",
            yellow: "#d29922",
            blue: "#58a6ff",
            magenta: "#bc8cff",
            cyan: "#39c5cf",
            white: "#e6edf3",
        },
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(viewerEl);
    fitAddon.fit();

    term.write("\x1b[90m[Loading remote scrollback...]\x1b[0m\r\n");

    try {
        const resp = await fetch(`/api/remote-terminals/${tid}/scrollback?machine_id=${encodeURIComponent(machineId)}`);
        const data = await resp.json();
        if (data.ok && data.scrollback) {
            term.clear();
            term.write("\x1b[2J\x1b[H");
            term.write(data.scrollback);
        } else {
            term.write("\x1b[90m[No scrollback available]\x1b[0m\r\n");
        }
    } catch (e) {
        term.write("\x1b[31m[Failed to load scrollback]\x1b[0m\r\n");
    }

    // "Open locally" button
    const info = remoteTerminals.find(t => t.tid === tid && t.machine_id === machineId);
    const btnBar = document.createElement("div");
    btnBar.className = "remote-actions";
    btnBar.innerHTML = `<button class="open-local-btn">${info?.has_summary ? "Open locally (with context)" : "Open locally"}</button>`;
    viewerEl.insertBefore(btnBar, viewerEl.firstChild);

    btnBar.querySelector(".open-local-btn").addEventListener("click", async () => {
        if (!info) return;
        // Map remote cwd to local path (same project name, different base path)
        const projectName = info.cwd.split(/[\\/]/).pop();
        const resp = await fetch("/api/terminals", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                cwd: info.cwd,  // Will need to be adjusted for different machines
                launch_claude: true,
                label: info.label || projectName,
            }),
        });
        const result = await resp.json();
        if (result.ok) {
            connectTerminal(result.terminal_id, result.label, info.cwd);
            // If there's a compact summary, paste it after Claude starts
            if (info.compact_summary) {
                setTimeout(() => {
                    const t = terminals[result.terminal_id];
                    if (t && t.ws && t.ws.readyState === WebSocket.OPEN) {
                        const contextPrompt = `Here is context from my previous session on another machine. Use this to understand what I was working on:\n\n${info.compact_summary}`;
                        t.ws.send(JSON.stringify({ type: "input", data: contextPrompt + "\n" }));
                    }
                }, 5000); // Wait for Claude to fully start
            }
        }
    });

    // Store for cleanup
    viewerEl._term = term;
    viewerEl._fitAddon = fitAddon;

    if (pathBar && info) pathBar.textContent = `${info.hostname}: ${info.cwd}`;
}

// Clean up remote viewer when switching to a local terminal
const origSwitchTerminal = switchTerminal;
switchTerminal = function(tid) {
    activeRemoteId = null;
    const viewer = terminalContainer.querySelector(".remote-viewer");
    if (viewer) {
        if (viewer._term) viewer._term.dispose();
        viewer.remove();
    }
    remoteSection?.querySelectorAll(".remote-tab").forEach(el => el.classList.remove("active"));
    origSwitchTerminal(tid);
};

