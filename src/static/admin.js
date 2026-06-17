/* ── Grasp Admin Dashboard — Frontend Logic ──────────────── */

const API_BASE = '';
let adminKey = sessionStorage.getItem('grasp_admin_key') || '';

// ── Authentication ────────────────────────────────────────

async function authenticateAdmin() {
    const input = document.getElementById('adminKeyInput');
    const key = input.value.trim();
    if (!key) return;

    // Verify key by calling a protected endpoint
    try {
        const res = await fetch(`${API_BASE}/api/sync/status`, {
            headers: { 'X-Admin-Key': key },
        });
        if (res.status === 403) {
            document.getElementById('authError').style.display = 'block';
            return;
        }
        adminKey = key;
        sessionStorage.setItem('grasp_admin_key', key);
        showAdminDashboard();
    } catch (e) {
        document.getElementById('authError').style.display = 'block';
    }
}

function showAdminDashboard() {
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('adminApp').style.display = 'flex';
    refreshStatus();
    checkPendingChanges();
    loadSyncHistory();
    setInterval(refreshStatus, 15000);
    setInterval(checkPendingChanges, 15000);
}

// Auto-authenticate if key is stored in session
document.addEventListener('DOMContentLoaded', () => {
    if (adminKey) {
        showAdminDashboard();
    }
});

// ── Admin API helper ──────────────────────────────────────

function adminHeaders(extra = {}) {
    return { 'X-Admin-Key': adminKey, ...extra };
}

// ── Status Polling ────────────────────────────────────────

async function refreshStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const data = await res.json();

        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        if (data.status === 'syncing') {
            dot.className = 'status-dot syncing';
            text.textContent = 'Syncing';
        } else {
            dot.className = 'status-dot online';
            text.textContent = 'Online';
        }

        const lastSync = document.getElementById('lastSyncTime');
        if (data.last_sync && data.last_sync.timestamp) {
            lastSync.textContent = timeAgo(data.last_sync.timestamp);
        }

        const docCount = document.getElementById('docCount');
        if (data.document_stats && data.document_stats.total !== undefined) {
            docCount.textContent = data.document_stats.total.toLocaleString();
        }

        const nextSync = document.getElementById('nextSync');
        if (data.next_scheduled) {
            nextSync.textContent = timeAgo(data.next_scheduled, true);
        }

        // Connectors — pill badge style (matching main page)
        const container = document.getElementById('connectorsContainer');
        const connectors = data.connector_health || {};
        const names = { confluence: 'Confluence', jira: 'Jira', sharepoint: 'SharePoint', slack: 'Slack', notion: 'Notion' };
        const icons = { confluence: '📘', jira: '🔷', sharepoint: '📂', slack: '💬', notion: '📝' };

        container.innerHTML = Object.entries(names).map(([key, name]) => {
            const health = connectors[key];
            const dotClass = health === true ? 'healthy' : health === false ? 'unhealthy' : 'unknown';
            const pillLabel = health === true ? 'Active' : health === false ? 'Error' : 'N/A';
            return `<div class="connector-item">
                <span class="connector-dot ${dotClass}"></span>
                ${icons[key]} ${name}
                <span class="connector-status-pill ${dotClass}">${pillLabel}</span>
            </div>`;
        }).join('');

        // Update sync status card
        updateSyncStatusCard(data);

    } catch (e) {
        console.error('Status refresh failed:', e);
    }
}

