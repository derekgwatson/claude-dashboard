// Claude Session Dashboard — status monitor

const sessionsContainer = document.getElementById("sessions-container");
const emptyState = document.getElementById("empty-state");
const remoteSection = document.getElementById("remote-section");
const syncStatusEl = document.getElementById("sync-status");

const STATUS_LABELS = {
    running: "Working",
    waiting_input: "Waiting for input",
    permission_needed: "Needs permission",
    done: "Done",
    idle: "Idle",
};

const STATUS_PRIORITY = {
    permission_needed: 0,
    waiting_input: 1,
    running: 2,
    idle: 3,
    done: 4,
};

let sessions = [];
let selectedSessionId = null;

// ---------------------------------------------------------------------------
// Session polling
// ---------------------------------------------------------------------------

async function pollSessions() {
    try {
        const resp = await fetch("/api/sessions");
        sessions = await resp.json();
        renderSessions();
    } catch (e) {}
}

function renderSessions() {
    if (sessions.length === 0) {
        sessionsContainer.innerHTML = "";
        emptyState.style.display = "flex";
        return;
    }
    emptyState.style.display = "none";

    // Filter out old done sessions (> 1 hour)
    const now = Date.now() / 1000;
    const visible = sessions.filter(s =>
        s.status !== "done" || (now - s.updated_at) < 3600
    );

    if (visible.length === 0) {
        sessionsContainer.innerHTML = "";
        emptyState.style.display = "flex";
        return;
    }

    sessionsContainer.innerHTML = visible.map(s => {
        const status = s.status || "idle";
        const statusLabel = STATUS_LABELS[status] || status;
        const label = s.label || s.repo || shortPath(s.cwd) || "Unknown";
        const isSelected = s.session_id === selectedSessionId;
        const attention = s.needs_attention ? " attention" : "";
        const age = timeAgo(s.updated_at);

        return `<div class="session-card${isSelected ? " selected" : ""}${attention}" data-sid="${escHtml(s.session_id)}">
            <div class="session-header">
                <span class="session-status status-${status}" title="${statusLabel}"></span>
                <span class="session-label">${escHtml(label)}</span>
                <span class="session-age">${age}</span>
            </div>
            <div class="session-path">${escHtml(s.cwd || "")}</div>
            <div class="session-message">${escHtml(s.last_message || "")}</div>
            <div class="session-actions">
                ${s.needs_attention ? `<button class="btn-dismiss" data-sid="${escHtml(s.session_id)}">Dismiss</button>` : ""}
                ${status !== "done" ? `<button class="btn-label" data-sid="${escHtml(s.session_id)}">Rename</button>` : ""}
            </div>
        </div>`;
    }).join("");
}

function shortPath(cwd) {
    if (!cwd) return "";
    const parts = cwd.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || "";
}

function timeAgo(ts) {
    if (!ts) return "";
    const seconds = Math.floor(Date.now() / 1000 - ts);
    if (seconds < 10) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

function escHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

sessionsContainer.addEventListener("click", (e) => {
    // Dismiss button
    const dismissBtn = e.target.closest(".btn-dismiss");
    if (dismissBtn) {
        e.stopPropagation();
        const sid = dismissBtn.dataset.sid;
        fetch(`/api/sessions/${sid}/dismiss`, { method: "POST" }).then(() => pollSessions());
        return;
    }

    // Rename button
    const labelBtn = e.target.closest(".btn-label");
    if (labelBtn) {
        e.stopPropagation();
        const sid = labelBtn.dataset.sid;
        const session = sessions.find(s => s.session_id === sid);
        const current = session?.label || session?.repo || "";
        const newLabel = prompt("Session label:", current);
        if (newLabel !== null) {
            fetch(`/api/sessions/${sid}/label`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label: newLabel }),
            }).then(() => pollSessions());
        }
        return;
    }

    // Card selection
    const card = e.target.closest(".session-card");
    if (card) {
        selectedSessionId = card.dataset.sid;
        renderSessions();
    }
});

// ---------------------------------------------------------------------------
// Remote terminals (from other machines via cloud sync)
// ---------------------------------------------------------------------------

let remoteTerminals = [];

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
            const alive = t.alive ? '<span class="remote-alive"></span>' : "";
            html += `<div class="remote-tab">
                <span class="remote-tab-label">${alive}${escHtml(t.label || t.cwd)}</span>
            </div>`;
        }
    }
    remoteSection.innerHTML = html;
    loadSyncStatus();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

pollSessions();
setInterval(pollSessions, 2000);

loadSyncStatus();
loadRemoteTerminals();
setInterval(loadRemoteTerminals, 30000);
