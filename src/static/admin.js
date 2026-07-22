const API_BASE = '';
let adminKey = sessionStorage.getItem('grasp_admin_key') || '';

// Theme

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

// Sidebar

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

// Authentication

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
    dropdown.classList.toggle('dropdown-menu-open');
}

document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('adminMenuDropdown');
    const btn = document.getElementById('adminMenuBtn');
    if (dropdown && btn && !btn.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.classList.remove('dropdown-menu-open');
    }
    // Close any open data table action dropdowns
    document.querySelectorAll('.dropdown-menu-content.dropdown-menu-open').forEach(dd => {
        if (!dd.closest('.sidebar-menu-item') && !dd.contains(e.target)) {
            const trigger = dd.previousElementSibling;
            if (!trigger || !trigger.contains(e.target)) {
                dd.classList.remove('dropdown-menu-open');
            }
        }
    });
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

// Screen routing

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

// API helpers

function adminHeaders(extra = {}) {
    return { 'X-Admin-Key': adminKey, ...extra };
}

// Status polling

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
            return `<li class="connector-item">
                ${iconHtml} <span style="margin-left:6px">${name}</span>
                <span class="connector-status-pill ${dotClass}">${pillLabel}</span>
            </li>`;
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

// Pending changes

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

// Sync

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

// Utilities

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

// User management

let usersPage = 0;
const USERS_PER_PAGE = 10;
let usersFilterText = '';
let usersSortCol = 'status';
let usersSortAsc = true;
let allUsersData = [];

function toggleActionDropdown(event, id) {
    event.stopPropagation();
    // Close all other open dropdowns first
    document.querySelectorAll('.dropdown-menu-content.dropdown-menu-open').forEach(dd => {
        if (dd.id !== id) dd.classList.remove('dropdown-menu-open');
    });
    const dd = document.getElementById(id);
    if (dd) dd.classList.toggle('dropdown-menu-open');
}

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
        allUsersData = data.users || [];

        if (!allUsersData.length) {
            card.innerHTML = '<div class="data-table-empty">No registered users yet</div>';
            return;
        }

        renderUsersTable();
    } catch (e) {
        card.innerHTML = `<p style="color:var(--danger)">Error loading users: ${escapeHtml(e.message)}</p>`;
    }
}