function updateSyncStatusCard(data) {
    const card = document.getElementById('syncStatusCard');
    const ls = data.last_sync;

    let html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">';
    html += `<div class="stat-card"><div class="stat-number" style="color:var(--text-primary)">${data.status === 'syncing' ? '⟳' : '✓'}</div><div class="stat-label">${data.status === 'syncing' ? 'Syncing' : 'Idle'}</div></div>`;
    html += `<div class="stat-card"><div class="stat-number" style="color:var(--text-primary)">${data.document_stats?.total ?? '—'}</div><div class="stat-label">Documents</div></div>`;
    html += `<div class="stat-card"><div class="stat-number" style="color:var(--text-primary)">${data.vector_index?.total_chunks ?? '—'}</div><div class="stat-label">Index Chunks</div></div>`;
    html += '</div>';

    if (ls) {
        html += `<div style="margin-top:20px;font-size:12px;color:var(--text-secondary);line-height:1.8">`;
        html += `Last sync: <strong style="color:var(--text-primary)">${ls.type || 'unknown'}</strong> — ${ls.total_docs ?? 0} docs — ${timeAgo(ls.timestamp)}`;
        if (ls.workers) {
            html += '<div style="margin-top:10px">';
            for (const [name, info] of Object.entries(ls.workers)) {
                const icon = info.status === 'completed' ? '✓' : '✗';
                const color = info.status === 'completed' ? 'var(--success)' : 'var(--danger)';
                html += `<div style="padding:2px 0"><span style="color:${color}">${icon}</span> ${name}: ${info.docs ?? 0} docs</div>`;
            }
            html += '</div>';
        }
        html += '</div>';
    }

    card.innerHTML = html;
}

async function loadSyncHistory() {
    const card = document.getElementById('syncHistoryCard');
    try {
        const res = await fetch(`${API_BASE}/api/sync/history`, {
            headers: adminHeaders(),
        });
        if (!res.ok) {
            card.innerHTML = '<p style="color:var(--text-secondary)">No sync history available.</p>';
            return;
        }
        const history = await res.json();
        if (!history || !history.length) {
            card.innerHTML = '<p style="color:var(--text-secondary)">No sync history yet.</p>';
            return;
        }

        let html = '';
        for (const entry of history.slice(0, 10)) {
            html += `<div style="padding:10px 0;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-secondary);line-height:1.6">`;
            html += `<strong style="color:var(--text-primary)">${entry.type || 'sync'}</strong> — ${entry.total_docs ?? 0} docs — <span style="color:var(--text-tertiary)">${timeAgo(entry.timestamp)}</span>`;
            html += '</div>';
        }
        card.innerHTML = html || '<p style="color:var(--text-secondary)">No history.</p>';
    } catch (e) {
        card.innerHTML = '<p style="color:var(--text-secondary)">Could not load history.</p>';
    }
}

// ── Pending Changes ───────────────────────────────────────

async function checkPendingChanges() {
    try {
        const res = await fetch(`${API_BASE}/api/changes/pending`, {
            headers: adminHeaders(),
        });
        const data = await res.json();

        const badge = document.getElementById('pendingBadge');
        if (data.has_pending && data.changeset) {
            const total = data.changeset.summary?.total_changes || 0;
            document.getElementById('pendingCount').textContent = total;
            badge.style.display = 'inline-flex';
        } else {
            badge.style.display = 'none';
        }
    } catch (e) {
        console.error('Pending check failed:', e);
    }
}

function openPendingModal() {
    document.getElementById('pendingModal').classList.add('active');
    loadPendingDetails();
}

function closePendingModal() {
    document.getElementById('pendingModal').classList.remove('active');
}

