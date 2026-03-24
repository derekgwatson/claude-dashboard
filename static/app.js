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
        scrollback: 50000,
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

    ws.onmessage = (event) => {
        term.write(event.data);
    };

    ws.onopen = () => {
        setTimeout(() => {
            if (terminals[tid]) terminals[tid].fitAddon.fit();
        }, 100);
    };

    ws.onclose = () => {
        term.write("\r\n\x1b[90m[terminal disconnected]\x1b[0m\r\n");
    };

    term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "input", data }));
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
                if (text && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "input", data: text }));
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
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "resize", cols, rows }));
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
                if (data.ok && ws.readyState === WebSocket.OPEN) {
                    const path = data.path.includes(" ") ? `"${data.path}"` : data.path;
                    ws.send(JSON.stringify({ type: "input", data: path + " " }));
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

loadSettings().then(() => reconnectTerminals());

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