function renderUsersTable() {
    const card = document.getElementById('usersCard');

    const ALL_ROLES = [
        'Intern', 'Junior Associate', 'Associate', 'Senior Associate',
        'Team Lead', 'Manager', 'Director', 'Principal', 'Vice President', 'Partner',
    ];

    const statusOrder = { pending_approval: 0, approved: 1, rejected: 2 };

    // Capture focus state
    const searchInput = card.querySelector('.data-table-search');
    const focusActive = document.activeElement === searchInput;
    const cursorStart = searchInput ? searchInput.selectionStart : 0;
    const cursorEnd = searchInput ? searchInput.selectionEnd : 0;

    // Filter
    let filtered = allUsersData;
    if (usersFilterText) {
        const q = usersFilterText.toLowerCase();
        filtered = allUsersData.filter(u => {
            const name = `${u.first_name || ''} ${u.last_name || ''}`.toLowerCase();
            return name.includes(q) || (u.email || '').toLowerCase().includes(q);
        });
    }

    // Sort
    filtered.sort((a, b) => {
        let va, vb;
        if (usersSortCol === 'status') {
            va = statusOrder[a.status] ?? 9;
            vb = statusOrder[b.status] ?? 9;
        } else if (usersSortCol === 'name') {
            va = `${a.first_name || ''} ${a.last_name || ''}`.toLowerCase();
            vb = `${b.first_name || ''} ${b.last_name || ''}`.toLowerCase();
        } else if (usersSortCol === 'email') {
            va = (a.email || '').toLowerCase();
            vb = (b.email || '').toLowerCase();
        } else if (usersSortCol === 'joined') {
            va = a.created_at || '';
            vb = b.created_at || '';
        } else {
            va = a[usersSortCol] || '';
            vb = b[usersSortCol] || '';
        }
        if (va < vb) return usersSortAsc ? -1 : 1;
        if (va > vb) return usersSortAsc ? 1 : -1;
        return 0;
    });

    // Paginate
    const totalPages = Math.ceil(filtered.length / USERS_PER_PAGE);
    if (usersPage >= totalPages) usersPage = Math.max(0, totalPages - 1);
    const pageItems = filtered.slice(usersPage * USERS_PER_PAGE, (usersPage + 1) * USERS_PER_PAGE);

    const sortIcon = (col) => {
        if (usersSortCol !== col) return '<span class="sort-icon">↕</span>';
        return usersSortAsc ? '<span class="sort-icon">↑</span>' : '<span class="sort-icon">↓</span>';
    };
    const sortClass = (col) => {
        let cls = 'sortable';
        if (usersSortCol === col) cls += usersSortAsc ? ' sort-asc' : ' sort-desc';
        return cls;
    };

    let html = `
        <div class="data-table-toolbar">
            <input type="text" class="data-table-search" placeholder="Filter by name or email..."
                value="${escapeHtml(usersFilterText)}" oninput="usersFilterText=this.value;usersPage=0;renderUsersTable()">
        </div>
        <div class="data-table-wrapper">
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="width:25%" class="${sortClass('name')}" onclick="usersSortCol='name';usersSortAsc=usersSortCol==='name'?!usersSortAsc:true;renderUsersTable()">Name ${sortIcon('name')}</th>
                        <th style="width:25%" class="${sortClass('email')}" onclick="usersSortCol='email';usersSortAsc=usersSortCol==='email'?!usersSortAsc:true;renderUsersTable()">Email ${sortIcon('email')}</th>
                        <th style="width:10%">Auth</th>
                        <th style="width:15%">Role</th>
                        <th style="width:15%" class="${sortClass('status')}" onclick="usersSortCol='status';usersSortAsc=usersSortCol==='status'?!usersSortAsc:true;renderUsersTable()">Status ${sortIcon('status')}</th>
                        <th style="width:10%" class="${sortClass('joined')}" onclick="usersSortCol='joined';usersSortAsc=usersSortCol==='joined'?!usersSortAsc:true;renderUsersTable()">Joined ${sortIcon('joined')}</th>
                        <th style="width:50px"></th>
                    </tr>
                </thead>
                <tbody>`;

    if (pageItems.length === 0) {
        html += `<tr><td colspan="7" class="data-table-empty">No results.</td></tr>`;
    }

    for (const u of pageItems) {
        const fullName = `${u.first_name || ''} ${u.last_name || ''}`.trim() || '—';
        const initials = (u.first_name || '?')[0].toUpperCase();
        const avatarContent = u.profile_picture ? `<img src="${u.profile_picture}" alt="Avatar" style="width:100%;height:100%;object-fit:cover;border-radius:50%">` : initials;
        const statusClass = u.status === 'approved' ? 'status-approved' : u.status === 'rejected' ? 'status-rejected' : 'status-pending';
        const statusLabel = u.status === 'pending_approval' ? 'Pending' : u.status.charAt(0).toUpperCase() + u.status.slice(1);
        const authIcon = u.auth_method === 'google' ? 'Google' : 'Email';
        const joinedAt = u.created_at ? timeAgo(u.created_at) : '—';
        const ddId = `action-dd-user-${u.id}`;

        // Role cell
        let roleHtml = '';
        if (u.status === 'pending_approval') {
            const opts = '<option value="" disabled selected>— Select —</option>' +
                ALL_ROLES.map(r => `<option value="${r}">${r}</option>`).join('');
            roleHtml = `<select class="role-select" id="role-${u.id}">${opts}</select>`;
        } else if (u.status === 'approved') {
            const opts = ALL_ROLES.map(r => `<option value="${r}" ${r === u.role ? 'selected' : ''}>${r}</option>`).join('');
            roleHtml = `<span id="role-display-${u.id}">${escapeHtml(u.role || '—')}</span>
                        <select class="role-select" id="role-${u.id}" style="display:none;" data-original-role="${escapeHtml(u.role || '')}" onchange="confirmRoleChange('${u.id}', this.value)">${opts}</select>`;
        } else {
            roleHtml = `<span style="color:var(--text-tertiary);font-size:12px">—</span>`;
        }

        // Actions dropdown
        let actionsHtml = '';
        if (u.status === 'pending_approval') {
            actionsHtml = `
                <div class="dropdown-menu-group">
                    <div class="dropdown-menu-label">Actions</div>
                    <button class="dropdown-menu-item" onclick="approveUserAction('${u.id}')">✓ Approve</button>
                    <button class="dropdown-menu-item dropdown-menu-item-destructive" onclick="rejectUserAction('${u.id}')">✗ Reject</button>
                </div>`;
        } else if (u.status === 'approved') {
            actionsHtml = `
                <div class="dropdown-menu-group">
                    <div class="dropdown-menu-label">Actions</div>
                    <button class="dropdown-menu-item" onclick="enableRoleEdit('${u.id}')">Update Role</button>
                    <button class="dropdown-menu-item dropdown-menu-item-destructive" onclick="rejectUserAction('${u.id}')">Revoke Access</button>
                </div>`;
        } else {
            actionsHtml = `
                <div class="dropdown-menu-group">
                    <div class="dropdown-menu-label">Actions</div>
                    <button class="dropdown-menu-item" onclick="approveUserAction('${u.id}')">✓ Re-approve</button>
                </div>`;
        }

        html += `<tr>
            <td>
                <div style="display:flex;align-items:center;gap:10px">
                    <div class="user-card-avatar" style="width:30px;height:30px;font-size:12px;flex-shrink:0">${avatarContent}</div>
                    <span class="cell-primary">${escapeHtml(fullName)}</span>
                </div>
            </td>
            <td class="cell-email">${escapeHtml(u.email)}</td>
            <td>${authIcon}</td>
            <td>${roleHtml}</td>
            <td><span class="status-pill ${statusClass}">${statusLabel}</span></td>
            <td style="color:var(--text-tertiary);font-size:12px">${joinedAt}</td>
            <td class="data-table-actions">
                <div class="dropdown-menu" style="position:relative">
                    <button class="data-table-action-btn" onclick="toggleActionDropdown(event, '${ddId}')">⋯</button>
                    <div class="dropdown-menu-content dropdown-side-bottom dropdown-align-end" id="${ddId}" style="min-width:160px">
                        ${actionsHtml}
                    </div>
                </div>
            </td>
        </tr>`;
    }

    html += `</tbody></table></div>
        <div class="data-table-pagination">
            <div class="data-table-pagination-info">${filtered.length} user(s) total</div>
            <div class="data-table-pagination-controls">
                <button class="data-table-pagination-btn" onclick="usersPage--;renderUsersTable()" ${usersPage <= 0 ? 'disabled' : ''}>Previous</button>
                <button class="data-table-pagination-btn" onclick="usersPage++;renderUsersTable()" ${usersPage >= totalPages - 1 ? 'disabled' : ''}>Next</button>
            </div>
        </div>`;

    card.innerHTML = html;

    // Restore focus
    if (focusActive) {
        const newSearchInput = card.querySelector('.data-table-search');
        if (newSearchInput) {
            newSearchInput.focus();
            newSearchInput.setSelectionRange(cursorStart, cursorEnd);
        }
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

function enableRoleEdit(userId) {
    document.getElementById(`role-display-${userId}`).style.display = 'none';
    const select = document.getElementById(`role-${userId}`);
    select.style.display = 'inline-block';
    select.focus();

    // Close dropdown menu if open
    document.querySelectorAll('.dropdown-menu-content.dropdown-menu-open').forEach(dd => {
        dd.classList.remove('dropdown-menu-open');
    });
}

function cancelRoleEdit(userId) {
    const select = document.getElementById(`role-${userId}`);
    if (select) {
        select.value = select.getAttribute('data-original-role');
        select.style.display = 'none';
    }
    const display = document.getElementById(`role-display-${userId}`);
    if (display) display.style.display = 'inline-block';
}

function confirmRoleChange(userId, newRole) {
    const select = document.getElementById(`role-${userId}`);
    const oldRole = select ? select.getAttribute('data-original-role') : '';
    if (newRole === oldRole) {
        cancelRoleEdit(userId);
        return;
    }

    // Use custom modal
    document.getElementById('roleConfirmText').textContent = `Are you sure you want to change this user's role to ${newRole}?`;

    const confirmBtn = document.getElementById('roleConfirmBtn');
    // Remove old listeners by cloning
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);

    newConfirmBtn.onclick = () => {
        closeRoleConfirmModal();
        changeRoleAction(userId, newRole);
    };

    // Handle cancel via modal close
    const modal = document.getElementById('roleConfirmModal');
    modal.classList.add('active');

    // Store userId on modal to cancel on close
    modal.setAttribute('data-cancel-userid', userId);
}

function closeRoleConfirmModal() {
    const modal = document.getElementById('roleConfirmModal');
    modal.classList.remove('active');
    const userId = modal.getAttribute('data-cancel-userid');
    if (userId) {
        cancelRoleEdit(userId);
        modal.removeAttribute('data-cancel-userid');
    }
}

async function changeRoleAction(userId, explicitRole) {
    const select = document.getElementById(`role-${userId}`);
    const role = explicitRole || (select ? select.value : 'Associate');
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

// Contribution management

let currentContributionId = null;

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

let contribPage = 0;
const CONTRIBS_PER_PAGE = 10;
let contribFilterText = '';
let allContribsData = [];

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
        allContribsData = data.contributions || [];

        if (!allContribsData.length) {
            card.innerHTML = '<div class="data-table-empty"><p style="color:var(--text-tertiary);font-size:13px">No pending contributions</p><p style="color:var(--text-tertiary);font-size:11px;margin-top:6px">User submissions will appear here for review</p></div>';
            return;
        }

        renderContribsTable();
    } catch (e) {
        card.innerHTML = `<p style="color:var(--danger)">Error loading contributions: ${e.message}</p>`;
    }
}

