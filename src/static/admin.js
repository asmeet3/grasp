/* ── Grasp Admin Dashboard — Frontend Logic ──────────────── */

const API_BASE = '';
let adminKey = sessionStorage.getItem('grasp_admin_key') || '';

// ── Theme Toggle ──────────────────────────────────────────

function initTheme() {
    const saved = localStorage.getItem('grasp_theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    }
    document.addEventListener('DOMContentLoaded', updateThemeIcon);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    if (current === 'light') {
        document.documentElement.removeAttribute('data-theme');
        localStorage.setItem('grasp_theme', 'dark');
    } else {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('grasp_theme', 'light');
    }
    updateThemeIcon();
}

function updateThemeIcon() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const menuIcon = document.getElementById('themeMenuIcon');
    const menuLabel = document.getElementById('themeMenuLabel');
    if (menuIcon) menuIcon.textContent = isLight ? '☀️' : '🌙';
    if (menuLabel) menuLabel.textContent = isLight ? 'Dark Mode' : 'Light Mode';
}

// Apply theme immediately
initTheme();

// ── Sidebar Collapse ─────────────────────────────────────

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('grasp_sidebar_collapsed', sidebar.classList.contains('collapsed') ? '1' : '0');
}

(function initSidebar() {
    const collapsed = localStorage.getItem('grasp_sidebar_collapsed');
    if (collapsed === '1') {
        const sidebar = document.getElementById('sidebar');
        if (sidebar) sidebar.classList.add('collapsed');
    }
})();

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

function adminLogout() {
    sessionStorage.removeItem('grasp_admin_key');
    adminKey = '';
    window.location.reload();
}

function toggleAdminMenu(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('adminMenuDropdown');
    if (!dropdown) return;
    const isVisible = dropdown.style.display !== 'none';
    dropdown.style.display = isVisible ? 'none' : 'block';
}

document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('adminMenuDropdown');
    const btn = document.getElementById('adminMenuBtn');
    if (dropdown && btn && !btn.contains(e.target)) {
        dropdown.style.display = 'none';
    }
});

function showAdminDashboard() {
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('adminApp').style.display = 'flex';
    refreshStatus();
    checkPendingChanges();
    checkContributionCount();
    checkUserPendingCount();
    
    // Default to Home screen
    showAdminScreen('Home');

    setInterval(refreshStatus, 15000);
    setInterval(checkPendingChanges, 15000);
    setInterval(checkContributionCount, 15000);
    setInterval(checkUserPendingCount, 15000);
}

// ── Screen Routing ────────────────────────────────────────

