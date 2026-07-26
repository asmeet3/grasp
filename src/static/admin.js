const API_BASE = '';
let adminKey = sessionStorage.getItem('grasp_admin_key') || '';
let bootstrapMode = false;
let adminIntervalsStarted = false;
let currentAdminUser = null;

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
    const key = input ? input.value.trim() : '';
    const token = localStorage.getItem('grasp_session_token');
    if (!key) {
        showAdminGateError('Enter the configured bootstrap key.');
        return;
    }

    try {
        const headers = {};
        if (token) headers['Authorization'] = `Bearer ${token}`;
        if (key) headers['X-Admin-Key'] = key;
        const res = await fetch(`${API_BASE}/api/admin/access`, { headers });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            showAdminGateError(data.detail || 'Invalid bootstrap key.');
            return;
        }
        const access = await res.json();
        adminKey = key;
        if (key) sessionStorage.setItem('grasp_admin_key', key);
        showAdminDashboard(Boolean(access.bootstrap));
    } catch (e) {
        showAdminGateError('Grasp could not verify bootstrap access. Try again.');
    }
}

function adminLogout() {
    sessionStorage.removeItem('grasp_admin_key');
    localStorage.removeItem('grasp_session_token');
    localStorage.removeItem('grasp_user');
    adminKey = '';
    redirectToAdminLogin();
}

function redirectToAdminLogin() {
    window.location.replace('/login?next=%2Fadmin');
}

function signInWithDifferentAccount() {
    sessionStorage.removeItem('grasp_admin_key');
    localStorage.removeItem('grasp_session_token');
    localStorage.removeItem('grasp_user');
    adminKey = '';
    redirectToAdminLogin();
}

function showAdminGateError(message) {
    const error = document.getElementById('authError');
    if (!error) return;
    error.textContent = message;
    error.style.display = 'block';
}

function showBootstrapGate(configured) {
    document.getElementById('adminApp').style.display = 'none';
    document.getElementById('authGate').style.display = 'flex';
    document.getElementById('adminGateTitle').textContent = configured
        ? 'Set up Grasp Admin'
        : 'Administrator setup unavailable';
    document.getElementById('adminGateMessage').textContent = configured
        ? 'No administrator account exists yet. Enter the one-time bootstrap key to establish the first administrator.'
        : 'No administrator exists and no bootstrap key is configured. Set GRASP_ADMIN_KEY on the server and restart Grasp.';
    document.getElementById('adminBootstrapForm').style.display = configured ? 'block' : 'none';
    document.getElementById('adminDeniedActions').style.display = 'none';
    const error = document.getElementById('authError');
    error.textContent = '';
    error.style.display = 'none';
}

function showAdminAccessDenied() {
    document.getElementById('adminApp').style.display = 'none';
    document.getElementById('authGate').style.display = 'flex';
    document.getElementById('adminGateTitle').textContent = 'Administrator access required';
    document.getElementById('adminGateMessage').textContent =
        'Your account is signed in, but it does not have Administrator access.';
    document.getElementById('adminBootstrapForm').style.display = 'none';
    document.getElementById('adminDeniedActions').style.display = 'flex';
    const error = document.getElementById('authError');
    error.textContent = '';
    error.style.display = 'none';
}

function showAdminStartupError() {
    document.getElementById('adminApp').style.display = 'none';
    document.getElementById('authGate').style.display = 'flex';
    document.getElementById('adminGateTitle').textContent = 'Grasp is unavailable';
    document.getElementById('adminGateMessage').textContent =
        'The server could not determine administrator status. Confirm that Grasp is running, then refresh this page.';
    document.getElementById('adminBootstrapForm').style.display = 'none';
    document.getElementById('adminDeniedActions').style.display = 'none';
    showAdminGateError('Administrator status check failed.');
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

    const clickTarget = e.target instanceof Element ? e.target : null;
    const clickedInsideCustomSelect = Boolean(
        clickTarget && clickTarget.closest('.shadcn-select, .shadcn-select-content')
    );
    if (!clickedInsideCustomSelect) closeAllShadcnSelects();

    const clickedInsideUsersTable = Boolean(clickTarget && clickTarget.closest('.users-table-shell'));
    const clickedInsideEditConfirmation = Boolean(
        clickTarget && clickTarget.closest('#roleConfirmModal, #accessConfirmModal')
    );
    if (editingUserId && !clickedInsideUsersTable && !clickedInsideCustomSelect && !clickedInsideEditConfirmation) {
        cancelUserEdit(editingUserId);
    }
});

