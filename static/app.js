// Poll sessions and render the dashboard

const container = document.getElementById("sessions");

function timeAgo(ts) {
    if (!ts) return "never";
    const seconds = Math.floor(Date.now() / 1000 - ts);
    if (seconds < 5) return "just now";
    if (seconds < 60) return seconds + "s ago";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
    return Math.floor(seconds / 3600) + "h ago";
}

function statusLabel(status) {
    const labels = {
        running: "Running",
        waiting_input: "Waiting for Input",
        permission_needed: "Permission Needed",
        idle: "Idle",
        done: "Done",
    };
    return labels[status] || status;
}

function renderSession(s) {
    const extraClass = s.needs_attention ? " attention" : s.status === "done" ? " done" : "";
    const dismissBtn = s.needs_attention
        ? `<button class="dismiss-btn" onclick="dismiss('${s.session_id}')">dismiss</button>`
        : "";
    const focusBtn = s.pid && s.status !== "done"
        ? `<button class="focus-btn" onclick="focusSession('${s.session_id}')" title="Flash this session's terminal in the taskbar">find</button>`
        : "";

    return `
    <div class="session${extraClass}">
        <div class="session-header">
            <div>
                <input class="session-label"
                       value="${escHtml(s.label || s.repo || 'unnamed')}"
                       data-sid="${s.session_id}"
                       onblur="saveLabel(this)"
                       onkeydown="if(event.key==='Enter')this.blur()">
                <span class="session-id">${s.session_id.slice(0, 12)}</span>
            </div>
            <div>
                ${focusBtn}
                <span class="status status-${s.status}">${statusLabel(s.status)}</span>
                ${dismissBtn}
            </div>
        </div>
        <div class="session-details">
            <span><span class="detail-label">repo:</span> ${escHtml(s.repo || "—")}</span>
            <span><span class="detail-label">cwd:</span> ${escHtml(s.cwd || "—")}</span>
            <span><span class="detail-label">updated:</span> ${timeAgo(s.updated_at)}</span>
        </div>
        ${s.last_message ? `<div class="last-message">${escHtml(s.last_message)}</div>` : ""}
    </div>`;
}

function escHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

async function fetchSessions() {
    try {
        const resp = await fetch("/api/sessions");
        const sessions = await resp.json();
        if (sessions.length === 0) {
            container.innerHTML = '<p class="empty">No sessions yet. Start a Claude Code session with hooks configured.</p>';
            return;
        }
        container.innerHTML = sessions.map(renderSession).join("");
    } catch (e) {
        // Keep current content on fetch error
    }
}

async function saveLabel(input) {
    const sid = input.dataset.sid;
    const label = input.value.trim();
    try {
        await fetch(`/api/sessions/${sid}/label`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label }),
        });
    } catch (e) {
        // Ignore
    }
}

async function focusSession(sid) {
    try {
        await fetch(`/api/sessions/${sid}/focus`, { method: "POST" });
    } catch (e) {
        // Ignore
    }
}

async function dismiss(sid) {
    try {
        await fetch(`/api/sessions/${sid}/dismiss`, {
            method: "POST",
        });
        fetchSessions();
    } catch (e) {
        // Ignore
    }
}

// Poll every 2 seconds
fetchSessions();
setInterval(fetchSessions, 2000);