function showAdminScreen(screenName) {
    // Hide all screens
    document.querySelectorAll('.admin-screen').forEach(el => el.style.display = 'none');
    // Remove active class from all nav items
    document.querySelectorAll('.admin-nav-item').forEach(el => el.classList.remove('active'));

    const titleEl = document.getElementById('adminScreenTitle');

    if (screenName === 'Home') {
        document.getElementById('screenHome').style.display = 'block';
        document.getElementById('navHome').classList.add('active');
        titleEl.textContent = 'Dashboard';
        loadSyncHistory();
    } else if (screenName === 'Users') {
        document.getElementById('screenUsers').style.display = 'block';
        document.getElementById('navUsers').classList.add('active');
        titleEl.textContent = 'User Management';
        loadUsers();
    } else if (screenName === 'Contributions') {
        document.getElementById('screenContributions').style.display = 'block';
        document.getElementById('navContributions').classList.add('active');
        titleEl.textContent = 'Contribution Requests';
        loadContributions();
    }
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
        container.innerHTML = Object.entries(names).map(([key, name]) => {
            const health = connectors[key];
            const dotClass = health === true ? 'healthy' : health === false ? 'unhealthy' : 'unknown';
            const pillLabel = health === true ? 'Active' : health === false ? 'Error' : 'N/A';
            const iconHtml = `<img src="/icons/${key}-dark.svg" class="theme-icon-dark" alt="${name}"><img src="/icons/${key}-light.svg" class="theme-icon-light" alt="${name}">`;
            return `<div class="connector-item">
                <span class="connector-dot ${dotClass}"></span>
                ${iconHtml} <span style="margin-left:6px">${name}</span>
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

let expandedFiles = new Set();

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
    expandedFiles.clear();
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

        // File list header with toggle all button
        const files = cs.files || {};
        const allFiles = [
            ...(files.added || []).map(f => ({ path: f, type: 'added' })),
            ...(files.modified || []).map(f => ({ path: f, type: 'modified' })),
            ...(files.deleted || []).map(f => ({ path: f, type: 'deleted' })),
        ];

        html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <strong style="font-size:11px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:0.8px">Files (${allFiles.length})</strong>
            <button class="diff-toggle-all" onclick="toggleAllDiffs()">Expand All</button>
        </div>`;

        html += '<div class="change-file-list" id="fileListContainer">';
        for (const file of allFiles.slice(0, 60)) {
            const badgeClass = file.type;
            const badgeLabel = file.type === 'added' ? 'A' : file.type === 'modified' ? 'M' : 'D';
            const fileId = btoa(file.path).replace(/[^a-zA-Z0-9]/g, '_');
            const isExpanded = expandedFiles.has(file.path);

            html += `<div class="file-item expandable ${isExpanded ? 'expanded' : ''}" onclick="toggleFileDiff('${escapeHtml(file.path)}', '${fileId}')" style="cursor:pointer">
                <span class="file-badge ${badgeClass}">${badgeLabel}</span>
                <span style="flex:1;overflow:hidden;text-overflow:ellipsis">${escapeHtml(file.path)}</span>
                <span class="file-expand-icon">▶</span>
            </div>
            <div id="diff-${fileId}" style="display:${isExpanded ? 'block' : 'none'}"></div>`;
        }

        if (allFiles.length > 60) {
            html += `<div style="padding:8px;color:var(--text-tertiary);font-size:12px">...and ${allFiles.length - 60} more files</div>`;
        }
        html += '</div>';

        body.innerHTML = html;

        // Load any already-expanded diffs
        for (const file of allFiles) {
            if (expandedFiles.has(file.path)) {
                const fileId = btoa(file.path).replace(/[^a-zA-Z0-9]/g, '_');
                loadFileDiff(file.path, fileId);
            }
        }

    } catch (e) {
        body.innerHTML = `<p style="color:var(--danger)">Error loading changes: ${e.message}</p>`;
    }
}

async function toggleFileDiff(filePath, fileId) {
    const panel = document.getElementById(`diff-${fileId}`);
    const item = panel.previousElementSibling;

    if (expandedFiles.has(filePath)) {
        expandedFiles.delete(filePath);
        panel.style.display = 'none';
        item.classList.remove('expanded');
    } else {
        expandedFiles.add(filePath);
        panel.style.display = 'block';
        item.classList.add('expanded');
        loadFileDiff(filePath, fileId);
    }
}

async function loadFileDiff(filePath, fileId) {
    const panel = document.getElementById(`diff-${fileId}`);
    panel.innerHTML = '<div class="diff-loading">Loading diff...</div>';

    try {
        const res = await fetch(`${API_BASE}/api/changes/diff/${encodeURIComponent(filePath)}`, {
            headers: adminHeaders(),
        });
        const data = await res.json();

        if (!data.diff) {
            panel.innerHTML = '<div class="diff-panel"><div class="diff-empty">No diff available (new file or binary)</div></div>';
            return;
        }

        panel.innerHTML = renderDiff(data.diff, filePath);
    } catch (e) {
        panel.innerHTML = `<div class="diff-panel"><div class="diff-empty">Error: ${e.message}</div></div>`;
    }
}

function renderDiff(diffText, filePath) {
    const lines = diffText.split('\n');
    let addCount = 0, delCount = 0;

    let linesHtml = '';
    for (const line of lines) {
        if (line.startsWith('+++') || line.startsWith('---')) {
            linesHtml += `<div class="diff-line header">${escapeHtml(line)}</div>`;
        } else if (line.startsWith('@@')) {
            linesHtml += `<div class="diff-line header">${escapeHtml(line)}</div>`;
        } else if (line.startsWith('+')) {
            addCount++;
            linesHtml += `<div class="diff-line add">${escapeHtml(line)}</div>`;
        } else if (line.startsWith('-')) {
            delCount++;
            linesHtml += `<div class="diff-line del">${escapeHtml(line)}</div>`;
        } else {
            linesHtml += `<div class="diff-line context">${escapeHtml(line || ' ')}</div>`;
        }
    }

    const stats = [];
    if (addCount) stats.push(`+${addCount}`);
    if (delCount) stats.push(`-${delCount}`);

    return `<div class="diff-panel">
        <div class="diff-panel-header">
            <span>${escapeHtml(filePath)}</span>
            <span style="color:var(--text-secondary)">${stats.join(' / ') || 'no changes'}</span>
        </div>
        <div class="diff-content">${linesHtml}</div>
    </div>`;
}

function toggleAllDiffs() {
    const container = document.getElementById('fileListContainer');
    if (!container) return;

    const fileItems = container.querySelectorAll('.file-item.expandable');
    const allExpanded = expandedFiles.size >= fileItems.length;

    for (const item of fileItems) {
        const next = item.nextElementSibling;
        const fileId = next?.id?.replace('diff-', '');
        if (!fileId) continue;

        // Reconstruct path from the item text
        const pathSpan = item.querySelector('span[style*="flex:1"]');
        const filePath = pathSpan ? pathSpan.textContent : '';

        if (allExpanded) {
            expandedFiles.delete(filePath);
            next.style.display = 'none';
            item.classList.remove('expanded');
        } else {
            if (!expandedFiles.has(filePath)) {
                expandedFiles.add(filePath);
                next.style.display = 'block';
                item.classList.add('expanded');
                loadFileDiff(filePath, fileId);
            }
        }
    }

    // Update button text
    const btn = container.parentElement.querySelector('.diff-toggle-all');
    if (btn) {
        btn.textContent = allExpanded ? 'Expand All' : 'Collapse All';
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
            const branchInfo = data.branch ? ` → branch: ${data.branch}` : '';
            showToast(`Changes committed & pushed ✓${branchInfo}`, 'success');
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

// ── User Management ───────────────────────────────────────

async function loadUsers() {
    const card = document.getElementById('usersCard');
    try {
        const res = await fetch(`${API_BASE}/api/admin/users`, {
            headers: adminHeaders(),
        });
        if (!res.ok) {
            card.innerHTML = '<p style="color:var(--text-secondary)">Could not load users. Check admin key.</p>';
            return;
        }
        const data = await res.json();
        const users = data.users || [];

        if (!users.length) {
            card.innerHTML = '<div style="text-align:center;padding:24px"><p style="color:var(--text-tertiary);font-size:13px">No registered users yet</p></div>';
            return;
        }

        const statusOrder = { pending_approval: 0, approved: 1, rejected: 2 };
        users.sort((a, b) => (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9));

        const ALL_ROLES = [
            'Intern',
            'Junior Associate',
            'Associate',
            'Senior Associate',
            'Team Lead',
            'Manager',
            'Director',
            'Principal',
            'Vice President',
            'Partner',
        ];

        let html = '<div class="users-list">';
        for (const u of users) {
            const statusClass = u.status === 'approved' ? 'approved' : u.status === 'rejected' ? 'rejected' : 'pending';
            const statusLabel = u.status === 'pending_approval' ? 'Pending' : u.status.charAt(0).toUpperCase() + u.status.slice(1);
            const fullName = `${u.first_name || ''} ${u.last_name || ''}`.trim() || '—';
            const initials = (u.first_name || '?')[0].toUpperCase();
            const joinedAt = u.created_at ? timeAgo(u.created_at) : '—';
            const authIcon = u.auth_method === 'google' ? '🔵' : '✉️';

            html += `<div class="user-card">
                <div class="user-card-header">
                    <div class="user-card-avatar">${initials}</div>
                    <div class="user-card-info">
                        <div class="user-card-name">${escapeHtml(fullName)}</div>
                        <div class="user-card-email">${authIcon} ${escapeHtml(u.email)}</div>
                    </div>
                    <span class="contribution-status-pill ${statusClass}">${statusLabel}</span>
                </div>
                <div class="user-card-meta">
                    <span>Joined ${joinedAt}</span>
                    ${u.role ? `<span>Role: <strong>${escapeHtml(u.role)}</strong></span>` : ''}
                </div>
                <div class="user-card-actions">`;

            if (u.status === 'pending_approval') {
                const pendingRoleOptions = `<option value="" disabled selected>— Select Role —</option>` +
                    ALL_ROLES.map(r => `<option value="${r}">${r}</option>`).join('');
                html += `<select class="user-role-select" id="role-${u.id}">${pendingRoleOptions}</select>
                    <button class="approve-btn" style="font-size:12px;padding:6px 14px" onclick="approveUserAction('${u.id}')">✓ Approve</button>
                    <button class="reject-btn" style="font-size:12px;padding:6px 14px" onclick="rejectUserAction('${u.id}')">✗ Reject</button>`;
            } else if (u.status === 'approved') {
                const approvedRoleOptions = ALL_ROLES.map(r => `<option value="${r}" ${r === u.role ? 'selected' : ''}>${r}</option>`).join('');
                html += `<select class="user-role-select" id="role-${u.id}">
                    ${approvedRoleOptions}
                </select>
                    <button class="approve-btn" style="font-size:12px;padding:6px 14px;background:var(--bg-glass);color:var(--text-secondary)" onclick="changeRoleAction('${u.id}')">Update Role</button>
                    <button class="reject-btn" style="font-size:12px;padding:6px 14px" onclick="rejectUserAction('${u.id}')">Revoke</button>`;
            } else {
                html += `<span style="color:var(--text-tertiary);font-size:12px">Account rejected</span>`;
            }

            html += `</div></div>`;
        }
        html += '</div>';
        card.innerHTML = html;

    } catch (e) {
        card.innerHTML = `<p style="color:var(--danger)">Error loading users: ${escapeHtml(e.message)}</p>`;
    }
}

async function approveUserAction(userId) {
    const select = document.getElementById(`role-${userId}`);
    const role = select ? select.value : '';

    // Validate that a role has been selected
    if (!role) {
        showToast('Please select a role before approving', 'warning');
        if (select) {
            select.classList.add('role-select-error');
            select.focus();
            setTimeout(() => select.classList.remove('role-select-error'), 2000);
        }
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/approve`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ role }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(`User approved as ${role} ✓`, 'success');
            loadUsers();
            checkUserPendingCount();
        } else {
            showToast(`Error: ${data.detail || 'Approval failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

async function rejectUserAction(userId) {
    if (!confirm('Are you sure you want to reject/revoke this user?')) return;
    try {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/reject`, {
            method: 'POST',
            headers: adminHeaders(),
        });
        if (res.ok) {
            showToast('User rejected', 'warning');
            loadUsers();
            checkUserPendingCount();
        } else {
            const data = await res.json();
            showToast(`Error: ${data.detail || 'Rejection failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

async function changeRoleAction(userId) {
    const select = document.getElementById(`role-${userId}`);
    const role = select ? select.value : 'Associate';
    try {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/role`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ role }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(`Role updated to ${role} ✓`, 'success');
            loadUsers();
        } else {
            showToast(`Error: ${data.detail || 'Update failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

// ── Contribution Management ───────────────────────────────

let currentContributionId = null;

function scrollToContributions() {
    showAdminScreen('Contributions');
}

async function checkContributionCount() {
    try {
        const res = await fetch(`${API_BASE}/api/contributions/count`, {
            headers: adminHeaders(),
        });
        const data = await res.json();
        
        // Update nav badge instead of inline header badge
        const badge = document.getElementById('navContributionsBadge');
        if (badge) {
            if (data.count > 0) {
                badge.textContent = data.count;
                badge.style.display = 'inline-block';
            } else {
                badge.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('Failed to check contribution count:', e);
    }
}

async function checkUserPendingCount() {
    try {
        const res = await fetch(`${API_BASE}/api/admin/users`, {
            headers: adminHeaders(),
        });
        if (!res.ok) return;
        const data = await res.json();
        const users = data.users || [];
        const pendingCount = users.filter(u => u.status === 'pending_approval').length;

        const badge = document.getElementById('navUsersBadge');
        if (badge) {
            if (pendingCount > 0) {
                badge.textContent = pendingCount;
                badge.style.display = 'inline-block';
            } else {
                badge.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('Failed to check user pending count:', e);
    }
}

async function loadContributions() {
    const card = document.getElementById('contributionsCard');
    try {
        const res = await fetch(`${API_BASE}/api/contributions/pending`, {
            headers: adminHeaders(),
        });
        if (!res.ok) {
            card.innerHTML = '<p style="color:var(--text-secondary)">Could not load contributions.</p>';
            return;
        }
        const data = await res.json();

        if (!data.contributions || data.contributions.length === 0) {
            card.innerHTML = '<div style="text-align:center;padding:24px"><p style="color:var(--text-tertiary);font-size:13px">No pending contributions</p><p style="color:var(--text-tertiary);font-size:11px;margin-top:6px">User submissions will appear here for review</p></div>';
            return;
        }

        let html = '<div class="contributions-list">';
        for (const c of data.contributions) {
            const typeIcons = { document: '📄', code: '💻', plain_text: '📝' };
            const typeLabels = { document: 'Document', code: 'Code', plain_text: 'Plain Text' };
            const icon = typeIcons[c.content_type] || '📄';
            const typeLabel = typeLabels[c.content_type] || c.content_type;
            const preview = c.content.substring(0, 120).replace(/\n/g, ' ') + (c.content.length > 120 ? '…' : '');
            const hasFile = c.original_filename ? ' · 📎 ' + escapeHtml(c.original_filename) : '';

            html += `<div class="contribution-card" onclick="openContributionReview('${c.id}')">
                <div class="contribution-card-header">
                    <div class="contribution-card-title">${icon} ${escapeHtml(c.title)}</div>
                    <span class="contribution-type-badge">${typeLabel}</span>
                </div>
                <div class="contribution-card-preview">${escapeHtml(preview)}</div>
                <div class="contribution-card-meta">
                    <span>By <strong>${escapeHtml(c.submitted_by)}</strong>${hasFile}</span>
                    <span>${timeAgo(c.submitted_at)}</span>
                </div>
            </div>`;
        }
        html += '</div>';
        card.innerHTML = html;

    } catch (e) {
        card.innerHTML = `<p style="color:var(--danger)">Error loading contributions: ${e.message}</p>`;
    }
}

async function openContributionReview(id) {
    currentContributionId = id;
    document.getElementById('contributionReviewModal').classList.add('active');
    const body = document.getElementById('contributionReviewBody');
    body.innerHTML = '<p style="color:var(--text-secondary)">Loading...</p>';

    try {
        const res = await fetch(`${API_BASE}/api/contributions/${id}`, {
            headers: adminHeaders(),
        });
        const c = await res.json();

        const typeIcons = { document: '📄', code: '💻', plain_text: '📝' };
        const typeLabels = { document: 'Document', code: 'Code', plain_text: 'Plain Text' };
        const isCode = c.content_type === 'code';

        // Build download button HTML if original file exists
        let downloadHtml = '';
        if (c.original_filename) {
            downloadHtml = `
            <div class="contribute-field">
                <label class="contribute-label">Original Document</label>
                <a href="${API_BASE}/api/contributions/${c.id}/download" class="download-btn" target="_blank">
                    📥 Download ${escapeHtml(c.original_filename)}
                </a>
            </div>`;
        }

        body.innerHTML = `
            <div class="contribution-review-meta">
                <div class="review-meta-item">
                    <span class="review-meta-label">Submitted by</span>
                    <span class="review-meta-value">${escapeHtml(c.submitted_by)}</span>
                </div>
                <div class="review-meta-item">
                    <span class="review-meta-label">Type</span>
                    <span class="review-meta-value">${typeIcons[c.content_type] || '📄'} ${typeLabels[c.content_type] || c.content_type}</span>
                </div>
                <div class="review-meta-item">
                    <span class="review-meta-label">Submitted</span>
                    <span class="review-meta-value">${timeAgo(c.submitted_at)}</span>
                </div>
                <div class="review-meta-item">
                    <span class="review-meta-label">Status</span>
                    <span class="contribution-status-pill pending">${c.status}</span>
                </div>
            </div>

            ${downloadHtml}

            <div class="contribute-field">
                <label class="contribute-label">Title</label>
                <input type="text" class="contribute-input" id="reviewTitle" value="${escapeHtml(c.title)}">
            </div>

            <div class="contribute-field">
                <label class="contribute-label">Content <span style="color:var(--text-tertiary);font-weight:400">— editable</span></label>
                <textarea class="contribute-textarea" id="reviewContent" rows="14" style="${isCode ? "font-family:'IBM Plex Mono',monospace;font-size:12.5px" : ''}">${escapeHtml(c.content)}</textarea>
            </div>
        `;

        document.getElementById('contributionAdminNotes').value = c.admin_notes || '';

    } catch (e) {
        body.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
    }
}

function closeContributionReview() {
    document.getElementById('contributionReviewModal').classList.remove('active');
    currentContributionId = null;
}

async function saveContributionEdits() {
    if (!currentContributionId) return;

    const title = document.getElementById('reviewTitle').value.trim();
    const content = document.getElementById('reviewContent').value;

    if (!title || !content) {
        showToast('Title and content cannot be empty', 'warning');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/contributions/${currentContributionId}`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, content }),
        });

        if (res.ok) {
            showToast('Edits saved ✓', 'success');
        } else {
            const data = await res.json();
            showToast(`Error: ${data.detail || 'Save failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

async function approveContribution() {
    if (!currentContributionId) return;

    // Save any edits first
    const title = document.getElementById('reviewTitle').value.trim();
    const content = document.getElementById('reviewContent').value;
    if (title && content) {
        await fetch(`${API_BASE}/api/contributions/${currentContributionId}`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, content }),
        });
    }

    const adminNotes = document.getElementById('contributionAdminNotes').value;

    try {
        const res = await fetch(`${API_BASE}/api/contributions/${currentContributionId}/approve`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ admin_notes: adminNotes }),
        });
        const data = await res.json();

        if (data.status === 'approved') {
            closeContributionReview();
            loadContributions();
            checkContributionCount();
            checkPendingChanges();
            showToast(`Approved! Classified as "${data.info_type}" — now in pending changes`, 'success');
        } else {
            showToast(`Error: ${data.message || 'Approval failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

async function rejectContribution() {
    if (!currentContributionId) return;
    if (!confirm('Are you sure you want to reject this contribution?')) return;

    const adminNotes = document.getElementById('contributionAdminNotes').value;

    try {
        const res = await fetch(`${API_BASE}/api/contributions/${currentContributionId}/reject`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ admin_notes: adminNotes }),
        });
        const data = await res.json();

        if (data.status === 'rejected') {
            closeContributionReview();
            loadContributions();
            checkContributionCount();
            showToast('Contribution rejected', 'warning');
        } else {
            showToast(`Error: ${data.message || 'Rejection failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

// ── Sidebar Section Toggles ───────────────────────────────

function toggleSidebarSection(section) {
    const bodyMap = { connectors: 'connectorsSectionBody' };
    const chevronMap = { connectors: 'connectorsChevron' };
    const toggleMap = { connectors: 'connectorsToggle' };

    const body = document.getElementById(bodyMap[section]);
    const chevron = document.getElementById(chevronMap[section]);
    const toggle = document.getElementById(toggleMap[section]);
    if (!body) return;

    const isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : '';
    if (chevron) {
        chevron.classList.toggle('open', !isOpen);
    }
    if (toggle) {
        toggle.setAttribute('aria-expanded', String(!isOpen));
    }
}