async function loadAdminProfile() {
    const token = localStorage.getItem('grasp_session_token');
    let user = null;
    if (token) {
        try {
            const response = await fetch(`${API_BASE}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (response.ok) user = await response.json();
        } catch (_) {
            // Fall back to the last authenticated profile below.
        }
    }
    if (!user) {
        try {
            user = JSON.parse(localStorage.getItem('grasp_user') || 'null');
        } catch (_) {
            user = null;
        }
    }
    if (!user) return;
    currentAdminUser = user;

    const name = `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'Admin';
    const initials = (user.first_name || 'A')[0].toUpperCase();
    const nameElement = document.getElementById('adminProfileName');
    if (nameElement) nameElement.textContent = name;
    const menuNameElement = document.getElementById('adminMenuName');
    if (menuNameElement) menuNameElement.textContent = name;
    const accessElement = document.getElementById('adminProfileAccess');
    const menuAccessElement = document.getElementById('adminMenuAccess');
    if (accessElement) {
        const label = (user.system_role || 'member').replaceAll('_', ' ');
        accessElement.textContent = label.replace(/\b\w/g, character => character.toUpperCase());
        if (menuAccessElement) menuAccessElement.textContent = accessElement.textContent;
    }
    setAdminAvatar(document.getElementById('adminAvatar'), user.profile_picture, initials);
    setAdminAvatar(document.getElementById('adminMenuAvatar'), user.profile_picture, initials);
    if (allUsersData.length) renderUsersTable();
}

function showAdminDashboard(isBootstrap = false) {
    bootstrapMode = isBootstrap;
    document.getElementById('authGate').style.display = 'none';
    document.getElementById('adminApp').style.display = 'flex';
    loadAdminProfile();

    document.getElementById('statusPanel').style.display = isBootstrap ? 'none' : '';
    document.getElementById('connectorsToggle').style.display = isBootstrap ? 'none' : '';
    document.getElementById('connectorsSectionBody').style.display = isBootstrap ? 'none' : '';
    document.getElementById('navHome').style.display = isBootstrap ? 'none' : '';
    document.getElementById('navContributions').style.display = isBootstrap ? 'none' : '';
    document.getElementById('syncBtn').style.display = isBootstrap ? 'none' : '';

    if (isBootstrap) {
        showAdminScreen('Users');
        return;
    }

    refreshStatus();
    checkPendingChanges();
    checkContributionCount();
    checkUserPendingCount();

    // Default to Home screen
    showAdminScreen('Home');

    if (!adminIntervalsStarted) {
        adminIntervalsStarted = true;
        setInterval(refreshStatus, 15000);
        setInterval(checkPendingChanges, 15000);
        setInterval(checkContributionCount, 15000);
        setInterval(checkUserPendingCount, 15000);
    }
}

// Screen routing

function showAdminScreen(screenName) {
    // Hide all screens
    document.querySelectorAll('.admin-screen').forEach(el => el.style.display = 'none');
    // Remove active class from all nav items
    document.querySelectorAll('.admin-sidebar-button').forEach(el => el.classList.remove('active'));

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

async function getAdminBootstrapStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/admin/bootstrap/status`);
        if (!response.ok) return null;
        return await response.json();
    } catch (_) {
        return null;
    }
}

async function hasValidSignedInUser(token) {
    if (!token) return false;
    try {
        const response = await fetch(`${API_BASE}/api/auth/me`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        return response.ok;
    } catch (_) {
        return false;
    }
}

async function initializeAdminPage() {
    const token = localStorage.getItem('grasp_session_token');
    if (adminKey || token) {
        try {
            const res = await fetch(`${API_BASE}/api/admin/access`, {
                headers: bootstrapHeaders(),
            });
            if (res.ok) {
                const access = await res.json();
                showAdminDashboard(Boolean(access.bootstrap));
                return;
            }
        } catch (_) {
            // Resolve the correct logged-out, denied, or bootstrap state below.
        }
        sessionStorage.removeItem('grasp_admin_key');
        adminKey = '';
    }

    const signedIn = await hasValidSignedInUser(token);
    if (token && !signedIn) {
        localStorage.removeItem('grasp_session_token');
        localStorage.removeItem('grasp_user');
    }

    const bootstrapStatus = await getAdminBootstrapStatus();
    if (!bootstrapStatus) {
        showAdminStartupError();
        return;
    }
    if (bootstrapStatus.bootstrap_required) {
        showBootstrapGate(Boolean(bootstrapStatus.bootstrap_configured));
        return;
    }
    if (signedIn) {
        showAdminAccessDenied();
        return;
    }
    redirectToAdminLogin();
}

document.addEventListener('DOMContentLoaded', () => {
    initializeAdminPage();
});

// API helpers

function adminHeaders(extra = {}) {
    const token = localStorage.getItem('grasp_session_token');
    const headers = { ...extra };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
}

function bootstrapHeaders(extra = {}) {
    const headers = adminHeaders(extra);
    if (adminKey) headers['X-Admin-Key'] = adminKey;
    return headers;
}

// Status polling

async function refreshStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`, { headers: adminHeaders() });
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

function adminDashboardIcon(name) {
    const icons = {
        activity: '<path d="M4 12h3l2-5 4 10 2-5h5" />',
        database: '<ellipse cx="12" cy="5" rx="7" ry="3" /><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />',
        layers: '<path d="m12 3-9 5 9 5 9-5-9-5Z" /><path d="m3 12 9 5 9-5M3 16l9 5 9-5" />',
        clock: '<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />',
        history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" />',
        source: '<path d="M8 6h13M8 12h13M8 18h13" /><circle cx="3.5" cy="6" r="1" /><circle cx="3.5" cy="12" r="1" /><circle cx="3.5" cy="18" r="1" />',
        empty: '<path d="M4 5h16v14H4z" /><path d="M8 10h8M8 14h5" />',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.activity}</svg>`;
}

function adminDashboardEmptyState(title, description) {
    return `<div class="admin-dashboard-empty-item">
        <div class="admin-dashboard-item-media">${adminDashboardIcon('empty')}</div>
        <div class="admin-dashboard-item-content">
            <div class="admin-dashboard-item-title">${escapeHtml(title)}</div>
            <div class="admin-dashboard-item-description">${escapeHtml(description)}</div>
        </div>
    </div>`;
}

function updateSyncStatusCard(data) {
    const card = document.getElementById('syncStatusCard');
    const lastSync = data.last_sync;
    const isSyncing = data.status === 'syncing';
    const numberValue = value => typeof value === 'number' ? value.toLocaleString() : value ?? '—';
    const metrics = [
        {
            icon: 'activity',
            title: 'Sync engine',
            description: isSyncing ? 'Processing connected knowledge sources' : 'Ready for scheduled or manual runs',
            value: isSyncing ? 'Syncing' : 'Idle',
            tone: isSyncing ? 'warning' : 'success',
        },
        {
            icon: 'database',
            title: 'Documents',
            description: 'Knowledge records available to Grasp',
            value: numberValue(data.document_stats?.total),
            tone: 'neutral',
        },
        {
            icon: 'layers',
            title: 'Index chunks',
            description: 'Embedded segments ready for retrieval',
            value: numberValue(data.vector_index?.total_chunks),
            tone: 'neutral',
        },
        {
            icon: 'clock',
            title: 'Next sync',
            description: 'Next scheduled automation window',
            value: data.next_scheduled ? timeAgo(data.next_scheduled, true) : 'Not scheduled',
            tone: 'neutral',
        },
    ];

    let html = `<div class="admin-dashboard-item-grid" role="list">${metrics.map(metric => `
        <article class="admin-dashboard-item admin-dashboard-metric-item" role="listitem">
            <div class="admin-dashboard-item-media ${metric.tone}">${adminDashboardIcon(metric.icon)}</div>
            <div class="admin-dashboard-item-content">
                <div class="admin-dashboard-item-title">${metric.title}</div>
                <div class="admin-dashboard-item-description">${metric.description}</div>
            </div>
            <div class="admin-dashboard-item-actions">
                <strong class="admin-dashboard-metric-value ${metric.tone}">${metric.value}</strong>
            </div>
        </article>`).join('')}</div>`;

    html += '<div class="admin-dashboard-detail-block">';
    html += '<div class="admin-dashboard-detail-label">Latest run</div>';
    if (!lastSync) {
        html += adminDashboardEmptyState('No synchronization recorded', 'Run a sync to populate source and document activity.');
    } else {
        const workers = Object.entries(lastSync.workers || {});
        const failedWorkers = workers.filter(([, info]) => info.status !== 'completed');
        const resultTone = failedWorkers.length ? 'danger' : 'success';
        const resultLabel = failedWorkers.length ? 'Needs attention' : 'Completed';
        const typeLabel = String(lastSync.type || 'sync').replaceAll('_', ' ');
        const completedLabel = lastSync.timestamp ? timeAgo(lastSync.timestamp) : 'Time unavailable';

        html += `<article class="admin-dashboard-item admin-dashboard-latest-item">
            <div class="admin-dashboard-item-media ${resultTone}">${adminDashboardIcon('history')}</div>
            <div class="admin-dashboard-item-content">
                <div class="admin-dashboard-item-title">${escapeHtml(typeLabel)} sync</div>
                <div class="admin-dashboard-item-description">${numberValue(lastSync.total_docs ?? 0)} documents · ${completedLabel}</div>
            </div>
            <div class="admin-dashboard-item-actions">
                <span class="admin-dashboard-result-badge ${resultTone}">${resultLabel}</span>
            </div>
        </article>`;

        if (workers.length) {
            html += `<div class="admin-dashboard-worker-grid" role="list">${workers.map(([name, info]) => {
                const completed = info.status === 'completed';
                const statusLabel = String(info.status || 'unknown').replaceAll('_', ' ');
                return `<div class="admin-dashboard-item admin-dashboard-worker-item" role="listitem">
                    <div class="admin-dashboard-item-media ${completed ? 'success' : 'danger'}">${adminDashboardIcon('source')}</div>
                    <div class="admin-dashboard-item-content">
                        <div class="admin-dashboard-item-title">${escapeHtml(name)}</div>
                        <div class="admin-dashboard-item-description">${numberValue(info.docs ?? 0)} documents processed</div>
                    </div>
                    <div class="admin-dashboard-item-actions">
                        <span class="admin-dashboard-result-badge ${completed ? 'success' : 'danger'}">${escapeHtml(statusLabel)}</span>
                    </div>
                </div>`;
            }).join('')}</div>`;
        }
    }
    html += '</div>';
    card.innerHTML = html;
}

async function loadSyncHistory() {
    const card = document.getElementById('syncHistoryCard');
    try {
        const res = await fetch(`${API_BASE}/api/sync/history`, {
            headers: adminHeaders(),
        });
        if (!res.ok) {
            card.innerHTML = adminDashboardEmptyState('History unavailable', 'Grasp could not retrieve synchronization history.');
            return;
        }
        const history = await res.json();
        if (!history || !history.length) {
            card.innerHTML = adminDashboardEmptyState('No sync history yet', 'Completed synchronization runs will appear here.');
            return;
        }

        const recentHistory = history.slice(-10).reverse();
        const rows = recentHistory.map(entry => {
            const workers = Object.entries(entry.workers || {});
            const failedCount = workers.filter(([, info]) => info.status !== 'completed').length;
            const resultTone = failedCount ? 'danger' : 'success';
            const resultLabel = failedCount ? `${failedCount} failed` : 'Completed';
            const typeLabel = String(entry.type || 'sync').replaceAll('_', ' ');
            const completedLabel = entry.timestamp ? timeAgo(entry.timestamp) : '—';
            return `<tr>
                <td>
                    <div class="admin-dashboard-table-primary">${escapeHtml(typeLabel)}</div>
                    <div class="admin-dashboard-table-secondary">Synchronization run</div>
                </td>
                <td><span class="admin-dashboard-result-badge ${resultTone}">${resultLabel}</span></td>
                <td class="admin-dashboard-table-sources">${workers.length}</td>
                <td class="admin-dashboard-table-number">${Number(entry.total_docs ?? 0).toLocaleString()}</td>
                <td class="admin-dashboard-table-time"><time datetime="${escapeHtml(entry.timestamp || '')}">${completedLabel}</time></td>
            </tr>`;
        }).join('');

        card.innerHTML = `<div class="admin-dashboard-table-frame">
            <table class="admin-dashboard-table">
                <caption class="sr-only">Ten most recent synchronization runs</caption>
                <thead>
                    <tr>
                        <th scope="col">Sync</th>
                        <th scope="col">Result</th>
                        <th scope="col" class="admin-dashboard-table-sources">Sources</th>
                        <th scope="col" class="admin-dashboard-table-number">Documents</th>
                        <th scope="col" class="admin-dashboard-table-time">Completed</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            <div class="admin-dashboard-table-footer">Showing ${recentHistory.length} of ${history.length} recorded sync${history.length === 1 ? '' : 's'}</div>
        </div>`;
    } catch (e) {
        card.innerHTML = adminDashboardEmptyState('History unavailable', 'Grasp could not retrieve synchronization history.');
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
    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/changes/approve`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'active') {
            throw new Error(data.detail || data.error || 'Approval failed');
        }
        closePendingModal();
        await checkPendingChanges();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Approving and indexing changes...',
            success: 'Changes committed, indexed, and activated.',
            error: error => `Could not approve changes: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

async function rejectChanges() {
    if (!confirm('Reject this isolated proposal? Active knowledge will not change.')) return;
    const operation = (async () => {
        const response = await fetch(`${API_BASE}/api/changes/reject`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || data.error || 'Rejection failed');
        closePendingModal();
        await checkPendingChanges();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Rejecting proposed changes...',
            success: { type: 'warning', description: 'Changes rejected.' },
            error: error => `Could not reject changes: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

// Sync

async function triggerSync() {
    const btn = document.getElementById('syncBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Syncing...';

    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/sync/trigger`, {
            method: 'POST',
            headers: adminHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.message || 'Sync could not be started');
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Starting knowledge sync...',
            success: data => data.message || 'Knowledge sync started.',
            error: error => `Sync could not be started: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }

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

function safeProfilePictureUrl(value) {
    if (typeof value !== 'string' || !value) return '';
    if (/^data:image\/(png|jpeg|webp);base64,/i.test(value)) return value;
    try {
        const parsed = new URL(value);
        return parsed.protocol === 'https:' ? parsed.href : '';
    } catch (_) {
        return '';
    }
}

function setAdminAvatar(container, picture, initials) {
    if (!container) return;
    container.textContent = initials;
    const source = safeProfilePictureUrl(picture);
    if (!source) return;
    const image = document.createElement('img');
    image.src = source;
    image.alt = 'Avatar';
    image.referrerPolicy = 'no-referrer';
    image.style.cssText = 'width:100%;height:100%;object-fit:cover;border-radius:50%';
    image.addEventListener('error', () => {
        container.textContent = initials;
    }, { once: true });
    container.replaceChildren(image);
}

function avatarMarkup(picture, initials) {
    const source = safeProfilePictureUrl(picture);
    if (!source) return escapeHtml(initials);
    return `<img src="${escapeHtml(source)}" alt="Avatar" referrerpolicy="no-referrer" style="width:100%;height:100%;object-fit:cover;border-radius:50%" onerror="this.remove();this.parentElement.textContent='${escapeHtml(initials)}'">`;
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

// User management

let usersPage = 0;
const USERS_PER_PAGE = 10;
let usersFilterText = '';
let usersSortCol = 'status';
let usersSortAsc = true;
let allUsersData = [];
let editingUserId = null;
let revokeDialogTrigger = null;
const SYSTEM_ROLES = [
    ['member', 'Member'],
    ['knowledge_editor', 'Knowledge Editor'],
    ['reviewer', 'Reviewer'],
    ['operator', 'Operator'],
    ['administrator', 'Administrator'],
];

function userSelectIds(field, userId) {
    const prefix = field === 'role' ? 'role' : 'system-role';
    return {
        control: `${prefix}-control-${userId}`,
        input: `${prefix}-${userId}`,
        content: `${prefix}-content-${userId}`,
        display: `${prefix}-display-${userId}`,
    };
}

function shadcnSelectMarkup({
    field,
    userId,
    value,
    originalValue,
    options,
    label,
    placeholder,
    disabled = false,
    hidden = false,
}) {
    const ids = userSelectIds(field, userId);
    const selectedOption = options.find(([optionValue]) => optionValue === value);
    const selectedLabel = selectedOption ? selectedOption[1] : placeholder;
    const originalData = field === 'role'
        ? `data-original-role="${escapeHtml(originalValue || '')}"`
        : `data-original-system-role="${escapeHtml(originalValue || 'member')}"`;
    const items = options.map(([optionValue, optionLabel]) => {
        const selected = optionValue === value;
        return `<button type="button" class="shadcn-select-item" role="option" tabindex="-1"
            data-value="${escapeHtml(optionValue)}" data-state="${selected ? 'checked' : 'unchecked'}"
            aria-selected="${selected ? 'true' : 'false'}"
            onclick="selectShadcnOption(event, '${field}', '${userId}', '${optionValue}')"
            onkeydown="handleShadcnSelectItemKeydown(event, this)">
            <span class="shadcn-select-check" aria-hidden="true">✓</span>
            <span class="shadcn-select-item-label">${escapeHtml(optionLabel)}</span>
        </button>`;
    }).join('');

    return `<div class="shadcn-select user-edit-control" id="${ids.control}"
            data-field="${field}" data-content-id="${ids.content}"${hidden ? ' style="display:none"' : ''}>
        <input type="hidden" id="${ids.input}" value="${escapeHtml(value || '')}" ${originalData}>
        <button type="button" class="shadcn-select-trigger" aria-haspopup="listbox" aria-expanded="false"
            aria-controls="${ids.content}" aria-label="${escapeHtml(label)}"
            onclick="toggleShadcnSelect(event, '${ids.control}')"
            onkeydown="handleShadcnSelectTriggerKeydown(event, '${ids.control}')"${disabled ? ' disabled' : ''}>
            <span class="shadcn-select-value${selectedOption ? '' : ' placeholder'}">${escapeHtml(selectedLabel)}</span>
            <svg class="shadcn-select-chevron" viewBox="0 0 20 20" aria-hidden="true">
                <path d="m6 8 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
        </button>
        <div class="shadcn-select-content" id="${ids.content}" role="listbox"
            aria-label="${escapeHtml(label)}" data-owner-id="${ids.control}" data-field="${field}"
            data-user-id="${userId}" hidden>
            <div class="shadcn-select-viewport">${items}</div>
        </div>
    </div>`;
}

function positionShadcnSelect(root, content) {
    const trigger = root.querySelector('.shadcn-select-trigger');
    const triggerRect = trigger.getBoundingClientRect();
    const viewportPadding = 8;
    const width = Math.min(Math.max(triggerRect.width, 190), window.innerWidth - viewportPadding * 2);
    const left = Math.min(
        Math.max(triggerRect.left, viewportPadding),
        window.innerWidth - width - viewportPadding
    );

    content.style.width = `${width}px`;
    content.style.left = `${left}px`;
    content.style.top = `${triggerRect.bottom + 5}px`;
    const contentHeight = content.getBoundingClientRect().height;
    const availableBelow = window.innerHeight - triggerRect.bottom - viewportPadding;
    const availableAbove = triggerRect.top - viewportPadding;
    if (contentHeight > availableBelow && availableAbove > availableBelow) {
        content.style.top = `${Math.max(viewportPadding, triggerRect.top - contentHeight - 5)}px`;
    }
}

function openShadcnSelect(rootId, focusDirection = null) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const trigger = root.querySelector('.shadcn-select-trigger');
    if (!trigger || trigger.disabled) return;

    closeAllShadcnSelects(rootId);
    const content = document.getElementById(root.dataset.contentId);
    if (!content) return;
    content.hidden = false;
    document.body.appendChild(content);
    trigger.setAttribute('aria-expanded', 'true');
    root.dataset.open = 'true';
    positionShadcnSelect(root, content);

    if (focusDirection) {
        const items = Array.from(content.querySelectorAll('.shadcn-select-item'));
        if (!items.length) return;
        const selected = content.querySelector('[aria-selected="true"]');
        const item = focusDirection === 'last' ? items[items.length - 1] : selected || items[0];
        item.focus();
    }
}

function closeShadcnSelect(rootId, restoreFocus = false) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const trigger = root.querySelector('.shadcn-select-trigger');
    const content = document.getElementById(root.dataset.contentId);
    if (content) {
        content.hidden = true;
        content.removeAttribute('style');
        root.appendChild(content);
    }
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'false');
        if (restoreFocus) trigger.focus();
    }
    delete root.dataset.open;
}

function closeAllShadcnSelects(exceptRootId = null) {
    document.querySelectorAll('.shadcn-select[data-open="true"]').forEach(root => {
        if (root.id !== exceptRootId) closeShadcnSelect(root.id);
    });
}

function toggleShadcnSelect(event, rootId) {
    event.stopPropagation();
    const root = document.getElementById(rootId);
    if (!root) return;
    if (root.dataset.open === 'true') closeShadcnSelect(rootId);
    else openShadcnSelect(rootId);
}

function handleShadcnSelectTriggerKeydown(event, rootId) {
    if (!['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    openShadcnSelect(rootId, event.key === 'ArrowUp' ? 'last' : 'selected');
}

function handleShadcnSelectItemKeydown(event, item) {
    const content = item.closest('.shadcn-select-content');
    if (!content) return;
    const items = Array.from(content.querySelectorAll('.shadcn-select-item'));
    const currentIndex = items.indexOf(item);

    if (event.key === 'Escape') {
        event.preventDefault();
        closeShadcnSelect(content.dataset.ownerId, true);
        return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectShadcnOption(
            event,
            content.dataset.field,
            content.dataset.userId,
            item.dataset.value
        );
        return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;

    event.preventDefault();
    let nextIndex = currentIndex;
    if (event.key === 'ArrowDown') nextIndex = (currentIndex + 1) % items.length;
    if (event.key === 'ArrowUp') nextIndex = (currentIndex - 1 + items.length) % items.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = items.length - 1;
    items[nextIndex].focus();
}

function setShadcnSelectValue(field, userId, value) {
    const ids = userSelectIds(field, userId);
    const root = document.getElementById(ids.control);
    const input = document.getElementById(ids.input);
    if (!root || !input) return;

    input.value = value;
    const content = document.getElementById(ids.content);
    const items = content ? Array.from(content.querySelectorAll('.shadcn-select-item')) : [];
    const selectedItem = items.find(item => item.dataset.value === value);
    items.forEach(item => {
        const selected = item.dataset.value === value;
        item.dataset.state = selected ? 'checked' : 'unchecked';
        item.setAttribute('aria-selected', selected ? 'true' : 'false');
    });
    const valueElement = root.querySelector('.shadcn-select-value');
    if (valueElement && selectedItem) {
        valueElement.textContent = selectedItem.querySelector('.shadcn-select-item-label').textContent;
        valueElement.classList.remove('placeholder');
    }
    root.querySelector('.shadcn-select-trigger')?.classList.remove('role-select-error');
}

function selectShadcnOption(event, field, userId, value) {
    event.preventDefault();
    event.stopPropagation();
    const ids = userSelectIds(field, userId);
    setShadcnSelectValue(field, userId, value);
    closeShadcnSelect(ids.control, true);

    const user = allUsersData.find(item => item.id === userId);
    if (!user || user.status !== 'approved') return;
    if (field === 'role') confirmRoleChange(userId, value);
    else confirmSystemRoleChange(userId, value);
}

window.addEventListener('resize', () => closeAllShadcnSelects());
document.addEventListener('scroll', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target || !target.closest('.shadcn-select-content')) closeAllShadcnSelects();
}, true);

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
            headers: bootstrapMode ? bootstrapHeaders() : adminHeaders(),
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

function sortUsersBy(column) {
    if (usersSortCol === column) {
        usersSortAsc = !usersSortAsc;
    } else {
        usersSortCol = column;
        usersSortAsc = true;
    }
    usersPage = 0;
    renderUsersTable();
}

function setUsersSortColumn(column) {
    if (usersSortCol !== column) {
        usersSortCol = column;
        usersSortAsc = true;
    }
    usersPage = 0;
    renderUsersTable();
}

function toggleUsersSortDirection() {
    usersSortAsc = !usersSortAsc;
    usersPage = 0;
    renderUsersTable();
}

function renderUsersTable() {
    const card = document.getElementById('usersCard');
    closeAllShadcnSelects();
    editingUserId = null;
    const ALL_ROLES = [
        'Intern', 'Junior Associate', 'Associate', 'Senior Associate',
        'Team Lead', 'Manager', 'Director', 'Principal', 'Vice President', 'Partner',
    ];
    const statusOrder = { pending_approval: 0, approved: 1, rejected: 2 };

    const searchInput = card.querySelector('.user-table-search');
    const focusActive = document.activeElement === searchInput;
    const cursorStart = searchInput ? searchInput.selectionStart : 0;
    const cursorEnd = searchInput ? searchInput.selectionEnd : 0;

    const query = usersFilterText.trim().toLowerCase();
    const filtered = allUsersData.filter(user => {
        if (!query) return true;
        const name = `${user.first_name || ''} ${user.last_name || ''}`.toLowerCase();
        return name.includes(query) || (user.email || '').toLowerCase().includes(query);
    });

    filtered.sort((a, b) => {
        const aIsCurrentUser = Boolean(currentAdminUser && currentAdminUser.id === a.id);
        const bIsCurrentUser = Boolean(currentAdminUser && currentAdminUser.id === b.id);
        if (aIsCurrentUser !== bIsCurrentUser) return aIsCurrentUser ? -1 : 1;

        let valueA;
        let valueB;
        if (usersSortCol === 'status') {
            valueA = statusOrder[a.status] ?? 9;
            valueB = statusOrder[b.status] ?? 9;
        } else if (usersSortCol === 'name') {
            valueA = `${a.first_name || ''} ${a.last_name || ''}`.toLowerCase();
            valueB = `${b.first_name || ''} ${b.last_name || ''}`.toLowerCase();
        } else if (usersSortCol === 'email') {
            valueA = (a.email || '').toLowerCase();
            valueB = (b.email || '').toLowerCase();
        } else if (usersSortCol === 'joined') {
            valueA = a.created_at || '';
            valueB = b.created_at || '';
        } else {
            valueA = a[usersSortCol] || '';
            valueB = b[usersSortCol] || '';
        }
        if (valueA < valueB) return usersSortAsc ? -1 : 1;
        if (valueA > valueB) return usersSortAsc ? 1 : -1;
        return 0;
    });

    const totalPages = Math.ceil(filtered.length / USERS_PER_PAGE);
    if (usersPage >= totalPages) usersPage = Math.max(0, totalPages - 1);
    const pageItems = filtered.slice(usersPage * USERS_PER_PAGE, (usersPage + 1) * USERS_PER_PAGE);
    const visiblePage = totalPages ? usersPage + 1 : 1;
    const visibleTotalPages = Math.max(totalPages, 1);

    const sortIcon = column => {
        if (usersSortCol !== column) return '<span class="user-sort-icon" aria-hidden="true">↕</span>';
        return `<span class="user-sort-icon active" aria-hidden="true">${usersSortAsc ? '↑' : '↓'}</span>`;
    };
    const ariaSort = column => {
        if (usersSortCol !== column) return 'none';
        return usersSortAsc ? 'ascending' : 'descending';
    };

    let html = bootstrapMode
        ? `<div class="user-bootstrap-banner">
                <div><strong>Administrator setup required</strong><span>Claim administrator access to unlock user management and the full dashboard.</span></div>
                <button type="button" class="approve-btn" onclick="claimAdministratorAccess()">Make me Administrator</button>
           </div>`
        : '';

    html += `
        <div class="user-management-toolbar">
            <label class="user-search-field">
                <span class="user-search-icon" aria-hidden="true">⌕</span>
                <span class="sr-only">Search users</span>
                <input type="search" class="user-table-search" placeholder="Search by name or email"
                    aria-label="Search users by name or email" value="${escapeHtml(usersFilterText)}"
                    oninput="usersFilterText=this.value;usersPage=0;renderUsersTable()">
            </label>
            <span class="user-results-count">${filtered.length} of ${allUsersData.length} users</span>
            <div class="user-mobile-sort">
                <label for="usersMobileSort">Sort by</label>
                <select id="usersMobileSort" onchange="setUsersSortColumn(this.value)">
                    <option value="status" ${usersSortCol === 'status' ? 'selected' : ''}>Status</option>
                    <option value="name" ${usersSortCol === 'name' ? 'selected' : ''}>Name</option>
                    <option value="email" ${usersSortCol === 'email' ? 'selected' : ''}>Email</option>
                    <option value="joined" ${usersSortCol === 'joined' ? 'selected' : ''}>Joined</option>
                </select>
                <button type="button" class="user-sort-direction" onclick="toggleUsersSortDirection()"
                    aria-label="Reverse sort direction" title="Reverse sort direction">${usersSortAsc ? '↑' : '↓'}</button>
            </div>
        </div>
        <div class="users-table-shell">
            <table class="users-table">
                <caption class="sr-only">Registered Grasp users and their access settings</caption>
                <colgroup>
                    <col class="user-col-identity">
                    <col class="user-col-auth">
                    <col class="user-col-role">
                    <col class="user-col-access">
                    <col class="user-col-status">
                    <col class="user-col-joined">
                    <col class="user-col-actions">
                </colgroup>
                <thead>
                    <tr>
                        <th scope="col" aria-sort="${ariaSort('name')}"><button type="button" class="user-sort-button" onclick="sortUsersBy('name')">User ${sortIcon('name')}</button></th>
                        <th scope="col">Auth</th>
                        <th scope="col">Job role</th>
                        <th scope="col">System access</th>
                        <th scope="col" aria-sort="${ariaSort('status')}"><button type="button" class="user-sort-button" onclick="sortUsersBy('status')">Status ${sortIcon('status')}</button></th>
                        <th scope="col" aria-sort="${ariaSort('joined')}"><button type="button" class="user-sort-button" onclick="sortUsersBy('joined')">Joined ${sortIcon('joined')}</button></th>
                        <th scope="col" class="user-actions-heading">Actions</th>
                    </tr>
                </thead>
                <tbody>`;

    if (pageItems.length === 0) {
        html += `<tr class="user-empty-row"><td colspan="7"><div class="user-empty-state"><strong>No users found</strong><span>Try a different name or email.</span></div></td></tr>`;
    }

    for (const user of pageItems) {
        const isCurrentUser = Boolean(currentAdminUser && currentAdminUser.id === user.id);
        const status = user.status || 'pending_approval';
        const baseName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || 'Unnamed user';
        const initials = (user.first_name || user.email || '?')[0].toUpperCase();
        const avatarContent = avatarMarkup(user.profile_picture, initials);
        const statusClass = status === 'approved' ? 'status-approved' : status === 'rejected' ? 'status-rejected' : 'status-pending';
        const statusLabel = status === 'pending_approval' ? 'Pending' : status.charAt(0).toUpperCase() + status.slice(1);
        const authLabel = user.auth_method === 'google' ? 'Google' : 'Email';
        const authClass = user.auth_method === 'google' ? 'auth-google' : 'auth-email';
        const joinedAt = user.created_at ? timeAgo(user.created_at) : '—';

        const roleSelectOptions = ALL_ROLES.map(role => [role, role]);
        const roleControl = shadcnSelectMarkup({
            field: 'role',
            userId: user.id,
            value: user.role || '',
            originalValue: user.role || '',
            options: roleSelectOptions,
            label: `Job role for ${baseName}`,
            placeholder: 'Select role',
            hidden: status === 'approved',
        });
        const roleHtml = status === 'approved'
            ? `<span class="user-role-value" id="role-display-${user.id}">${escapeHtml(user.role || '—')}</span>${roleControl}`
            : roleControl;

        const currentSystemRole = user.system_role || 'member';
        const selectedSystemRole = bootstrapMode && status !== 'approved'
            ? 'administrator'
            : currentSystemRole;
        const accessTitle = isCurrentUser
            ? 'You cannot change your own access level'
            : bootstrapMode
                ? 'Claim administrator access before managing users'
                : 'Change access level';
        const accessDisabled = (status === 'approved' && (isCurrentUser || bootstrapMode)) || bootstrapMode;
        const accessControl = shadcnSelectMarkup({
            field: 'access',
            userId: user.id,
            value: selectedSystemRole,
            originalValue: currentSystemRole,
            options: SYSTEM_ROLES,
            label: `System access for ${baseName}. ${accessTitle}`,
            placeholder: 'Select access',
            disabled: accessDisabled,
            hidden: status === 'approved',
        });
        const accessHtml = status === 'approved'
            ? `<span class="user-role-value" id="system-role-display-${user.id}">${escapeHtml(systemRoleLabel(currentSystemRole))}</span>${accessControl}`
            : accessControl;

        const actionButtons = [];
        if (status === 'pending_approval' || status === 'rejected') {
            const approveLabel = status === 'rejected' ? 'Re-approve' : 'Approve';
            actionButtons.push(`<button type="button" class="user-row-action user-row-action-primary"
                onclick="approveUserAction('${user.id}')" aria-label="${approveLabel} ${escapeHtml(baseName)}" title="${approveLabel}">
                <span aria-hidden="true">✓</span><span class="user-action-label">${approveLabel}</span></button>`);
            if (status === 'pending_approval' && !bootstrapMode) {
                actionButtons.push(`<button type="button" class="user-row-action user-row-action-danger"
                    onclick="rejectUserAction('${user.id}')" aria-label="Reject ${escapeHtml(baseName)}" title="Reject">
                    <span aria-hidden="true">×</span><span class="user-action-label">Reject</span></button>`);
            }
        } else if (status === 'approved' && !bootstrapMode) {
            actionButtons.push(`<button type="button" class="user-row-action"
                onclick="enableUserEdit('${user.id}')" aria-label="Edit ${escapeHtml(baseName)}" title="Edit user">
                <span aria-hidden="true">✎</span><span class="user-action-label">Edit</span></button>`);
            if (!isCurrentUser) {
                actionButtons.push(`<button type="button" class="user-row-action user-row-action-danger"
                    onclick="openRevokeUserModal('${user.id}', this)" aria-label="Revoke access for ${escapeHtml(baseName)}" title="Revoke access">
                    <span aria-hidden="true">×</span><span class="user-action-label">Revoke</span></button>`);
            }
        }
        const actionsHtml = actionButtons.length
            ? `<div class="user-row-actions">${actionButtons.join('')}</div>`
            : '<span class="user-no-actions">—</span>';

        html += `<tr class="user-table-row" data-user-id="${user.id}"${isCurrentUser ? ' data-current-user="true"' : ''}>
            <td class="user-identity-cell" data-label="User">
                <div class="user-identity">
                    <div class="user-table-avatar">${avatarContent}</div>
                    <div class="user-identity-copy">
                        <div class="user-display-name" title="${escapeHtml(baseName)}">${escapeHtml(baseName)}${isCurrentUser ? '<span class="user-you-badge">You</span>' : ''}</div>
                        <div class="user-email" title="${escapeHtml(user.email || '')}">${escapeHtml(user.email || 'No email')}</div>
                    </div>
                </div>
            </td>
            <td class="user-auth-cell" data-label="Authentication"><span class="user-auth-badge ${authClass}">${authLabel}</span></td>
            <td class="user-role-cell" data-label="Job role">${roleHtml}</td>
            <td class="user-access-cell" data-label="System access">${accessHtml}</td>
            <td class="user-status-cell" data-label="Status"><span class="status-pill ${statusClass}">${statusLabel}</span></td>
            <td class="user-joined-cell" data-label="Joined">${joinedAt}</td>
            <td class="user-actions-cell" data-label="Actions">${actionsHtml}</td>
        </tr>`;
    }

    html += `</tbody></table></div>
        <div class="user-table-pagination">
            <div class="user-pagination-summary">Page ${visiblePage} of ${visibleTotalPages}<span>•</span>${filtered.length} user${filtered.length === 1 ? '' : 's'}</div>
            <div class="user-pagination-controls">
                <button type="button" onclick="usersPage--;renderUsersTable()" ${usersPage <= 0 ? 'disabled' : ''}>Previous</button>
                <button type="button" onclick="usersPage++;renderUsersTable()" ${usersPage >= totalPages - 1 ? 'disabled' : ''}>Next</button>
            </div>
        </div>`;

    card.innerHTML = html;

    if (focusActive) {
        const newSearchInput = card.querySelector('.user-table-search');
        if (newSearchInput) {
            newSearchInput.focus();
            newSearchInput.setSelectionRange(cursorStart, cursorEnd);
        }
    }
}

async function approveUserAction(userId) {
    const select = document.getElementById(`role-${userId}`);
    const role = select ? select.value : '';
    const systemRoleSelect = document.getElementById(`system-role-${userId}`);
    const systemRole = systemRoleSelect ? systemRoleSelect.value : 'member';

    if (!role) {
        showToast('Please select a role before approving', 'warning');
        const trigger = document.querySelector(`#role-control-${userId} .shadcn-select-trigger`);
        if (trigger) {
            trigger.classList.add('role-select-error');
            trigger.focus();
            setTimeout(() => trigger.classList.remove('role-select-error'), 2000);
        }
        return;
    }

    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/approve`, {
            method: 'POST',
            headers: {
                ...(bootstrapMode ? bootstrapHeaders() : adminHeaders()),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ role, system_role: systemRole }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Approval failed');
        await loadUsers();
        await checkUserPendingCount();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Approving user...',
            success: `User approved as ${role}.`,
            error: error => `Could not approve user: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

async function rejectUserAction(userId) {
    if (!confirm('Are you sure you want to reject/revoke this user?')) return;
    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/reject`, {
            method: 'POST',
            headers: adminHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Rejection failed');
        await loadUsers();
        await checkUserPendingCount();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Rejecting user...',
            success: { type: 'warning', description: 'User rejected.' },
            error: error => `Could not reject user: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

function enableUserEdit(userId) {
    if (editingUserId && editingUserId !== userId) cancelUserEdit(editingUserId);
    closeAllShadcnSelects();

    let firstEditableTrigger = null;
    for (const field of ['role', 'access']) {
        const ids = userSelectIds(field, userId);
        const control = document.getElementById(ids.control);
        const display = document.getElementById(ids.display);
        const trigger = control ? control.querySelector('.shadcn-select-trigger') : null;
        if (!control || !trigger || trigger.disabled) continue;
        if (display) display.style.display = 'none';
        control.style.display = 'block';
        if (!firstEditableTrigger) firstEditableTrigger = trigger;
    }

    if (!firstEditableTrigger) return;
    editingUserId = userId;
    firstEditableTrigger.focus();

    document.querySelectorAll('.dropdown-menu-content.dropdown-menu-open').forEach(dd => {
        dd.classList.remove('dropdown-menu-open');
    });
}

function cancelUserEdit(userId) {
    for (const field of ['role', 'access']) {
        const ids = userSelectIds(field, userId);
        const control = document.getElementById(ids.control);
        const input = document.getElementById(ids.input);
        const display = document.getElementById(ids.display);
        if (!control || !input) continue;

        closeShadcnSelect(ids.control);
        const originalValue = field === 'role'
            ? input.dataset.originalRole || ''
            : input.dataset.originalSystemRole || 'member';
        setShadcnSelectValue(field, userId, originalValue);
        control.style.display = 'none';
        if (display) display.style.display = '';
    }
    if (editingUserId === userId) editingUserId = null;
}

function openRevokeUserModal(userId, trigger = null) {
    const user = allUsersData.find(item => item.id === userId);
    if (!user || (currentAdminUser && currentAdminUser.id === userId)) return;

    const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || user.email || 'this user';
    const modal = document.getElementById('revokeUserModal');
    const input = document.getElementById('revokeUserConfirmInput');
    const error = document.getElementById('revokeUserConfirmError');
    const confirmButton = document.getElementById('revokeUserConfirmBtn');

    revokeDialogTrigger = trigger instanceof HTMLElement ? trigger : document.activeElement;
    modal.dataset.userId = userId;
    document.getElementById('revokeUserConfirmText').textContent =
        `Revoking ${fullName}'s access will prevent them from signing in until an administrator re-approves the account.`;
    input.value = '';
    error.textContent = '';
    confirmButton.disabled = true;
    confirmButton.textContent = 'Revoke access';
    modal.classList.add('active');
    requestAnimationFrame(() => input.focus());
}

function validateRevokeUserConfirmation() {
    const input = document.getElementById('revokeUserConfirmInput');
    const confirmButton = document.getElementById('revokeUserConfirmBtn');
    const error = document.getElementById('revokeUserConfirmError');
    const isConfirmed = input.value.trim() === 'Confirm';
    confirmButton.disabled = !isConfirmed;
    if (isConfirmed) error.textContent = '';
}

function closeRevokeUserModal() {
    const modal = document.getElementById('revokeUserModal');
    modal.classList.remove('active');
    delete modal.dataset.userId;
    document.getElementById('revokeUserConfirmInput').value = '';
    document.getElementById('revokeUserConfirmError').textContent = '';
    document.getElementById('revokeUserConfirmBtn').disabled = true;
    if (revokeDialogTrigger && revokeDialogTrigger.isConnected) revokeDialogTrigger.focus();
    revokeDialogTrigger = null;
}

async function confirmRevokeUserAction() {
    const modal = document.getElementById('revokeUserModal');
    const input = document.getElementById('revokeUserConfirmInput');
    const error = document.getElementById('revokeUserConfirmError');
    const confirmButton = document.getElementById('revokeUserConfirmBtn');
    const userId = modal.dataset.userId;

    if (!userId || input.value.trim() !== 'Confirm') {
        error.textContent = 'Type Confirm exactly to revoke this user.';
        input.focus();
        return;
    }

    confirmButton.disabled = true;
    confirmButton.textContent = 'Revoking...';
    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/reject`, {
            method: 'POST',
            headers: adminHeaders(),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Revocation failed. Please try again.');
        await loadUsers();
        await checkUserPendingCount();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Revoking user access...',
            success: { type: 'warning', description: 'User access revoked.' },
            error: error => `Could not revoke access: ${error.message}`,
        });
        closeRevokeUserModal();
    } catch (e) {
        error.textContent = e.message;
        confirmButton.disabled = false;
        confirmButton.textContent = 'Revoke access';
    }
}

function confirmRoleChange(userId, newRole) {
    const select = document.getElementById(`role-${userId}`);
    const oldRole = select ? select.getAttribute('data-original-role') : '';
    if (newRole === oldRole) {
        cancelUserEdit(userId);
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
        cancelUserEdit(userId);
        modal.removeAttribute('data-cancel-userid');
    }
}

async function changeRoleAction(userId, explicitRole) {
    const select = document.getElementById(`role-${userId}`);
    const role = explicitRole || (select ? select.value : 'Associate');
    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/role`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ role }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Update failed');
        await loadUsers();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Updating job role...',
            success: `Role updated to ${role}.`,
            error: error => `Could not update role: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

function systemRoleLabel(systemRole) {
    const match = SYSTEM_ROLES.find(([value]) => value === systemRole);
    return match ? match[1] : systemRole.replaceAll('_', ' ');
}

function confirmSystemRoleChange(userId, newSystemRole) {
    const user = allUsersData.find(item => item.id === userId);
    const select = document.getElementById(`system-role-${userId}`);
    if (!user || !select) return;

    const oldSystemRole = select.dataset.originalSystemRole || user.system_role || 'member';
    if (newSystemRole === oldSystemRole) {
        cancelUserEdit(userId);
        return;
    }

    const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || user.email;
    document.getElementById('accessConfirmText').textContent =
        `Change ${fullName}'s access from ${systemRoleLabel(oldSystemRole)} to ${systemRoleLabel(newSystemRole)}?`;

    const modal = document.getElementById('accessConfirmModal');
    modal.dataset.userId = userId;
    modal.dataset.oldSystemRole = oldSystemRole;
    modal.dataset.newSystemRole = newSystemRole;
    modal.classList.add('active');

    const confirmButton = document.getElementById('accessConfirmBtn');
    confirmButton.disabled = false;
    confirmButton.textContent = 'Change Access';
    confirmButton.onclick = confirmSystemRoleChangeAction;
}

function closeAccessConfirmModal(revertSelection = true) {
    const modal = document.getElementById('accessConfirmModal');
    const userId = modal.dataset.userId;
    if (revertSelection && userId) {
        setShadcnSelectValue('access', userId, modal.dataset.oldSystemRole || 'member');
    }
    modal.classList.remove('active');
    delete modal.dataset.userId;
    delete modal.dataset.oldSystemRole;
    delete modal.dataset.newSystemRole;
    if (userId) cancelUserEdit(userId);
}

async function confirmSystemRoleChangeAction() {
    const modal = document.getElementById('accessConfirmModal');
    const userId = modal.dataset.userId;
    const systemRole = modal.dataset.newSystemRole;
    if (!userId || !systemRole) return;

    const confirmButton = document.getElementById('accessConfirmBtn');
    confirmButton.disabled = true;
    confirmButton.textContent = 'Changing...';
    closeAccessConfirmModal(false);
    await changeSystemRoleAction(userId, systemRole);
}

async function changeSystemRoleAction(userId, systemRole) {
    const user = allUsersData.find(item => item.id === userId);
    const jobRole = user ? (user.job_title || user.role || 'Associate') : 'Associate';
    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/role`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: jobRole, system_role: systemRole }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Access update failed');
        await loadUsers();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Updating system access...',
            success: `Access updated to ${systemRoleLabel(systemRole)}.`,
            error: error => `Could not update access: ${error.message}`,
        });
    } catch (_) {
        await loadUsers();
    }
}

async function claimAdministratorAccess() {
    const token = localStorage.getItem('grasp_session_token');
    if (!token) {
        showToast('Sign in first, then return to the admin panel.', 'warning');
        return;
    }

    const operation = (async () => {
        const response = await fetch(`${API_BASE}/api/admin/bootstrap/claim`, {
            method: 'POST',
            headers: bootstrapHeaders(),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Administrator claim failed');
        currentAdminUser = data.user;
        localStorage.setItem('grasp_user', JSON.stringify(data.user));
        showAdminDashboard(false);
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Claiming administrator access...',
            success: 'Administrator access granted.',
            error: error => `Could not claim administrator access: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
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

    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/contributions/${currentContributionId}`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, content }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Save failed');
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Saving contribution edits...',
            success: 'Contribution edits saved.',
            error: error => `Could not save edits: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

async function approveContribution() {
    if (!currentContributionId) return;
    const title = document.getElementById('reviewTitle').value.trim();
    const content = document.getElementById('reviewContent').value;
    const adminNotes = document.getElementById('contributionAdminNotes').value;
    const contributionId = currentContributionId;
    const operation = (async () => {
        if (title && content) {
            const saveResponse = await fetch(`${API_BASE}/api/contributions/${contributionId}`, {
                method: 'PUT',
                headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, content }),
            });
            const saveData = await saveResponse.json().catch(() => ({}));
            if (!saveResponse.ok) throw new Error(saveData.detail || 'Could not save contribution edits');
        }

        const res = await fetch(`${API_BASE}/api/contributions/${contributionId}/approve`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ admin_notes: adminNotes }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'approved') {
            throw new Error(data.detail || data.message || 'Approval failed');
        }
        closeContributionReview();
        await loadContributions();
        await Promise.all([checkContributionCount(), checkPendingChanges()]);
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Approving and activating contribution...',
            success: data => `Approved and activated as "${data.info_type}".`,
            error: error => `Could not approve contribution: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
}

async function rejectContribution() {
    if (!currentContributionId) return;
    if (!confirm('Are you sure you want to reject this contribution?')) return;

    const adminNotes = document.getElementById('contributionAdminNotes').value;
    const contributionId = currentContributionId;
    const operation = (async () => {
        const res = await fetch(`${API_BASE}/api/contributions/${contributionId}/reject`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ admin_notes: adminNotes }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.status !== 'rejected') {
            throw new Error(data.detail || data.message || 'Rejection failed');
        }
        closeContributionReview();
        await loadContributions();
        await checkContributionCount();
        return data;
    })();

    try {
        await toast.promise(operation, {
            loading: 'Rejecting contribution...',
            success: { type: 'warning', description: 'Contribution rejected.' },
            error: error => `Could not reject contribution: ${error.message}`,
        });
    } catch (_) { /* The promise toast presents the error. */ }
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