async function loadPendingDetails() {
    const body = document.getElementById('pendingModalBody');
    try {
        const res = await fetch(`${API_BASE}/api/changes/pending`, {
            headers: adminHeaders(),
        });
        const data = await res.json();

        if (!data.has_pending || !data.changeset) {
            body.innerHTML = '<p style="color: var(--text-secondary)">No pending changes.</p>';
            return;
        }

        const cs = data.changeset;
        const s = cs.summary || {};

        let html = `
            <div class="change-stats">
                <div class="stat-card added">
                    <div class="stat-number">${s.total_added || 0}</div>
                    <div class="stat-label">Added</div>
                </div>
                <div class="stat-card modified">
                    <div class="stat-number">${s.total_modified || 0}</div>
                    <div class="stat-label">Modified</div>
                </div>
                <div class="stat-card deleted">
                    <div class="stat-number">${s.total_deleted || 0}</div>
                    <div class="stat-label">Deleted</div>
                </div>
            </div>
        `;

        if (cs.by_type && Object.keys(cs.by_type).length > 0) {
            html += '<div style="margin-bottom:16px"><strong style="font-size:11px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.8px">By Type:</strong>';
            for (const [type, counts] of Object.entries(cs.by_type)) {
                const parts = [];
                if (counts.added) parts.push(`+${counts.added}`);
                if (counts.modified) parts.push(`~${counts.modified}`);
                if (counts.deleted) parts.push(`-${counts.deleted}`);
                html += `<div style="font-size:12px;padding:3px 0;color:var(--text-secondary)">&nbsp;&nbsp;${type}: ${parts.join(', ')}</div>`;
            }
            html += '</div>';
        }

        html += '<div class="change-file-list">';
        const files = cs.files || {};
        for (const f of (files.added || []).slice(0, 30)) {
            html += `<div class="file-item"><span class="file-badge added">A</span>${escapeHtml(f)}</div>`;
        }
        for (const f of (files.modified || []).slice(0, 30)) {
            html += `<div class="file-item"><span class="file-badge modified">M</span>${escapeHtml(f)}</div>`;
        }
        for (const f of (files.deleted || []).slice(0, 30)) {
            html += `<div class="file-item"><span class="file-badge deleted">D</span>${escapeHtml(f)}</div>`;
        }

        const total = (files.added?.length || 0) + (files.modified?.length || 0) + (files.deleted?.length || 0);
        if (total > 90) {
            html += `<div style="padding:8px;color:var(--text-tertiary);font-size:12px">...and ${total - 90} more files</div>`;
        }
        html += '</div>';

        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = `<p style="color:var(--danger)">Error loading changes: ${e.message}</p>`;
    }
}

async function approveChanges() {
    const msg = document.getElementById('commitMessage').value || null;
    try {
        const res = await fetch(`${API_BASE}/api/changes/approve`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg }),
        });
        const data = await res.json();
        if (data.status === 'committed') {
            closePendingModal();
            checkPendingChanges();
            showToast('Changes committed & pushed ✓', 'success');
        } else {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

async function rejectChanges() {
    if (!confirm('Are you sure? This will revert all uncommitted changes.')) return;
    try {
        await fetch(`${API_BASE}/api/changes/reject`, {
            method: 'POST',
            headers: adminHeaders(),
        });
        closePendingModal();
        checkPendingChanges();
        showToast('Changes rejected', 'warning');
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

// ── Sync Trigger ──────────────────────────────────────────

async function triggerSync() {
    const btn = document.getElementById('syncBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Syncing...';

    try {
        const res = await fetch(`${API_BASE}/api/sync/trigger`, {
            method: 'POST',
            headers: adminHeaders(),
        });
        const data = await res.json();
        showToast(data.message, 'success');
    } catch (e) {
        showToast(`Sync error: ${e.message}`, 'error');
    }

    setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = '<span>⟳</span> Trigger Sync';
        refreshStatus();
    }, 3000);
}

// ── Utility ───────────────────────────────────────────────

function escapeHtml(text) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
}

function timeAgo(dateStr, future = false) {
    try {
        const date = new Date(dateStr);
        const now = new Date();
        const diff = future ? date - now : now - date;
        const seconds = Math.floor(Math.abs(diff) / 1000);

        if (seconds < 60) return future ? 'in <1m' : '<1m ago';
        if (seconds < 3600) return `${future ? 'in ' : ''}${Math.floor(seconds / 60)}m${future ? '' : ' ago'}`;
        if (seconds < 86400) return `${future ? 'in ' : ''}${Math.floor(seconds / 3600)}h${future ? '' : ' ago'}`;
        return `${future ? 'in ' : ''}${Math.floor(seconds / 86400)}d${future ? '' : ' ago'}`;
    } catch {
        return '—';
    }
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease-out';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