function renderContribsTable() {
    const card = document.getElementById('contributionsCard');
    const typeIcons = { document: '📄', code: '💻', plain_text: '📝' };
    const typeLabels = { document: 'Document', code: 'Code', plain_text: 'Plain Text' };

    // Capture focus state
    const searchInput = card.querySelector('.data-table-search');
    const focusActive = document.activeElement === searchInput;
    const cursorStart = searchInput ? searchInput.selectionStart : 0;
    const cursorEnd = searchInput ? searchInput.selectionEnd : 0;

    // Filter
    let filtered = allContribsData;
    if (contribFilterText) {
        const q = contribFilterText.toLowerCase();
        filtered = allContribsData.filter(c => {
            return (c.title || '').toLowerCase().includes(q) ||
                (c.submitted_by || '').toLowerCase().includes(q);
        });
    }

    // Paginate
    const totalPages = Math.ceil(filtered.length / CONTRIBS_PER_PAGE);
    if (contribPage >= totalPages) contribPage = Math.max(0, totalPages - 1);
    const pageItems = filtered.slice(contribPage * CONTRIBS_PER_PAGE, (contribPage + 1) * CONTRIBS_PER_PAGE);

    let html = `
        <div class="data-table-toolbar">
            <input type="text" class="data-table-search" placeholder="Filter by title or submitter..."
                value="${escapeHtml(contribFilterText)}" oninput="contribFilterText=this.value;contribPage=0;renderContribsTable()">
        </div>
        <div class="data-table-wrapper">
            <table class="data-table">
                <thead>
                    <tr>
                        <th style="width:30%">Title</th>
                        <th style="width:15%">Type</th>
                        <th style="width:25%">Submitted By</th>
                        <th style="width:20%">File</th>
                        <th style="width:10%">Submitted</th>
                        <th style="width:50px"></th>
                    </tr>
                </thead>
                <tbody>`;

    if (pageItems.length === 0) {
        html += `<tr><td colspan="6" class="data-table-empty">No results.</td></tr>`;
    }

    for (const c of pageItems) {
        const icon = typeIcons[c.content_type] || '📄';
        const typeLabel = typeLabels[c.content_type] || c.content_type;
        const hasFile = c.original_filename ? escapeHtml(c.original_filename) : '—';
        const ddId = `action-dd-contrib-${c.id}`;

        html += `<tr>
            <td>
                <span class="cell-primary" style="cursor:pointer" onclick="openContributionReview('${c.id}')">${escapeHtml(c.title)}</span>
            </td>
            <td><span class="type-badge">${icon} ${typeLabel}</span></td>
            <td class="cell-email">${escapeHtml(c.submitted_by)}</td>
            <td style="font-size:12px;color:var(--text-tertiary)">${hasFile}</td>
            <td style="color:var(--text-tertiary);font-size:12px">${timeAgo(c.submitted_at)}</td>
            <td class="data-table-actions">
                <div class="dropdown-menu" style="position:relative">
                    <button class="data-table-action-btn" onclick="toggleActionDropdown(event, '${ddId}')">⋯</button>
                    <div class="dropdown-menu-content dropdown-side-bottom dropdown-align-end" id="${ddId}" style="min-width:160px">
                        <div class="dropdown-menu-group">
                            <div class="dropdown-menu-label">Actions</div>
                            <button class="dropdown-menu-item" onclick="openContributionReview('${c.id}')">Review</button>
                        </div>
                    </div>
                </div>
            </td>
        </tr>`;
    }

    html += `</tbody></table></div>
        <div class="data-table-pagination">
            <div class="data-table-pagination-info">${filtered.length} contribution(s) total</div>
            <div class="data-table-pagination-controls">
                <button class="data-table-pagination-btn" onclick="contribPage--;renderContribsTable()" ${contribPage <= 0 ? 'disabled' : ''}>Previous</button>
                <button class="data-table-pagination-btn" onclick="contribPage++;renderContribsTable()" ${contribPage >= totalPages - 1 ? 'disabled' : ''}>Next</button>
            </div>
        </div>`;

    card.innerHTML = html;

    // Restore focus
    if (focusActive) {
        const newSearchInput = card.querySelector('.data-table-search');
        if (newSearchInput) {
            newSearchInput.focus();
            newSearchInput.setSelectionRange(cursorStart, cursorEnd);
        }
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

// Sidebar sections

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
