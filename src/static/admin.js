const API_BASE = '';

// Unicode-safe base64 encoding (btoa only handles Latin1)
function unicodeBtoa(str) {
    const bytes = new TextEncoder().encode(str);
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary);
}

function formatEntityName(name) {
    if (!name) return name;
    return name.charAt(0).toUpperCase() + name.slice(1);
}

let adminKey = sessionStorage.getItem('grasp_admin_key') || '';
let bootstrapMode = false;
let adminIntervalsStarted = false;
let currentAdminUser = null;
let allAgentsData = [];
let allAgentRuns = [];
let agentOwners = [];
let agentControlState = { enabled: false, emergency_stopped: false, reason: '' };
let auditEventsPage = 0;
let auditEventsTotal = 0;
let auditEventsLimit = 25;

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
    if (typeof renderGraph === 'function') {
        renderGraph();
    }
}

function updateThemeIcon() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const menuIcon = document.getElementById('themeMenuIcon');
    const menuLabel = document.getElementById('themeMenuLabel');
    if (menuIcon) {
        menuIcon.classList.toggle('theme-icon-sun', isLight);
        menuIcon.classList.toggle('theme-icon-moon', !isLight);
    }
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
    document.getElementById('adminGateTitle').textContent = 'Operations access required';
    document.getElementById('adminGateMessage').textContent =
        'Your account is signed in, but it does not have Operator or Administrator access.';
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
    configureAdminNavigation(user);

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
    document.getElementById('navAgents').style.display = isBootstrap ? 'none' : '';
    document.getElementById('navAudit').style.display = isBootstrap ? 'none' : '';
    document.getElementById('navObservability').style.display = isBootstrap ? 'none' : '';
    document.getElementById('syncBtn').style.display = isBootstrap ? 'none' : '';

    if (isBootstrap) {
        showAdminScreen('Users');
        return;
    }

    refreshStatus();
    checkPendingChanges();
    checkContributionCount();

    // Default to Home screen, or the Audit/Observability page when reached directly.
    if (location.pathname === '/admin/observability') showAdminScreen('Observability');
    else if (location.pathname === '/admin/audit' || location.pathname === '/admin/operations') showAdminScreen('Audit');
    else showAdminScreen('Home');

    if (!adminIntervalsStarted) {
        adminIntervalsStarted = true;
        setInterval(refreshStatus, 15000);
        setInterval(checkPendingChanges, 15000);
        setInterval(checkContributionCount, 15000);
        setInterval(() => {
            if (currentAdminUser?.system_role === 'administrator') checkUserPendingCount();
        }, 15000);
        setInterval(() => {
            if (['operator', 'administrator'].includes(currentAdminUser?.system_role)) loadAgents();
        }, 15000);
    }
}

function configureAdminNavigation(user) {
    if (bootstrapMode) return;
    const role = user?.system_role || 'member';
    const canManageUsers = role === 'administrator';
    const canManageAgents = role === 'operator' || role === 'administrator';
    const canViewAudit = role === 'operator' || role === 'administrator';
    document.getElementById('navUsers').style.display = canManageUsers ? '' : 'none';
    document.getElementById('navAgents').style.display = canManageAgents ? '' : 'none';
    document.getElementById('navAudit').style.display = canViewAudit ? '' : 'none';
    document.getElementById('navObservability').style.display = canViewAudit ? '' : 'none';
    if (canManageUsers) checkUserPendingCount();

    // Show Memory tab if feature is enabled (checked async)
    checkMemoryEnabled();

    const usersScreen = document.getElementById('screenUsers');
    const agentsScreen = document.getElementById('screenAgents');
    const memoryScreen = document.getElementById('screenMemory');
    if (!canManageUsers && usersScreen?.style.display !== 'none') showAdminScreen('Home');
    if (!canManageAgents && agentsScreen?.style.display !== 'none') showAdminScreen('Home');
}

// Screen routing

function showAdminScreen(screenName) {
    // Hide all screens
    document.querySelectorAll('.admin-screen').forEach(el => el.style.display = 'none');
    // Remove active class from all nav items
    document.querySelectorAll('.admin-sidebar-button').forEach(el => el.classList.remove('active'));

    const titleEl = document.getElementById('adminScreenTitle');

    // Keep the URL aligned with the Audit / Observability endpoints.
    if (screenName === 'Audit') {
        history.replaceState(null, '', '/admin/audit');
    } else if (screenName === 'Observability') {
        history.replaceState(null, '', '/admin/observability');
    } else if (location.pathname === '/admin/operations' || location.pathname === '/admin/audit' || location.pathname === '/admin/observability') {
        history.replaceState(null, '', '/admin');
    }

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
    } else if (screenName === 'Agents') {
        document.getElementById('screenAgents').style.display = 'block';
        document.getElementById('navAgents').classList.add('active');
        titleEl.textContent = 'Company-brain Agents';
        loadAgents();
    } else if (screenName === 'Memory') {
        document.getElementById('screenMemory').style.display = 'block';
        document.getElementById('navMemory').classList.add('active');
        titleEl.textContent = 'Structured Memory';
        loadMemoryStats();
        loadEntities();
        loadWorkItems();
    } else if (screenName === 'Audit') {
        document.getElementById('screenAudit').style.display = 'block';
        document.getElementById('navAudit').classList.add('active');
        titleEl.textContent = 'Audit Log';
        loadAuditSummary();
        loadAuditEvents();
    } else if (screenName === 'Observability') {
        document.getElementById('screenObservability').style.display = 'block';
        document.getElementById('navObservability').classList.add('active');
        titleEl.textContent = 'Observability';
        loadObservability();
    }
}

// Audit log querying

function currentAuditFilters() {
    const value = id => (document.getElementById(id)?.value || '').trim();
    const start = document.getElementById('auditStart')?.value;
    const end = document.getElementById('auditEnd')?.value;
    return {
        event_type: value('auditEventType') || null,
        actor: value('auditActor') || null,
        resource_type: value('auditResourceType') || null,
        resource_id: value('auditResourceId') || null,
        start: start ? `${start}T00:00:00` : null,
        end: end ? `${end}T23:59:59` : null,
    };
}

function resetAuditFilters() {
    ['auditEventType', 'auditActor', 'auditResourceType', 'auditResourceId', 'auditStart', 'auditEnd'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    // Reset the custom date pickers back to their placeholder state.
    Object.keys(_auditDatePickers).forEach(key => {
        const cfg = _auditDatePickers[key];
        if (!cfg) return;
        cfg.state.selected = null;
        const display = auditDateEl(key, 'display');
        if (display) {
            display.textContent = cfg.placeholder;
            display.classList.add('date-picker-placeholder');
        }
    });
    loadAuditEvents(true);
}

// --- Audit date pickers (same custom calendar as the register Date of Birth picker) ---

const AUDIT_DATE_MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
];

const _auditDatePickers = {
    start: {
        hidden: 'auditStart',
        display: 'auditStartDisplay',
        popover: 'auditStartPopover',
        monthSelect: 'auditStartMonthSelect',
        yearSelect: 'auditStartYearSelect',
        days: 'auditStartCalendarDays',
        wrapper: 'auditStartPicker',
        placeholder: 'From date',
        state: { viewMonth: 0, viewYear: 2000, selected: null },
    },
    end: {
        hidden: 'auditEnd',
        display: 'auditEndDisplay',
        popover: 'auditEndPopover',
        monthSelect: 'auditEndMonthSelect',
        yearSelect: 'auditEndYearSelect',
        days: 'auditEndCalendarDays',
        wrapper: 'auditEndPicker',
        placeholder: 'To date',
        state: { viewMonth: 0, viewYear: 2000, selected: null },
    },
};

function auditDateEl(key, field) {
    const cfg = _auditDatePickers[key];
    return cfg ? document.getElementById(cfg[field]) : null;
}

function initAuditDatePicker(key) {
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    const now = new Date();
    const hidden = auditDateEl(key, 'hidden');
    cfg.state.selected = null;
    if (hidden && hidden.value) {
        const m = hidden.value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (m) {
            cfg.state.selected = { day: parseInt(m[3], 10), month: parseInt(m[2], 10), year: parseInt(m[1], 10) };
            cfg.state.viewMonth = parseInt(m[2], 10) - 1;
            cfg.state.viewYear = parseInt(m[1], 10);
        }
    }
    if (!cfg.state.selected) {
        cfg.state.viewMonth = now.getMonth();
        cfg.state.viewYear = now.getFullYear();
    }
    populateAuditDateSelects(key);
    renderAuditDateCalendar(key);
}

function populateAuditDateSelects(key) {
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    const monthSelect = auditDateEl(key, 'monthSelect');
    const yearSelect = auditDateEl(key, 'yearSelect');
    if (!monthSelect || !yearSelect) return;

    monthSelect.innerHTML = AUDIT_DATE_MONTHS.map((m, i) =>
        `<option value="${i}" ${i === cfg.state.viewMonth ? 'selected' : ''}>${m}</option>`
    ).join('');

    const currentYear = new Date().getFullYear();
    let yearOpts = '';
    for (let y = currentYear; y >= 1920; y--) {
        yearOpts += `<option value="${y}" ${y === cfg.state.viewYear ? 'selected' : ''}>${y}</option>`;
    }
    yearSelect.innerHTML = yearOpts;
}

function renderAuditDateCalendar(key) {
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    const container = auditDateEl(key, 'days');
    if (!container) return;

    const today = new Date();
    const firstDay = new Date(cfg.state.viewYear, cfg.state.viewMonth, 1).getDay(); // 0=Sun
    const daysInMonth = new Date(cfg.state.viewYear, cfg.state.viewMonth + 1, 0).getDate();

    let html = '';
    for (let i = 0; i < firstDay; i++) {
        html += '<button type="button" class="date-picker-day date-picker-day-empty" disabled></button>';
    }

    for (let d = 1; d <= daysInMonth; d++) {
        const isToday = d === today.getDate() && cfg.state.viewMonth === today.getMonth() && cfg.state.viewYear === today.getFullYear();
        const sel = cfg.state.selected;
        const isSelected = sel && d === sel.day && cfg.state.viewMonth === (sel.month - 1) && cfg.state.viewYear === sel.year;
        const isFuture = new Date(cfg.state.viewYear, cfg.state.viewMonth, d) > today;

        let cls = 'date-picker-day';
        if (isToday) cls += ' date-picker-day-today';
        if (isSelected) cls += ' date-picker-day-selected';
        if (isFuture) cls += ' date-picker-day-disabled';

        html += `<button type="button" class="${cls}" ${isFuture ? 'disabled' : ''}
            onclick="selectAuditDate('${key}', ${d}, event)">${d}</button>`;
    }

    container.innerHTML = html;

    const monthSelect = auditDateEl(key, 'monthSelect');
    const yearSelect = auditDateEl(key, 'yearSelect');
    if (monthSelect) monthSelect.value = cfg.state.viewMonth;
    if (yearSelect) yearSelect.value = cfg.state.viewYear;
}

function selectAuditDate(key, day, event) {
    if (event) event.preventDefault();
    const cfg = _auditDatePickers[key];
    if (!cfg) return;

    cfg.state.selected = {
        day,
        month: cfg.state.viewMonth + 1,
        year: cfg.state.viewYear,
        iso: `${cfg.state.viewYear}-${String(cfg.state.viewMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    };

    const hidden = auditDateEl(key, 'hidden');
    if (hidden) hidden.value = cfg.state.selected.iso;

    const display = auditDateEl(key, 'display');
    if (display) {
        display.textContent = `${String(day).padStart(2, '0')} / ${String(cfg.state.selected.month).padStart(2, '0')} / ${cfg.state.viewYear}`;
        display.classList.remove('date-picker-placeholder');
    }

    closeAuditDatePicker(key);
}

function toggleAuditDatePicker(key, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    const popover = auditDateEl(key, 'popover');
    if (!popover) return;

    const isOpen = popover.classList.contains('date-picker-open');
    Object.keys(_auditDatePickers).forEach(k => closeAuditDatePicker(k));
    if (!isOpen) {
        initAuditDatePicker(key);
        popover.classList.add('date-picker-open');
    }
}

function closeAuditDatePicker(key) {
    const popover = auditDateEl(key, 'popover');
    if (popover) popover.classList.remove('date-picker-open');
}

function auditDateNavMonth(key, delta, event) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    cfg.state.viewMonth += delta;
    if (cfg.state.viewMonth < 0) { cfg.state.viewMonth = 11; cfg.state.viewYear--; }
    if (cfg.state.viewMonth > 11) { cfg.state.viewMonth = 0; cfg.state.viewYear++; }
    renderAuditDateCalendar(key);
}

function auditDateChangeMonth(key, event) {
    if (event) event.stopPropagation();
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    cfg.state.viewMonth = parseInt(event.target.value, 10);
    renderAuditDateCalendar(key);
}

function auditDateChangeYear(key, event) {
    if (event) event.stopPropagation();
    const cfg = _auditDatePickers[key];
    if (!cfg) return;
    cfg.state.viewYear = parseInt(event.target.value, 10);
    renderAuditDateCalendar(key);
}

// Close audit date pickers when clicking outside them
document.addEventListener('click', (e) => {
    Object.keys(_auditDatePickers).forEach(key => {
        const wrapper = auditDateEl(key, 'wrapper');
        if (wrapper && !wrapper.contains(e.target)) closeAuditDatePicker(key);
    });
});

function formatAuditTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    return isNaN(date.getTime()) ? escapeHtml(String(value)) : escapeHtml(date.toLocaleString());
}

function formatMetric(value) {
    if (typeof value !== 'number') return '—';
    return value.toLocaleString(undefined, { maximumFractionDigits: Math.abs(value) >= 100 ? 1 : 3 });
}

// --- Cached audit summary data for rendering across multiple containers ---
let _lastAuditSummary = null;

async function loadAuditSummary() {
    const statCards = document.getElementById('auditStatCards');
    const activityChart = document.getElementById('auditActivityChart');
    const typeBreakdown = document.getElementById('auditTypeBreakdown');
    const loading = '<div class="admin-dashboard-loading-item" style="border:none;padding:0"><span class="admin-dashboard-loading-media"></span><span class="admin-dashboard-loading-copy"></span></div>';
    if (statCards) statCards.innerHTML = `<div class="ops-stat-card">${loading}</div>`;
    if (activityChart) activityChart.innerHTML = `<div class="audit-activity-chart">${loading}</div>`;
    if (typeBreakdown) typeBreakdown.innerHTML = `<div class="audit-type-pills">${loading}</div>`;
    try {
        const res = await fetch(`${API_BASE}/api/admin/audit-events/summary?days=30`, { headers: adminHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        _lastAuditSummary = data;
        renderAuditStatCards(data);
        renderAuditActivityChart(data);
        renderAuditTypeBreakdown(data);
    } catch (err) {
        const errHtml = `<div class="data-table-empty">Failed to load audit summary: ${escapeHtml(err.message)}</div>`;
        if (statCards) statCards.innerHTML = errHtml;
        if (activityChart) activityChart.innerHTML = errHtml;
        if (typeBreakdown) typeBreakdown.innerHTML = errHtml;
    }
}

function renderAuditStatCards(data) {
    const container = document.getElementById('auditStatCards');
    if (!container) return;
    const total = Number(data.total || 0);
    const typeEntries = Object.entries(data.by_event_type || {});
    const typeCount = typeEntries.length;
    const topType = typeEntries.length ? typeEntries[0] : null;
    const byDay = data.by_day || [];
    const lastDay = byDay.length ? byDay[byDay.length - 1] : null;

    container.innerHTML = `
        <div class="ops-stat-card">
            <div class="ops-stat-icon blue">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Total events</span>
                <span class="ops-stat-value">${total.toLocaleString()}</span>
                <span class="ops-stat-sub">Last ${data.days ?? 30} days</span>
            </div>
        </div>
        <div class="ops-stat-card">
            <div class="ops-stat-icon emerald">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Event types</span>
                <span class="ops-stat-value">${typeCount}</span>
                <span class="ops-stat-sub">Unique categories</span>
            </div>
        </div>
        <div class="ops-stat-card">
            <div class="ops-stat-icon amber">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Most active</span>
                <span class="ops-stat-value">${topType ? Number(topType[1]).toLocaleString() : '—'}</span>
                <span class="ops-stat-sub">${topType ? escapeHtml(topType[0]) : 'No events'}</span>
            </div>
        </div>
        <div class="ops-stat-card">
            <div class="ops-stat-icon violet">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Latest activity</span>
                <span class="ops-stat-value">${lastDay ? Number(lastDay.count).toLocaleString() : '0'}</span>
                <span class="ops-stat-sub">${lastDay ? escapeHtml(lastDay.date) : 'No recent activity'}</span>
            </div>
        </div>`;
}

function renderAuditActivityChart(data) {
    const container = document.getElementById('auditActivityChart');
    if (!container) return;
    const byDay = data.by_day || [];
    if (!byDay.length) {
        container.innerHTML = '<div class="ops-empty-state"><div class="ops-empty-icon">📊</div><div class="ops-empty-title">No activity data</div><div class="ops-empty-desc">Events will appear here once audit events are recorded.</div></div>';
        return;
    }
    const maxCount = Math.max(...byDay.map(d => d.count), 1);
    const bars = byDay.map(d => {
        const height = Math.max(4, (d.count / maxCount) * 100);
        return `<div class="audit-activity-bar" style="height:${height}%" data-tooltip="${escapeHtml(d.date)}: ${d.count} events"></div>`;
    }).join('');
    container.innerHTML = `<div class="audit-activity-chart">${bars}</div>`;
}

function renderAuditTypeBreakdown(data) {
    const container = document.getElementById('auditTypeBreakdown');
    if (!container) return;
    const typeEntries = Object.entries(data.by_event_type || {});
    if (!typeEntries.length) {
        container.innerHTML = '<p style="color:var(--text-tertiary);font-size:12px">No event types recorded in this period.</p>';
        return;
    }
    const pills = typeEntries.map(([name, count]) => {
        return `<div class="audit-type-pill">
            <span class="audit-type-pill-count">${Number(count).toLocaleString()}</span>
            <span class="audit-type-pill-name">${escapeHtml(name)}</span>
        </div>`;
    }).join('');
    container.innerHTML = `<div class="audit-type-pills">${pills}</div>`;
}

function getAuditBadgeClass(eventType) {
    if (!eventType) return '';
    const t = eventType.toLowerCase();
    if (t.startsWith('auth') || t.includes('login') || t.includes('logout')) return 'auth';
    if (t.startsWith('agent') || t.includes('agent')) return 'agent';
    if (t.startsWith('user') || t.includes('user')) return 'user';
    if (t.startsWith('sync') || t.includes('sync')) return 'sync';
    if (t.startsWith('admin') || t.includes('admin')) return 'admin';
    if (t.includes('memory') || t.includes('entity')) return 'memory';
    if (t.includes('contrib')) return 'contrib';
    return '';
}

function formatRelativeTime(dateStr) {
    if (!dateStr) return '—';
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return escapeHtml(String(dateStr));
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    if (diffDay < 30) return `${diffDay}d ago`;
    return date.toLocaleDateString();
}

async function loadAuditEvents(resetPage = false) {
    const container = document.getElementById('auditEventsCard');
    if (!container) return;
    if (resetPage) auditEventsPage = 0;
    const params = new URLSearchParams({
        limit: String(auditEventsLimit),
        offset: String(auditEventsPage * auditEventsLimit),
    });
    for (const [key, value] of Object.entries(currentAuditFilters())) {
        if (value) params.set(key, value);
    }
    container.innerHTML = '<div class="admin-dashboard-loading-item"><span class="admin-dashboard-loading-media"></span><span class="admin-dashboard-loading-copy"></span></div>';
    try {
        const res = await fetch(`${API_BASE}/api/admin/audit-events?${params}`, { headers: adminHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        auditEventsTotal = data.total || 0;
        renderAuditEvents(data.events || []);
    } catch (err) {
        container.innerHTML = `<div class="data-table-empty">Failed to load audit events: ${escapeHtml(err.message)}</div>`;
    }
}

function renderAuditEvents(events) {
    const container = document.getElementById('auditEventsCard');
    if (!container) return;
    if (!events.length) {
        container.innerHTML = '<div class="ops-empty-state"><div class="ops-empty-icon">🔍</div><div class="ops-empty-title">No events found</div><div class="ops-empty-desc">No audit events match the current filters. Try adjusting your search criteria.</div></div>';
        return;
    }
    const rows = events.map((ev, idx) => {
        const details = ev.details && typeof ev.details === 'object'
            ? JSON.stringify(ev.details, null, 2)
            : String(ev.details || '');
        const badgeClass = getAuditBadgeClass(ev.event_type);
        const relTime = formatRelativeTime(ev.created_at);
        const fullTime = ev.created_at ? new Date(ev.created_at).toLocaleString() : '';
        return `<tr class="audit-detail-toggle" onclick="toggleAuditDetail(${idx})">
            <td class="cell-primary" title="${escapeHtml(fullTime)}">${escapeHtml(relTime)}</td>
            <td><span class="audit-event-badge ${badgeClass}">${escapeHtml(ev.event_type || '')}</span></td>
            <td>${escapeHtml(ev.actor_user_id || 'system')}</td>
            <td>${escapeHtml(ev.resource_type || '')}${ev.resource_id ? ` <span class="type-badge">${escapeHtml(String(ev.resource_id).slice(0, 16))}</span>` : ''}</td>
            <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(details)}">
                ${details.length > 2 ? '⋯' : '—'}
            </td>
        </tr>
        <tr class="audit-detail-expand" id="auditDetail${idx}">
            <td colspan="5">
                <div class="audit-detail-json">${escapeHtml(details || 'No details')}</div>
            </td>
        </tr>`;
    }).join('');
    const totalPages = Math.max(1, Math.ceil(auditEventsTotal / auditEventsLimit));
    container.innerHTML = `<table class="data-table">
        <thead>
            <tr><th>Time</th><th>Event type</th><th>Actor</th><th>Resource</th><th>Details</th></tr>
        </thead>
        <tbody>${rows}</tbody>
    </table>
    <div class="data-table-pagination">
        <div class="data-table-pagination-info">${auditEventsTotal.toLocaleString()} event(s) — page ${auditEventsPage + 1} of ${totalPages}</div>
        <div class="data-table-pagination-controls">
            <button class="data-table-pagination-btn" onclick="auditEventsPage--;loadAuditEvents()" ${auditEventsPage <= 0 ? 'disabled' : ''}>Previous</button>
            <button class="data-table-pagination-btn" onclick="auditEventsPage++;loadAuditEvents()" ${auditEventsPage >= totalPages - 1 ? 'disabled' : ''}>Next</button>
        </div>
    </div>`;
}

function toggleAuditDetail(idx) {
    const row = document.getElementById(`auditDetail${idx}`);
    if (row) row.classList.toggle('open');
}

function exportAuditCSV() {
    const events = document.querySelectorAll('#auditEventsCard .audit-detail-toggle');
    if (!events.length) {
        if (typeof showToast === 'function') showToast('No events to export', 'warning');
        return;
    }
    let csv = 'Time,Event Type,Actor,Resource,Details\n';
    events.forEach((row, idx) => {
        const cells = row.querySelectorAll('td');
        const detailRow = document.getElementById(`auditDetail${idx}`);
        const detailJson = detailRow?.querySelector('.audit-detail-json')?.textContent || '';
        const values = [
            cells[0]?.getAttribute('title') || cells[0]?.textContent?.trim() || '',
            cells[1]?.textContent?.trim() || '',
            cells[2]?.textContent?.trim() || '',
            cells[3]?.textContent?.trim() || '',
            detailJson.replace(/"/g, '""'),
        ];
        csv += values.map(v => `"${v}"`).join(',') + '\n';
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-events-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    if (typeof showToast === 'function') showToast('Audit events exported', 'success');
}

// Observability

let _lastObsData = null;

async function loadObservability() {
    const statCards = document.getElementById('obsStatCards');
    const metricCards = document.getElementById('obsMetricCards');
    const tableContainer = document.getElementById('obsTableContainer');
    const loading = '<div class="admin-dashboard-loading-item" style="border:none;padding:0"><span class="admin-dashboard-loading-media"></span><span class="admin-dashboard-loading-copy"></span></div>';
    if (statCards) statCards.innerHTML = `<div class="ops-stat-card">${loading}</div>`;
    if (metricCards) metricCards.innerHTML = `<div class="obs-metric-card">${loading}</div>`;
    if (tableContainer) tableContainer.innerHTML = loading;
    try {
        const res = await fetch(`${API_BASE}/api/admin/observability`, { headers: adminHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        _lastObsData = data;
        renderObsStatCards(data);
        renderObsMetricCards(data);
        renderObsTable(data);
    } catch (err) {
        const errHtml = `<div class="data-table-empty">Failed to load observability data: ${escapeHtml(err.message)}</div>`;
        if (statCards) statCards.innerHTML = errHtml;
        if (metricCards) metricCards.innerHTML = errHtml;
        if (tableContainer) tableContainer.innerHTML = errHtml;
    }
}

function getMetricHealth(m) {
    // Simple heuristic: if p95 is more than 5x p50, it's a warning; 10x is bad
    if (!m || m.count === 0) return 'good';
    if (m.p50 > 0 && m.p95 / m.p50 > 10) return 'bad';
    if (m.p50 > 0 && m.p95 / m.p50 > 5) return 'warn';
    return 'good';
}

function renderObsStatCards(data) {
    const container = document.getElementById('obsStatCards');
    if (!container) return;
    const metrics = data.metrics || {};
    const names = Object.keys(metrics);
    const metricCount = names.length;
    const captured = data.captured_at ? formatRelativeTime(data.captured_at) : '—';
    const capturedFull = data.captured_at ? new Date(data.captured_at).toLocaleString() : '';

    // Compute aggregate p50 and find highest p95
    let totalSamples = 0;
    let p50Sum = 0;
    let highestP95 = { name: '—', value: 0 };
    for (const [name, m] of Object.entries(metrics)) {
        totalSamples += (m.count || 0);
        p50Sum += (m.p50 || 0);
        if ((m.p95 || 0) > highestP95.value) {
            highestP95 = { name, value: m.p95 };
        }
    }
    const avgP50 = metricCount > 0 ? p50Sum / metricCount : 0;

    container.innerHTML = `
        <div class="ops-stat-card">
            <div class="ops-stat-icon cyan">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Tracked metrics</span>
                <span class="ops-stat-value">${metricCount}</span>
                <span class="ops-stat-sub">${totalSamples.toLocaleString()} total samples</span>
            </div>
        </div>
        <div class="ops-stat-card">
            <div class="ops-stat-icon violet">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Captured</span>
                <span class="ops-stat-value">${escapeHtml(captured)}</span>
                <span class="ops-stat-sub" title="${escapeHtml(capturedFull)}">${escapeHtml(capturedFull)}</span>
            </div>
        </div>
        <div class="ops-stat-card">
            <div class="ops-stat-icon emerald">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Avg median (p50)</span>
                <span class="ops-stat-value">${formatMetric(avgP50)}</span>
                <span class="ops-stat-sub">Across all metrics</span>
            </div>
        </div>
        <div class="ops-stat-card">
            <div class="ops-stat-icon rose">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            </div>
            <div class="ops-stat-body">
                <span class="ops-stat-label">Highest p95</span>
                <span class="ops-stat-value">${formatMetric(highestP95.value)}</span>
                <span class="ops-stat-sub">${escapeHtml(highestP95.name)}</span>
            </div>
        </div>`;
}

function renderObsMetricCards(data) {
    const container = document.getElementById('obsMetricCards');
    if (!container) return;
    const metrics = data.metrics || {};
    const names = Object.keys(metrics);
    if (!names.length) {
        container.innerHTML = '<div class="ops-empty-state"><div class="ops-empty-icon">📈</div><div class="ops-empty-title">No metrics recorded</div><div class="ops-empty-desc">Metrics appear after agent runs, queries, or scheduler activity.</div></div>';
        return;
    }
    container.innerHTML = names.map(name => {
        const m = metrics[name];
        const health = getMetricHealth(m);
        return `<div class="obs-metric-card health-${health}">
            <div class="obs-metric-name">
                <span class="obs-metric-health-dot ${health}"></span>
                ${escapeHtml(name)}
            </div>
            <div class="obs-metric-grid">
                <div class="obs-metric-cell">
                    <span class="obs-metric-cell-label">Samples</span>
                    <span class="obs-metric-cell-value">${Number(m.count || 0).toLocaleString()}</span>
                </div>
                <div class="obs-metric-cell">
                    <span class="obs-metric-cell-label">Median</span>
                    <span class="obs-metric-cell-value">${formatMetric(m.p50)}</span>
                </div>
                <div class="obs-metric-cell">
                    <span class="obs-metric-cell-label">p95</span>
                    <span class="obs-metric-cell-value">${formatMetric(m.p95)}</span>
                </div>
                <div class="obs-metric-cell">
                    <span class="obs-metric-cell-label">Maximum</span>
                    <span class="obs-metric-cell-value">${formatMetric(m.maximum)}</span>
                </div>
            </div>
        </div>`;
    }).join('');
}

function renderObsTable(data) {
    const container = document.getElementById('obsTableContainer');
    if (!container) return;
    const metrics = data.metrics || {};
    const names = Object.keys(metrics);
    if (!names.length) {
        container.innerHTML = '<div class="data-table-empty">No metrics to display in table form.</div>';
        return;
    }
    const rows = names.map(name => {
        const m = metrics[name];
        const health = getMetricHealth(m);
        return `<tr>
            <td class="cell-primary"><span class="obs-metric-health-dot ${health}" style="display:inline-block;vertical-align:middle;margin-right:6px"></span>${escapeHtml(name)}</td>
            <td>${Number(m.count || 0).toLocaleString()}</td>
            <td>${formatMetric(m.p50)}</td>
            <td>${formatMetric(m.p95)}</td>
            <td>${formatMetric(m.maximum)}</td>
        </tr>`;
    }).join('');
    const captured = data.captured_at ? formatAuditTime(data.captured_at) : '—';
    container.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 2px 10px;color:var(--text-tertiary);font-size:13px">
        <span>Captured ${captured}</span>
        <span>${names.length} metric(s)</span>
    </div>
    <table class="data-table">
        <thead>
            <tr><th>Metric</th><th>Samples</th><th>p50</th><th>p95</th><th>Maximum</th></tr>
        </thead>
        <tbody>${rows}</tbody>
    </table>`;
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

// Company-brain agents

function agentApiError(data, fallback) {
    if (typeof data?.detail === 'string') return data.detail;
    if (Array.isArray(data?.detail) && data.detail.length) {
        return data.detail.map(item => item.msg || String(item)).join('; ');
    }
    return data?.error || fallback;
}

async function loadAgents() {
    const grid = document.getElementById('agentsGrid');
    if (!grid || !['operator', 'administrator'].includes(currentAdminUser?.system_role)) return;
    try {
        const [statusResponse, agentsResponse, runsResponse, ownersResponse] = await Promise.all([
            fetch(`${API_BASE}/api/agents/status`, { headers: adminHeaders() }),
            fetch(`${API_BASE}/api/agents`, { headers: adminHeaders() }),
            fetch(`${API_BASE}/api/agents/runs?limit=50`, { headers: adminHeaders() }),
            fetch(`${API_BASE}/api/agents/owners`, { headers: adminHeaders() }),
        ]);
        if (![statusResponse, agentsResponse, runsResponse, ownersResponse].every(response => response.ok)) {
            throw new Error('Agent operations could not be loaded.');
        }
        agentControlState = await statusResponse.json();
        allAgentsData = (await agentsResponse.json()).agents || [];
        allAgentRuns = (await runsResponse.json()).runs || [];
        agentOwners = (await ownersResponse.json()).owners || [];
        renderAgentControls();
        renderAgents();
        renderAgentRuns();
    } catch (error) {
        grid.innerHTML = `<div class="agent-empty-state agent-error-state">${escapeHtml(error.message)}</div>`;
    }
}

function renderAgentControls() {
    const banner = document.getElementById('agentControlBanner');
    const title = document.getElementById('agentControlTitle');
    const description = document.getElementById('agentControlDescription');
    const button = document.getElementById('agentEmergencyButton');
    if (!banner || !title || !description || !button) return;

    banner.classList.toggle('stopped', Boolean(agentControlState.emergency_stopped));
    if (!agentControlState.enabled) {
        title.textContent = 'Agent execution is disabled';
        description.textContent = 'Set AGENTS_ENABLED=true and restart Grasp.';
        button.disabled = true;
        return;
    }
    button.disabled = false;
    if (agentControlState.emergency_stopped) {
        title.textContent = 'Emergency stop is active';
        description.textContent = agentControlState.reason || 'No agent runs will be started.';
        button.textContent = 'Resume agents';
        button.classList.add('agent-resume-button');
    } else {
        title.textContent = 'Agent runtime is live';
        description.textContent = 'Active schedules and manual runs use the durable worker queue.';
        button.textContent = 'Emergency stop';
        button.classList.remove('agent-resume-button');
    }
}

function agentOwnerName(agent) {
    const owner = agent.owner || agentOwners.find(item => item.id === agent.definition.owner_user_id);
    if (!owner) return agent.definition.owner_user_id;
    return `${owner.first_name || ''} ${owner.last_name || ''}`.trim() || owner.email || owner.id;
}

function agentSkillLabel(skill) {
    const labels = {
        knowledge_brief: 'Knowledge brief',
        gap_analysis: 'Gap analysis',
        risk_watch: 'Risk watch',
        decision_digest: 'Decision digest',
    };
    return labels[skill] || String(skill).replaceAll('_', ' ');
}

function agentScheduleLabel(agent) {
    const cron = agent.definition.schedule;
    const known = {
        '0 * * * *': 'Every hour',
        '0 9 * * 1-5': 'Weekdays at 09:00 UTC',
        '0 9 * * 1': 'Mondays at 09:00 UTC',
    };
    return cron ? known[cron] || `${cron} UTC` : 'Manual only';
}

function agentStateBadge(state) {
    const normalized = state || 'never_run';
    return `<span class="agent-state-badge agent-state-${escapeHtml(normalized)}">${escapeHtml(normalized.replaceAll('_', ' '))}</span>`;
}

function renderAgents() {
    const grid = document.getElementById('agentsGrid');
    if (!grid) return;
    document.getElementById('navAgentsBadge').style.display = allAgentsData.length ? '' : 'none';
    document.getElementById('navAgentsBadge').textContent = String(allAgentsData.length);
    if (!allAgentsData.length) {
        grid.innerHTML = `<div class="agent-empty-state">
            <strong>No agents configured</strong>
            <span>Create a governed routine for recurring briefs, risk watches, gap analysis, or decision digests.</span>
            <button type="button" class="approve-btn" onclick="openAgentEditor()">Create first agent</button>
        </div>`;
        return;
    }

    grid.innerHTML = allAgentsData.map(agent => {
        const definition = agent.definition;
        const latest = agent.latest_run;
        const statusText = agent.active ? 'Active' : agent.paused_reason || 'Inactive';
        const nextRun = agent.next_run_at ? timeAgo(agent.next_run_at, true) : 'Not scheduled';
        return `<article class="agent-card ${agent.active ? 'active' : 'inactive'}">
            <div class="agent-card-heading">
                <div>
                    <div class="agent-card-title-row">
                        <h5>${escapeHtml(agent.name)}</h5>
                        <span class="agent-activation-badge ${agent.active ? 'active' : ''}">${escapeHtml(statusText)}</span>
                    </div>
                    <p>${escapeHtml(definition.role)}</p>
                </div>
            </div>
            <div class="agent-card-purpose">${escapeHtml(definition.instructions)}</div>
            <dl class="agent-card-facts">
                <div><dt>Owner</dt><dd>${escapeHtml(agentOwnerName(agent))}</dd></div>
                <div><dt>Skill</dt><dd>${escapeHtml(agentSkillLabel(definition.skills[0]))}</dd></div>
                <div><dt>Schedule</dt><dd>${escapeHtml(agentScheduleLabel(agent))}</dd></div>
                <div><dt>Next run</dt><dd>${escapeHtml(nextRun)}</dd></div>
                <div><dt>Last run</dt><dd>${latest ? agentStateBadge(latest.state) : 'Never'}</dd></div>
                <div><dt>Daily budget</dt><dd>${definition.cost_budget_units ? definition.cost_budget_units.toLocaleString() : 'Unlimited'}</dd></div>
            </dl>
            ${agent.failure_count ? `<div class="agent-card-warning">${agent.failure_count} consecutive failure(s)</div>` : ''}
            <div class="agent-card-actions">
                <button type="button" class="agent-secondary-button" onclick="openAgentEditor('${agent.id}')">Edit</button>
                <button type="button" class="agent-secondary-button" onclick="setAgentActive('${agent.id}', ${!agent.active})">${agent.active ? 'Pause' : 'Activate'}</button>
                <button type="button" class="approve-btn" onclick="runAgent('${agent.id}')"
                    ${!agent.active || agentControlState.emergency_stopped ? 'disabled' : ''}>Run now</button>
            </div>
        </article>`;
    }).join('');
}

function renderAgentRuns() {
    const shell = document.getElementById('agentRunsTable');
    if (!shell) return;
    if (!allAgentRuns.length) {
        shell.innerHTML = '<div class="agent-empty-state"><strong>No runs yet</strong><span>Run an active agent to create its first report.</span></div>';
        return;
    }
    const names = Object.fromEntries(allAgentsData.map(agent => [agent.id, agent.name]));
    const rows = allAgentRuns.map(run => {
        const trigger = run.input?.trigger || 'manual';
        const hasDetails = Boolean(run.output?.report || run.output?.error);
        return `<tr>
            <td>${escapeHtml(names[run.agent_id] || 'Unknown agent')}</td>
            <td>${agentStateBadge(run.state)}</td>
            <td>${escapeHtml(trigger)}</td>
            <td>${Number(run.cost_units || 0).toLocaleString()}</td>
            <td>${run.created_at ? escapeHtml(timeAgo(run.created_at)) : '—'}</td>
            <td><button type="button" class="agent-link-button" onclick="openAgentReport('${run.id}')" ${hasDetails ? '' : 'disabled'}>View</button></td>
        </tr>`;
    }).join('');
    shell.innerHTML = `<table class="agent-runs-table">
        <thead><tr><th>Agent</th><th>Result</th><th>Trigger</th><th>Tokens</th><th>Started</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}

function populateAgentOwners(selectedId = '') {
    const select = document.getElementById('agentOwner');
    if (!select) return;
    select.innerHTML = agentOwners.map(owner => {
        const name = `${owner.first_name || ''} ${owner.last_name || ''}`.trim() || owner.email;
        return `<option value="${escapeHtml(owner.id)}" ${owner.id === selectedId ? 'selected' : ''}>${escapeHtml(name)} · ${escapeHtml(owner.email || owner.id)}</option>`;
    }).join('');
}

function resetAgentEditor() {
    document.getElementById('agentEditId').value = '';
    document.getElementById('agentName').value = '';
    document.getElementById('agentRole').value = '';
    document.getElementById('agentInstructions').value = '';
    document.getElementById('agentDomains').value = 'general';
    document.getElementById('agentSkill').value = 'knowledge_brief';
    document.getElementById('agentSchedulePreset').value = '';
    document.getElementById('agentCron').value = '';
    document.getElementById('agentRuntimeBudget').value = '300';
    document.getElementById('agentCostBudget').value = '20000';
    document.getElementById('agentConcurrency').value = '1';
    document.getElementById('agentEscalation').value = 'Agent owner';
    document.getElementById('agentSuppressUnchanged').checked = true;
    document.getElementById('agentAfterSync').checked = false;
    document.querySelectorAll('input[name="agentClassification"]').forEach(input => {
        input.checked = ['public', 'internal'].includes(input.value);
    });
    document.getElementById('agentFormError').textContent = '';
    updateAgentScheduleInput();
}

async function openAgentEditor(agentId = '') {
    if (!agentOwners.length) await loadAgents();
    resetAgentEditor();
    const agent = allAgentsData.find(item => item.id === agentId);
    const defaultOwner = agent?.definition.owner_user_id || currentAdminUser?.id || '';
    populateAgentOwners(defaultOwner);
    document.getElementById('agentEditorTitle').textContent = agent ? 'Edit company-brain agent' : 'Create company-brain agent';
    if (agent) {
        const definition = agent.definition;
        document.getElementById('agentEditId').value = agent.id;
        document.getElementById('agentName').value = definition.name;
        document.getElementById('agentRole').value = definition.role;
        document.getElementById('agentInstructions').value = definition.instructions;
        document.getElementById('agentDomains').value = definition.domains.join(', ');
        document.getElementById('agentSkill').value = definition.skills[0];
        const presets = ['', '0 * * * *', '0 9 * * 1-5', '0 9 * * 1'];
        document.getElementById('agentSchedulePreset').value = presets.includes(definition.schedule || '') ? definition.schedule || '' : 'custom';
        document.getElementById('agentCron').value = definition.schedule || '';
        document.getElementById('agentRuntimeBudget').value = definition.runtime_budget_seconds;
        document.getElementById('agentCostBudget').value = definition.cost_budget_units;
        document.getElementById('agentConcurrency').value = definition.concurrency_limit;
        document.getElementById('agentEscalation').value = definition.escalation_path;
        document.getElementById('agentSuppressUnchanged').checked = definition.suppress_unchanged;
        document.getElementById('agentAfterSync').checked = definition.event_triggers.includes('knowledge_sync');
        document.querySelectorAll('input[name="agentClassification"]').forEach(input => {
            input.checked = definition.allowed_classifications.includes(input.value);
        });
        updateAgentScheduleInput();
    }
    document.getElementById('agentEditorModal').classList.add('active');
    document.getElementById('agentName').focus();
}

function closeAgentEditor() {
    document.getElementById('agentEditorModal').classList.remove('active');
}

function updateAgentScheduleInput() {
    const preset = document.getElementById('agentSchedulePreset').value;
    const cronField = document.getElementById('agentCronField');
    cronField.style.display = preset === 'custom' ? '' : 'none';
    if (preset !== 'custom') document.getElementById('agentCron').value = preset;
}

function agentDefinitionFromForm() {
    const classifications = Array.from(document.querySelectorAll('input[name="agentClassification"]:checked')).map(input => input.value);
    const schedulePreset = document.getElementById('agentSchedulePreset').value;
    const schedule = schedulePreset === 'custom' ? document.getElementById('agentCron').value.trim() : schedulePreset;
    return {
        name: document.getElementById('agentName').value.trim(),
        role: document.getElementById('agentRole').value.trim(),
        owner_user_id: document.getElementById('agentOwner').value,
        instructions: document.getElementById('agentInstructions').value.trim(),
        domains: document.getElementById('agentDomains').value.split(',').map(value => value.trim().toLowerCase()).filter(Boolean),
        skills: [document.getElementById('agentSkill').value],
        allowed_classifications: classifications,
        allowed_actions: [],
        approval_thresholds: {},
        schedule: schedule || null,
        event_triggers: [
            'manual',
            ...(schedule ? ['schedule'] : []),
            ...(document.getElementById('agentAfterSync').checked ? ['knowledge_sync'] : []),
        ],
        runtime_budget_seconds: Number(document.getElementById('agentRuntimeBudget').value),
        cost_budget_units: Number(document.getElementById('agentCostBudget').value),
        concurrency_limit: Number(document.getElementById('agentConcurrency').value),
        escalation_path: document.getElementById('agentEscalation').value.trim(),
        suppress_unchanged: document.getElementById('agentSuppressUnchanged').checked,
    };
}

async function saveAgent() {
    const agentId = document.getElementById('agentEditId').value;
    const definition = agentDefinitionFromForm();
    const error = document.getElementById('agentFormError');
    error.textContent = '';
    if (!definition.name || !definition.role || definition.instructions.length < 10 || !definition.domains.length || !definition.allowed_classifications.length) {
        error.textContent = 'Complete the name, role, instructions, domains, and at least one classification.';
        return;
    }
    const button = document.getElementById('saveAgentButton');
    button.disabled = true;
    try {
        const response = await fetch(agentId ? `${API_BASE}/api/agents/${agentId}` : `${API_BASE}/api/agents`, {
            method: agentId ? 'PUT' : 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify(definition),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(agentApiError(data, 'Agent could not be saved.'));
        closeAgentEditor();
        showToast(agentId ? 'Agent updated' : 'Agent created', 'success');
        await loadAgents();
    } catch (requestError) {
        error.textContent = requestError.message;
    } finally {
        button.disabled = false;
    }
}

async function setAgentActive(agentId, active) {
    try {
        const response = await fetch(`${API_BASE}/api/agents/${agentId}/activation`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ active }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(agentApiError(data, 'Agent state could not be changed.'));
        showToast(active ? 'Agent activated' : 'Agent paused', active ? 'success' : 'info');
        await loadAgents();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function runAgent(agentId) {
    try {
        const response = await fetch(`${API_BASE}/api/agents/${agentId}/run`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: '' }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(agentApiError(data, 'Agent run could not be queued.'));
        showToast('Agent run queued', 'success');
        window.setTimeout(loadAgents, 1200);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function toggleAgentEmergencyStop() {
    const stopping = !agentControlState.emergency_stopped;
    let reason = '';
    if (stopping) {
        if (!window.confirm('Stop all company-brain agent execution for this organization?')) return;
        reason = window.prompt('Reason for the emergency stop:', 'Paused by an operator') || 'Paused by an operator';
    }
    try {
        const response = await fetch(`${API_BASE}/api/agents/emergency-stop`, {
            method: 'PUT',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ stopped: stopping, reason }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(agentApiError(data, 'Agent controls could not be changed.'));
        agentControlState = data;
        renderAgentControls();
        renderAgents();
        showToast(stopping ? 'Emergency stop activated' : 'Agent execution resumed', stopping ? 'warning' : 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function openAgentReport(runId) {
    const run = allAgentRuns.find(item => item.id === runId);
    if (!run) return;
    const agent = allAgentsData.find(item => item.id === run.agent_id);
    document.getElementById('agentReportTitle').textContent = agent?.name || 'Agent run';
    document.getElementById('agentReportMeta').innerHTML = `${agentStateBadge(run.state)}<span>${run.created_at ? escapeHtml(timeAgo(run.created_at)) : ''}</span><span>${Number(run.cost_units || 0).toLocaleString()} tokens</span>`;
    document.getElementById('agentReportContent').textContent = run.output?.report || run.output?.error || 'No report content.';
    document.getElementById('agentReportModal').classList.add('active');
}

function closeAgentReport() {
    document.getElementById('agentReportModal').classList.remove('active');
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

// ── Structured Memory ────────────────────────────────────────

let memoryEnabled = false;
let currentEntityId = null;

async function checkMemoryEnabled() {
    try {
        const response = await fetch(`${API_BASE}/api/memory/status`);
        if (!response.ok) return;
        const data = await response.json();
        memoryEnabled = data.enabled === true;
        document.getElementById('navMemory').style.display = memoryEnabled ? '' : 'none';
    } catch (_) {
        memoryEnabled = false;
    }
}

async function loadMemoryStats() {
    if (!memoryEnabled) return;
    const token = localStorage.getItem('grasp_session_token');
    try {
        const response = await fetch(`${API_BASE}/api/memory/stats`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) return;
        const stats = await response.json();
        const container = document.getElementById('memoryStatsCards');
        const typeIcons = {
            person: '👤', team: '👥', project: '📁', product: '📦',
            process: '⚙️', technology: '💻', decision: '📋', milestone: '🎯'
        };

        let html = '';
        html += `<div style="padding:16px;border-radius:12px;background:var(--bg-secondary);border:1px solid var(--border);text-align:center">
            <div style="font-size:24px;font-weight:700;color:var(--text-primary)">${stats.total_entities || 0}</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:4px">Total entities</div>
        </div>`;
        html += `<div style="padding:16px;border-radius:12px;background:var(--bg-secondary);border:1px solid var(--border);text-align:center">
            <div style="font-size:24px;font-weight:700;color:var(--text-primary)">${stats.total_relationships || 0}</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-top:4px">Relationships</div>
        </div>`;


        const byType = stats.entities_by_type || {};
        for (const [type, count] of Object.entries(byType)) {
            const icon = typeIcons[type] || '📄';
            html += `<div style="padding:16px;border-radius:12px;background:var(--bg-secondary);border:1px solid var(--border);text-align:center">
                <div style="font-size:24px;font-weight:700;color:var(--text-primary)">${count}</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px">${icon} ${formatEntityName(type)}</div>
            </div>`;
        }

        // Badge: pending work items
        const pending = (stats.work_items_by_status || {}).proposed || 0;
        const badge = document.getElementById('navMemoryBadge');
        if (pending > 0) {
            badge.textContent = pending;
            badge.style.display = '';
        } else {
            badge.style.display = 'none';
        }

        container.innerHTML = html;
    } catch (err) {
        console.error('Failed to load memory stats', err);
    }
}

async function loadEntities() {
    if (!memoryEnabled) return;
    const token = localStorage.getItem('grasp_session_token');
    const query = document.getElementById('memoryEntitySearch')?.value || '';
    const entityType = document.getElementById('memoryEntityTypeFilter')?.value || '';
    const container = document.getElementById('entitiesTableContainer');

    const params = new URLSearchParams();
    if (query) params.set('query', query);
    if (entityType) params.set('entity_type', entityType);
    params.set('limit', '100');

    try {
        const response = await fetch(`${API_BASE}/api/memory/entities?${params}`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) { container.innerHTML = '<p style="color:var(--text-secondary)">Failed to load entities.</p>'; return; }
        const data = await response.json();
        const entities = data.entities || [];

        if (entities.length === 0) {
            container.innerHTML = '<p style="color:var(--text-secondary)">No entities found.</p>';
            return;
        }

        const typeIcons = {
            person: '👤', team: '👥', project: '📁', product: '📦',
            process: '⚙️', technology: '💻', decision: '📋', milestone: '🎯'
        };
        const confColors = { high: 'var(--accent)', medium: 'var(--warning)', low: 'var(--danger)' };

        let html = `<table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="text-align:left;border-bottom:1px solid var(--border)">
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Type</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Name</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Aliases</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Confidence</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500"></th>
            </tr></thead><tbody>`;

        for (const e of entities) {
            const icon = typeIcons[e.entity_type] || '📄';
            const confColor = confColors[e.confidence] || 'var(--text-secondary)';
            const aliases = (e.aliases || []).join(', ') || '—';
            html += `<tr style="border-bottom:1px solid var(--border);cursor:pointer" onclick="showEntityDetail('${e.id}')">
                <td style="padding:10px 12px">${icon} ${formatEntityName(e.entity_type)}</td>
                <td style="padding:10px 12px;font-weight:600;color:var(--text-primary)">${escapeHtml(formatEntityName(e.canonical_name))}</td>
                <td style="padding:10px 12px;color:var(--text-secondary);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(aliases)}</td>
                <td style="padding:10px 12px"><span style="color:${confColor};font-weight:600;text-transform:capitalize">${e.confidence}</span></td>
                <td style="padding:10px 12px;text-align:right"><button class="agent-secondary-button" onclick="event.stopPropagation();showEntityDetail('${e.id}')" style="font-size:12px;padding:4px 10px">View</button></td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = '<p style="color:var(--danger)">Error loading entities.</p>';
        console.error(err);
    }
}

async function showEntityDetail(entityId) {
    currentEntityId = entityId;
    const token = localStorage.getItem('grasp_session_token');
    const body = document.getElementById('entityDetailBody');
    body.innerHTML = '<p style="color:var(--text-secondary)">Loading…</p>';
    document.getElementById('entityDetailModal').style.display = 'flex';

    try {
        const response = await fetch(`${API_BASE}/api/memory/entities/${entityId}`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) { body.innerHTML = '<p style="color:var(--danger)">Entity not found.</p>'; return; }
        const data = await response.json();
        const entity = data.entity;
        const rels = data.relationships || [];

        const typeIcons = {
            person: '👤', team: '👥', project: '📁', product: '📦',
            process: '⚙️', technology: '💻', decision: '📋', milestone: '🎯'
        };

        document.getElementById('entityDetailTitle').textContent =
            `${typeIcons[entity.entity_type] || '📄'} ${formatEntityName(entity.canonical_name)}`;

        let html = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">
            <div><strong style="font-size:12px;color:var(--text-secondary)">Type</strong><br><span style="text-transform:capitalize">${entity.entity_type}</span></div>
            <div><strong style="font-size:12px;color:var(--text-secondary)">Confidence</strong><br><span style="text-transform:capitalize;font-weight:600">${entity.confidence}</span></div>
            <div><strong style="font-size:12px;color:var(--text-secondary)">Sensitivity</strong><br>${entity.sensitivity}</div>
            <div><strong style="font-size:12px;color:var(--text-secondary)">ID</strong><br><code style="font-size:11px">${entity.id}</code></div>
        </div>`;

        const aliases = entity.aliases || [];
        if (aliases.length > 0) {
            html += `<div style="margin-bottom:16px"><strong style="font-size:12px;color:var(--text-secondary)">Aliases</strong><br>
                <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px">
                    ${aliases.map(a => `<span style="padding:3px 10px;border-radius:20px;background:var(--bg-secondary);border:1px solid var(--border);font-size:12px">${escapeHtml(a)}</span>`).join('')}
                </div></div>`;
        }

        const attrs = entity.attributes || {};
        const attrKeys = Object.keys(attrs);
        if (attrKeys.length > 0) {
            html += `<div style="margin-bottom:16px"><strong style="font-size:12px;color:var(--text-secondary)">Attributes</strong>
                <div style="margin-top:6px;display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:13px">
                    ${attrKeys.map(k => `<span style="font-weight:600">${escapeHtml(k)}</span><span>${escapeHtml(String(attrs[k]))}</span>`).join('')}
                </div></div>`;
        }

        if (rels.length > 0) {
            html += `<div style="margin-bottom:16px"><strong style="font-size:12px;color:var(--text-secondary)">Relationships (${rels.length})</strong>
                <div style="margin-top:6px">`;
            for (const rel of rels) {
                const isSource = rel.source_entity_id === entityId;
                const arrow = isSource ? '→' : '←';
                const otherLabel = isSource ? rel.target_entity_id : rel.source_entity_id;
                html += `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:13px">
                    ${arrow} <strong>${escapeHtml(rel.relationship_type)}</strong>
                    <span style="color:var(--text-secondary)">${otherLabel.substring(0,8)}…</span>
                    <span style="float:right;font-size:11px;color:var(--text-tertiary)">${rel.confidence}</span>
                </div>`;
            }
            html += '</div></div>';
        }

        const evidence = entity.evidence || [];
        if (evidence.length > 0) {
            html += `<div><strong style="font-size:12px;color:var(--text-secondary)">Evidence</strong>
                <div style="margin-top:6px;font-size:12px;color:var(--text-secondary);max-height:120px;overflow-y:auto;background:var(--bg-secondary);border-radius:8px;padding:10px">
                    <pre style="white-space:pre-wrap;margin:0">${escapeHtml(JSON.stringify(evidence, null, 2))}</pre>
                </div></div>`;
        }

        body.innerHTML = html;
    } catch (err) {
        body.innerHTML = '<p style="color:var(--danger)">Failed to load entity.</p>';
        console.error(err);
    }
}

function closeEntityDetail() {
    document.getElementById('entityDetailModal').style.display = 'none';
    currentEntityId = null;
}

async function reviewCurrentEntity(action) {
    if (!currentEntityId) return;
    const token = localStorage.getItem('grasp_session_token');
    try {
        const response = await fetch(`${API_BASE}/api/memory/entities/${currentEntityId}/review`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ action }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showToast(err.detail || `Failed to ${action} entity`, 'error');
            return;
        }
        showToast(`Entity ${action === 'confirm' ? 'confirmed' : 'retired'} successfully`, 'success');
        closeEntityDetail();
        loadEntities();
        loadMemoryStats();
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function loadWorkItems() {
    if (!memoryEnabled) return;
    const token = localStorage.getItem('grasp_session_token');
    const status = document.getElementById('memoryWorkItemStatusFilter')?.value || '';
    const container = document.getElementById('workItemsContainer');

    const params = new URLSearchParams();
    if (status) params.set('status', status);
    params.set('limit', '100');

    try {
        const response = await fetch(`${API_BASE}/api/memory/work-items?${params}`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) { container.innerHTML = '<p style="color:var(--text-secondary)">Failed to load work items.</p>'; return; }
        const data = await response.json();
        const items = data.work_items || [];

        if (items.length === 0) {
            container.innerHTML = '<p style="color:var(--text-secondary)">No work items found.</p>';
            return;
        }

        const statusColors = {
            proposed: 'var(--warning)', accepted: 'var(--accent)',
            completed: 'var(--success, #22c55e)', dismissed: 'var(--text-tertiary)'
        };

        let html = `<table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="text-align:left;border-bottom:1px solid var(--border)">
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Title</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Status</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Confidence</th>
                <th style="padding:10px 12px;color:var(--text-secondary);font-weight:500">Actions</th>
            </tr></thead><tbody>`;

        for (const item of items) {
            const statusColor = statusColors[item.status] || 'var(--text-secondary)';
            let actions = '';
            if (item.status === 'proposed') {
                actions = `<button class="approve-btn" style="font-size:11px;padding:3px 10px" onclick="event.stopPropagation();updateWorkItemStatus('${item.id}','accepted')">Accept</button>
                    <button class="reject-btn" style="font-size:11px;padding:3px 10px;margin-left:6px" onclick="event.stopPropagation();updateWorkItemStatus('${item.id}','dismissed')">Dismiss</button>`;
            } else if (item.status === 'accepted') {
                actions = `<button class="approve-btn" style="font-size:11px;padding:3px 10px" onclick="event.stopPropagation();updateWorkItemStatus('${item.id}','completed')">Complete</button>
                    <button class="reject-btn" style="font-size:11px;padding:3px 10px;margin-left:6px" onclick="event.stopPropagation();updateWorkItemStatus('${item.id}','dismissed')">Dismiss</button>`;
            } else {
                actions = `<span style="font-size:11px;color:var(--text-tertiary)">${item.status}</span>`;
            }
            html += `<tr style="border-bottom:1px solid var(--border)">
                <td style="padding:10px 12px;font-weight:500;color:var(--text-primary)">${escapeHtml(item.title)}</td>
                <td style="padding:10px 12px"><span style="color:${statusColor};font-weight:600;text-transform:capitalize">${item.status}</span></td>
                <td style="padding:10px 12px;text-transform:capitalize">${item.confidence}</td>
                <td style="padding:10px 12px">${actions}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = '<p style="color:var(--danger)">Error loading work items.</p>';
        console.error(err);
    }
}

async function updateWorkItemStatus(itemId, newStatus) {
    const token = localStorage.getItem('grasp_session_token');
    try {
        const response = await fetch(`${API_BASE}/api/memory/work-items/${itemId}/status`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ status: newStatus }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showToast(err.detail || `Failed to update work item`, 'error');
            return;
        }
        showToast(`Work item ${newStatus}`, 'success');
        loadWorkItems();
        loadMemoryStats();
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

// Manual extraction modal

function openExtractionModal() {
    document.getElementById('extractionText').value = '';
    document.getElementById('extractionSourceLabel').value = '';
    document.getElementById('extractionResult').style.display = 'none';
    document.getElementById('extractionSubmitBtn').disabled = false;
    document.getElementById('extractionModal').style.display = 'flex';
}

function closeExtractionModal() {
    document.getElementById('extractionModal').style.display = 'none';
}

async function submitExtraction() {
    const text = document.getElementById('extractionText').value.trim();
    if (text.length < 10) {
        showToast('Please enter at least 10 characters of text.', 'error');
        return;
    }
    const sourceLabel = document.getElementById('extractionSourceLabel').value.trim();
    const btn = document.getElementById('extractionSubmitBtn');
    const resultEl = document.getElementById('extractionResult');
    btn.disabled = true;
    btn.textContent = 'Extracting…';
    resultEl.style.display = 'none';

    const token = localStorage.getItem('grasp_session_token');
    try {
        const response = await fetch(`${API_BASE}/api/memory/extract`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ text, source_label: sourceLabel }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            resultEl.textContent = `Error: ${err.detail || 'Extraction failed'}`;
            resultEl.style.color = 'var(--danger)';
            resultEl.style.display = 'block';
            return;
        }
        const data = await response.json();
        resultEl.textContent = `Extracted ${data.entities_created || 0} entities and ${data.relationships_created || 0} relationships.`;
        resultEl.style.color = 'var(--accent)';
        resultEl.style.display = 'block';
        showToast(`Extracted ${data.entities_created || 0} entities`, 'success');
        loadEntities();
        loadMemoryStats();
    } catch (err) {
        resultEl.textContent = `Error: ${err.message}`;
        resultEl.style.color = 'var(--danger)';
        resultEl.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Extract Entities';
    }
}

// Rebuild entities modal
function openRebuildEntitiesModal() {
    document.getElementById('rebuildEntitiesResult').style.display = 'none';
    const btn = document.getElementById('rebuildEntitiesSubmitBtn');
    btn.disabled = false;
    btn.textContent = 'Rebuild all entities';
    document.getElementById('rebuildEntitiesModal').style.display = 'flex';
}

function closeRebuildEntitiesModal() {
    document.getElementById('rebuildEntitiesModal').style.display = 'none';
}

async function confirmRebuildEntities() {
    const btn = document.getElementById('rebuildEntitiesSubmitBtn');
    const resultEl = document.getElementById('rebuildEntitiesResult');

    btn.disabled = true;
    btn.textContent = 'Rebuilding…';
    resultEl.style.display = 'block';
    resultEl.style.color = 'var(--text-secondary)';
    resultEl.textContent = 'Deleting old entities and re-extracting from all documents. This may take a while…';

    try {
        const response = await fetch(`${API_BASE}/api/memory/rebuild`, {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            resultEl.textContent = `Error: ${err.detail || 'Rebuild failed'}`;
            resultEl.style.color = 'var(--danger)';
            return;
        }
        const data = await response.json();
        resultEl.style.color = 'var(--success, #27ae60)';
        resultEl.textContent =
            `Done! Deleted ${data.deleted_entities ?? 0} entities & ${data.deleted_relationships ?? 0} relationships. ` +
            `Processed ${data.docs_processed ?? 0} docs → ${data.entities_created ?? 0} entities, ${data.relationships_created ?? 0} relationships created.`;

        showToast(`Rebuilt entities: ${data.entities_created ?? 0} entities from ${data.docs_processed ?? 0} docs`, 'success');
        loadEntities();
        loadMemoryStats();
    } catch (err) {
        resultEl.textContent = `Error: ${err.message}`;
        resultEl.style.color = 'var(--danger)';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Rebuild all entities';
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

// ── Knowledge Graph (force-directed, upgraded) ──────────────

const GRAPH_TYPE_COLORS = {
    person:  '#6366f1', // indigo
    team:    '#0ea5e9', // sky
    project: '#f59e0b', // amber
    product: '#10b981', // emerald
};
const GRAPH_TYPE_ICONS = { person: '\ud83d\udc64', team: '\ud83d\udc65', project: '\ud83d\udcc1', product: '\ud83d\udce6' };
const GRAPH_NODE_BASE_RADIUS = 10;
const GRAPH_NODE_MIN_RADIUS = 6;
const GRAPH_NODE_MAX_RADIUS = 22;

let graphNodes = [];
let graphEdges = [];
let graphSimRunning = false;
let graphAnimFrame = null;
let graphTransform = { x: 0, y: 0, scale: 1 };
let graphDrag = null;       // { nodeIndex, offsetX, offsetY }
let graphPan = null;        // { startX, startY, origTx, origTy }
let graphHovered = null;    // node index
let graphTypeFilter = { person: true, team: true, project: true, product: true };
let graphTooltipEl = null;
let graphSelectedNode = null;  // index of clicked node for info popup
let graphWasDragging = false;  // track if mouse was dragging
let graphSearchTerm = '';
let graphFadeIn = 0;  // 0..1 for load animation

// ── Degree helpers ───────────────────────────────────────────

function computeNodeDegrees() {
    const degrees = new Map();
    for (const n of graphNodes) degrees.set(n.id, 0);
    for (const e of graphEdges) {
        const a = graphNodes[e.source], b = graphNodes[e.target];
        if (a) degrees.set(a.id, (degrees.get(a.id) || 0) + 1);
        if (b) degrees.set(b.id, (degrees.get(b.id) || 0) + 1);
    }
    let maxDeg = 1;
    for (const d of degrees.values()) if (d > maxDeg) maxDeg = d;
    for (const n of graphNodes) {
        const deg = degrees.get(n.id) || 0;
        n.degree = deg;
        n.radius = GRAPH_NODE_MIN_RADIUS + (GRAPH_NODE_MAX_RADIUS - GRAPH_NODE_MIN_RADIUS) * (deg / maxDeg);
    }
}

function getConnectedSet(nodeIndex) {
    const connected = new Set([nodeIndex]);
    const connectedEdges = new Set();
    for (let i = 0; i < graphEdges.length; i++) {
        const e = graphEdges[i];
        if (e.source === nodeIndex || e.target === nodeIndex) {
            connected.add(e.source);
            connected.add(e.target);
            connectedEdges.add(i);
        }
    }
    return { nodes: connected, edges: connectedEdges };
}

// ── Barnes-Hut Quadtree ──────────────────────────────────────

class QuadTreeNode {
    constructor(x, y, w, h) {
        this.x = x; this.y = y; this.w = w; this.h = h;
        this.body = null;  // { x, y, charge }
        this.cx = 0; this.cy = 0; this.totalCharge = 0;
        this.children = null; // [NW, NE, SW, SE]
    }

    insert(b) {
        if (this.w < 1) return; // too small
        if (!this.body && !this.children) {
            this.body = b;
            this.cx = b.x; this.cy = b.y; this.totalCharge = b.charge;
            return;
        }
        if (!this.children) {
            this._subdivide();
            this._insertIntoChild(this.body);
            this.body = null;
        }
        this._insertIntoChild(b);
        // update center of charge
        const tc = this.totalCharge + b.charge;
        if (tc !== 0) {
            this.cx = (this.cx * this.totalCharge + b.x * b.charge) / tc;
            this.cy = (this.cy * this.totalCharge + b.y * b.charge) / tc;
        }
        this.totalCharge = tc;
    }

    _subdivide() {
        const hw = this.w / 2, hh = this.h / 2;
        this.children = [
            new QuadTreeNode(this.x, this.y, hw, hh),
            new QuadTreeNode(this.x + hw, this.y, hw, hh),
            new QuadTreeNode(this.x, this.y + hh, hw, hh),
            new QuadTreeNode(this.x + hw, this.y + hh, hw, hh),
        ];
    }

    _insertIntoChild(b) {
        const mx = this.x + this.w / 2, my = this.y + this.h / 2;
        const idx = (b.x >= mx ? 1 : 0) + (b.y >= my ? 2 : 0);
        this.children[idx].insert(b);
    }

    computeForce(bx, by, theta, result) {
        if (this.totalCharge === 0) return;
        const dx = this.cx - bx, dy = this.cy - by;
        const distSq = dx * dx + dy * dy;
        if (distSq < 1) return;

        // If leaf or far enough (Barnes-Hut criterion)
        if (!this.children || (this.w * this.w / distSq) < (theta * theta)) {
            const dist = Math.sqrt(distSq);
            const force = this.totalCharge / distSq;
            result.fx += (dx / dist) * force;
            result.fy += (dy / dist) * force;
            return;
        }

        for (const child of this.children) {
            if (child) child.computeForce(bx, by, theta, result);
        }
    }
}

function buildQuadTree(nodes) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
        if (n.x < minX) minX = n.x;
        if (n.y < minY) minY = n.y;
        if (n.x > maxX) maxX = n.x;
        if (n.y > maxY) maxY = n.y;
    }
    const pad = 100;
    const size = Math.max(maxX - minX, maxY - minY) + pad * 2;
    const tree = new QuadTreeNode(minX - pad, minY - pad, size, size);
    for (const n of nodes) {
        tree.insert({ x: n.x, y: n.y, charge: -800 });
    }
    return tree;
}

// ── Legend ────────────────────────────────────────────────────

function initGraphLegend() {
    const legend = document.getElementById('graphLegend');
    if (!legend) return;
    legend.innerHTML = '';
    for (const [type, color] of Object.entries(GRAPH_TYPE_COLORS)) {
        const pill = document.createElement('button');
        pill.className = 'knowledge-graph-legend-pill' + (graphTypeFilter[type] ? '' : ' inactive');
        pill.innerHTML = `${GRAPH_TYPE_ICONS[type] || ''} ${formatEntityName(type)}`;
        pill.onclick = () => {
            graphTypeFilter[type] = !graphTypeFilter[type];
            pill.classList.toggle('inactive', !graphTypeFilter[type]);
            renderGraph();
        };
        legend.appendChild(pill);
    }
}

// ── Search with dropdown ─────────────────────────────────────

function setupGraphSearch() {
    const input = document.getElementById('graphSearchInput');
    const dropdown = document.getElementById('graphSearchDropdown');
    if (!input || !dropdown || input._graphSearchAttached) return;
    input._graphSearchAttached = true;

    input.addEventListener('input', () => {
        graphSearchTerm = input.value.trim().toLowerCase();
        renderGraph();
        updateGraphSearchDropdown();
    });

    input.addEventListener('focus', () => {
        if (graphSearchTerm.length >= 1) updateGraphSearchDropdown();
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.graph-search-wrapper')) {
            closeGraphSearchDropdown();
        }
    });

    // Keyboard navigation
    input.addEventListener('keydown', (e) => {
        if (!dropdown.classList.contains('open')) return;
        const items = dropdown.querySelectorAll('.graph-search-dropdown-item');
        if (!items.length) return;

        let focused = dropdown.querySelector('.graph-search-dropdown-item:focus');
        let idx = Array.from(items).indexOf(focused);

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            idx = idx < items.length - 1 ? idx + 1 : 0;
            items[idx].focus();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            idx = idx > 0 ? idx - 1 : items.length - 1;
            items[idx].focus();
        } else if (e.key === 'Enter' && focused) {
            e.preventDefault();
            focused.click();
        } else if (e.key === 'Escape') {
            closeGraphSearchDropdown();
            input.blur();
        }
    });
}

function updateGraphSearchDropdown() {
    const dropdown = document.getElementById('graphSearchDropdown');
    if (!dropdown) return;

    if (graphSearchTerm.length < 1) {
        closeGraphSearchDropdown();
        return;
    }

    // Find matches
    const matches = [];
    graphNodes.forEach((n, i) => {
        if (graphTypeFilter[n.type] === false) return;
        const nameMatch = n.label.toLowerCase().includes(graphSearchTerm);
        const aliasMatch = n.aliases.some(a => a.toLowerCase().includes(graphSearchTerm));
        if (nameMatch || aliasMatch) {
            matches.push({ node: n, index: i, nameMatch });
        }
    });

    // Sort: name matches first, then by label
    matches.sort((a, b) => {
        if (a.nameMatch !== b.nameMatch) return a.nameMatch ? -1 : 1;
        return a.node.label.localeCompare(b.node.label);
    });

    if (matches.length === 0) {
        dropdown.innerHTML = '<div class="graph-search-dropdown-empty">No entities found</div>';
        dropdown.classList.add('open');
        return;
    }

    const items = matches.slice(0, 15).map(m => {
        const color = GRAPH_TYPE_COLORS[m.node.type] || '#888';
        return `<button type="button" class="graph-search-dropdown-item" data-node-index="${m.index}" onclick="navigateToNode(${m.index})">
            <span class="search-dot" style="background:${color}"></span>
            <span class="search-name">${escapeHtml(m.node.label)}</span>
            <span class="search-type">${formatEntityName(m.node.type)}</span>
        </button>`;
    }).join('');

    const suffix = matches.length > 15 ? `<div class="graph-search-dropdown-empty">+${matches.length - 15} more results</div>` : '';
    dropdown.innerHTML = items + suffix;
    dropdown.classList.add('open');
}

function closeGraphSearchDropdown() {
    const dropdown = document.getElementById('graphSearchDropdown');
    if (dropdown) dropdown.classList.remove('open');
}

function navigateToNode(nodeIndex) {
    const n = graphNodes[nodeIndex];
    if (!n) return;

    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;

    // Close dropdown and update search
    closeGraphSearchDropdown();
    const input = document.getElementById('graphSearchInput');
    if (input) {
        input.value = n.label;
        graphSearchTerm = n.label.toLowerCase();
    }

    // Zoom to 1.0 scale and center on node
    const targetScale = Math.max(graphTransform.scale, 1.0);
    const cw = canvas.clientWidth / 2;
    const ch = canvas.clientHeight / 2;

    graphTransform.scale = targetScale;
    graphTransform.x = cw - n.x * targetScale;
    graphTransform.y = ch - n.y * targetScale;

    // Select the node and show tooltip
    graphHovered = nodeIndex;
    graphSelectedNode = nodeIndex;

    // Calculate screen position for the tooltip
    const screenX = n.x * targetScale + graphTransform.x + canvas.getBoundingClientRect().left;
    const screenY = n.y * targetScale + graphTransform.y + canvas.getBoundingClientRect().top;
    showGraphTooltipForNode(nodeIndex, screenX + 20, screenY);

    renderGraph();
}

// ── Load Graph Data ──────────────────────────────────────────

async function loadGraph() {
    if (!memoryEnabled) return;
    const token = localStorage.getItem('grasp_session_token');
    const canvas = document.getElementById('graphCanvas');
    const emptyState = document.getElementById('graphEmptyState');
    if (!canvas) return;

    // Resize canvas to container
    const container = canvas.parentElement;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = canvas.clientHeight * dpr;

    initGraphLegend();
    setupGraphSearch();

    // Clear search
    const searchInput = document.getElementById('graphSearchInput');
    if (searchInput) { searchInput.value = ''; graphSearchTerm = ''; }

    const skeleton = document.getElementById('graphLoadingSkeleton');
    if (skeleton) skeleton.style.display = 'flex';
    try {
        const response = await fetch(`${API_BASE}/api/memory/graph?limit=200`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        if (!response.ok) { if (skeleton) skeleton.style.display = 'none'; return; }
        const data = await response.json();
        const nodes = data.nodes || [];
        const edges = data.edges || [];

        // Hide loading skeleton
        if (skeleton) skeleton.style.display = 'none';

        if (nodes.length === 0) {
            emptyState.style.display = 'flex';
            canvas.style.display = 'none';
            return;
        }
        emptyState.style.display = 'none';
        canvas.style.display = 'block';

        // Build node array with positions spread out more
        const cx = canvas.width / dpr / 2;
        const cy = canvas.height / dpr / 2;
        const spread = Math.min(cx, cy) * 0.8;
        graphNodes = nodes.map((n, i) => ({
            id: n.id,
            label: formatEntityName(n.canonical_name),
            type: n.entity_type,
            confidence: n.confidence,
            aliases: n.aliases || [],
            attributes: n.attributes || {},
            x: cx + (Math.random() - 0.5) * spread * 2,
            y: cy + (Math.random() - 0.5) * spread * 2,
            vx: 0,
            vy: 0,
            pinned: false,
            degree: 0,
            radius: GRAPH_NODE_BASE_RADIUS,
        }));

        // Build edge array with index references
        const idToIdx = {};
        graphNodes.forEach((n, i) => idToIdx[n.id] = i);
        graphEdges = edges
            .map(e => ({
                source: idToIdx[e.source_entity_id],
                target: idToIdx[e.target_entity_id],
                type: e.relationship_type,
                confidence: e.confidence,
            }))
            .filter(e => e.source !== undefined && e.target !== undefined);

        computeNodeDegrees();

        // Reset transform
        graphTransform = { x: 0, y: 0, scale: 1 };
        graphFadeIn = 0;

        startGraphSimulation();
    } catch (err) {
        console.error('Failed to load graph', err);
        if (skeleton) skeleton.style.display = 'none';
    }
}

// ── Simulation ───────────────────────────────────────────────

function startGraphSimulation() {
    if (graphAnimFrame) cancelAnimationFrame(graphAnimFrame);
    graphSimRunning = true;
    let alpha = 1.0;
    const alphaDecay = 0.018;
    const alphaMin = 0.001;
    const canvas = document.getElementById('graphCanvas');
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width / dpr;
    const H = canvas.height / dpr;
    const cx = W / 2;
    const cy = H / 2;
    let tickCount = 0;

    function tick() {
        if (alpha < alphaMin) {
            graphSimRunning = false;
            // Auto fit after settling
            graphFitToView();
            renderGraph();
            return;
        }
        alpha *= (1 - alphaDecay);
        tickCount++;

        // Fade in animation
        if (graphFadeIn < 1) graphFadeIn = Math.min(1, graphFadeIn + 0.04);

        // Build quadtree for Barnes-Hut
        const tree = buildQuadTree(graphNodes);

        // Center gravity
        for (const n of graphNodes) {
            if (n.pinned) continue;
            n.vx += (cx - n.x) * 0.008 * alpha;
            n.vy += (cy - n.y) * 0.008 * alpha;
        }

        // Barnes-Hut charge repulsion
        for (const n of graphNodes) {
            if (n.pinned) continue;
            const result = { fx: 0, fy: 0 };
            tree.computeForce(n.x, n.y, 0.9, result);
            n.vx += result.fx * alpha;
            n.vy += result.fy * alpha;
        }

        // Link spring
        const idealDist = 180;
        for (const e of graphEdges) {
            const a = graphNodes[e.source], b = graphNodes[e.target];
            if (!a || !b) continue;
            let dx = b.x - a.x, dy = b.y - a.y;
            let dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = (dist - idealDist) * 0.04 * alpha;
            const fx = dx / dist * force;
            const fy = dy / dist * force;
            if (!a.pinned) { a.vx += fx; a.vy += fy; }
            if (!b.pinned) { b.vx -= fx; b.vy -= fy; }
        }

        // Collision avoidance
        for (let i = 0; i < graphNodes.length; i++) {
            for (let j = i + 1; j < graphNodes.length; j++) {
                const a = graphNodes[i], b = graphNodes[j];
                const minDist = a.radius + b.radius + 8;
                let dx = b.x - a.x, dy = b.y - a.y;
                let dist = Math.sqrt(dx * dx + dy * dy) || 1;
                if (dist < minDist) {
                    const push = (minDist - dist) * 0.5;
                    const px = dx / dist * push;
                    const py = dy / dist * push;
                    if (!a.pinned) { a.x -= px; a.y -= py; }
                    if (!b.pinned) { b.x += px; b.y += py; }
                }
            }
        }

        // Apply velocity with damping + boundary constraints
        const margin = 40;
        for (const n of graphNodes) {
            if (n.pinned) continue;
            n.vx *= 0.55;
            n.vy *= 0.55;
            n.x += n.vx;
            n.y += n.vy;
            // Soft boundary
            if (n.x < margin) n.vx += (margin - n.x) * 0.1;
            if (n.x > W - margin) n.vx += (W - margin - n.x) * 0.1;
            if (n.y < margin) n.vy += (margin - n.y) * 0.1;
            if (n.y > H - margin) n.vy += (H - margin - n.y) * 0.1;
        }

        renderGraph();
        graphAnimFrame = requestAnimationFrame(tick);
    }
    tick();
}

// ── Rendering ────────────────────────────────────────────────

function renderGraph() {
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width / dpr;
    const H = canvas.height / dpr;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(graphTransform.x, graphTransform.y);
    ctx.scale(graphTransform.scale, graphTransform.scale);

    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const s = graphTransform.scale;
    const globalAlpha = graphFadeIn;

    // Determine visible types and search matches
    const visibleIds = new Set(
        graphNodes.filter(n => graphTypeFilter[n.type] !== false).map(n => n.id)
    );

    // Search highlighting
    let searchMatches = null;
    if (graphSearchTerm.length >= 2) {
        searchMatches = new Set();
        graphNodes.forEach((n, i) => {
            if (n.label.toLowerCase().includes(graphSearchTerm) ||
                n.aliases.some(a => a.toLowerCase().includes(graphSearchTerm))) {
                searchMatches.add(i);
            }
        });
    }

    // Hover highlight: connected subgraph
    let highlightSet = null;
    let highlightEdges = null;
    if (graphHovered !== null && graphHovered >= 0) {
        const connected = getConnectedSet(graphHovered);
        highlightSet = connected.nodes;
        highlightEdges = connected.edges;
    }

    // ── Draw edges ───────────────────────────────────────────
    for (let ei = 0; ei < graphEdges.length; ei++) {
        const e = graphEdges[ei];
        const a = graphNodes[e.source], b = graphNodes[e.target];
        if (!a || !b) continue;
        if (!visibleIds.has(a.id) || !visibleIds.has(b.id)) continue;

        const isHighlightedEdge = highlightEdges && highlightEdges.has(ei);
        const isDimmed = highlightSet && !isHighlightedEdge;

        // Edge thickness by confidence
        const confThickness = e.confidence === 'high' ? 2.2 : e.confidence === 'medium' ? 1.5 : 1.0;
        const lineWidth = Math.max(0.5, confThickness / s);

        ctx.globalAlpha = globalAlpha * (isDimmed ? 0.08 : 1);

        // Draw edge line
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = isHighlightedEdge
            ? (isLight ? 'rgba(60,60,80,0.7)' : 'rgba(200,200,220,0.55)')
            : (isLight ? 'rgba(100,100,120,0.3)' : 'rgba(150,150,170,0.2)');
        ctx.lineWidth = lineWidth;
        ctx.stroke();

        // Arrow
        const angle = Math.atan2(b.y - a.y, b.x - a.x);
        const targetR = (b.radius || GRAPH_NODE_BASE_RADIUS) / s + 4 / s;
        const arrowLen = Math.max(4, 7 / s);
        const arrowX = b.x - Math.cos(angle) * targetR;
        const arrowY = b.y - Math.sin(angle) * targetR;
        ctx.beginPath();
        ctx.moveTo(arrowX, arrowY);
        ctx.lineTo(arrowX - arrowLen * Math.cos(angle - 0.35), arrowY - arrowLen * Math.sin(angle - 0.35));
        ctx.lineTo(arrowX - arrowLen * Math.cos(angle + 0.35), arrowY - arrowLen * Math.sin(angle + 0.35));
        ctx.closePath();
        ctx.fillStyle = isHighlightedEdge
            ? (isLight ? 'rgba(60,60,80,0.8)' : 'rgba(200,200,220,0.6)')
            : (isLight ? 'rgba(100,100,120,0.45)' : 'rgba(150,150,170,0.35)');
        ctx.fill();

        // Edge label with background pill
        if (!isDimmed) {
            const mx = (a.x + b.x) / 2;
            const my = (a.y + b.y) / 2;
            const edgeLabelSize = Math.max(5, 8 / s);
            const labelText = e.type.replace(/_/g, ' ');

            ctx.save();
            ctx.font = `500 ${edgeLabelSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
            const labelW = ctx.measureText(labelText).width;
            const pillPadX = 4 / s;
            const pillPadY = 2 / s;

            // Rotate label to follow edge
            let labelAngle = angle;
            if (labelAngle > Math.PI / 2) labelAngle -= Math.PI;
            if (labelAngle < -Math.PI / 2) labelAngle += Math.PI;

            ctx.translate(mx, my);
            ctx.rotate(labelAngle);

            // Background pill
            ctx.fillStyle = isLight ? 'rgba(255,255,255,0.85)' : 'rgba(20,20,30,0.8)';
            const rr = 3 / s;
            const pw = labelW + pillPadX * 2;
            const ph = edgeLabelSize + pillPadY * 2;
            ctx.beginPath();
            ctx.roundRect(-pw / 2, -ph - 2 / s, pw, ph, rr);
            ctx.fill();

            // Label text
            ctx.fillStyle = isHighlightedEdge
                ? (isLight ? 'rgba(30,30,50,0.95)' : 'rgba(220,220,240,0.9)')
                : (isLight ? 'rgba(40,40,60,0.75)' : 'rgba(150,150,170,0.6)');
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            ctx.fillText(labelText, 0, -3 / s);
            ctx.restore();
        }
    }

    ctx.globalAlpha = globalAlpha;

    // ── Draw nodes ───────────────────────────────────────────
    for (let i = 0; i < graphNodes.length; i++) {
        const n = graphNodes[i];
        if (graphTypeFilter[n.type] === false) continue;

        const color = GRAPH_TYPE_COLORS[n.type] || '#888';
        const isHovered = graphHovered === i;
        const isSelected = graphSelectedNode === i;
        const isSearchMatch = searchMatches && searchMatches.has(i);
        const isDimmed = highlightSet && !highlightSet.has(i);
        const r = Math.max(GRAPH_NODE_MIN_RADIUS / s, (n.radius || GRAPH_NODE_BASE_RADIUS) / s);

        ctx.globalAlpha = globalAlpha * (isDimmed ? 0.12 : 1);

        // Ambient glow
        if (!isDimmed) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r * 2.5, 0, Math.PI * 2);
            const ambientGlow = ctx.createRadialGradient(n.x, n.y, r * 0.5, n.x, n.y, r * 2.5);
            ambientGlow.addColorStop(0, color + '18');
            ambientGlow.addColorStop(1, color + '00');
            ctx.fillStyle = ambientGlow;
            ctx.fill();
        }

        // Hover/selected glow ring
        if ((isHovered || isSelected) && !isDimmed) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 6 / s, 0, Math.PI * 2);
            const glow = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 10 / s);
            glow.addColorStop(0, color + '55');
            glow.addColorStop(1, color + '00');
            ctx.fillStyle = glow;
            ctx.fill();
        }

        // Search match pulse ring
        if (isSearchMatch && !isDimmed) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 5 / s, 0, Math.PI * 2);
            ctx.strokeStyle = '#facc15';
            ctx.lineWidth = Math.max(1, 2.5 / s);
            ctx.setLineDash([4 / s, 3 / s]);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        // Node circle with gradient
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        const grad = ctx.createRadialGradient(n.x - r * 0.3, n.y - r * 0.3, r * 0.1, n.x, n.y, r);
        const lightColor = lightenColor(color, 40);
        grad.addColorStop(0, lightColor + ((isHovered || isSelected) ? 'ff' : 'dd'));
        grad.addColorStop(1, color + ((isHovered || isSelected) ? 'ee' : 'cc'));
        ctx.fillStyle = grad;
        ctx.fill();

        // Border
        ctx.strokeStyle = (isHovered || isSelected)
            ? (isLight ? '#1a1a2e' : '#ffffff')
            : color + '88';
        ctx.lineWidth = (isHovered || isSelected) ? Math.max(1, 2.5 / s) : Math.max(0.5, 1.2 / s);
        ctx.stroke();

        // Label
        const labelSize = Math.max(5, ((isHovered || isSelected) ? 11 : 10) / s);
        const labelWeight = (isHovered || isSelected) ? '600' : '500';
        ctx.font = `${labelWeight} ${labelSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';

        // Label background for readability
        let label = n.label;
        if (label.length > 20) label = label.substring(0, 18) + '\u2026';
        const labelMetrics = ctx.measureText(label);
        const lbgPad = 3 / s;
        ctx.fillStyle = isLight ? 'rgba(255,255,255,0.75)' : 'rgba(10,10,20,0.65)';
        ctx.beginPath();
        ctx.roundRect(
            n.x - labelMetrics.width / 2 - lbgPad,
            n.y + r + 2 / s,
            labelMetrics.width + lbgPad * 2,
            labelSize + lbgPad * 2,
            2 / s
        );
        ctx.fill();

        ctx.fillStyle = isDimmed
            ? (isLight ? 'rgba(0,0,0,0.2)' : 'rgba(200,200,210,0.2)')
            : isSearchMatch
                ? '#facc15'
                : (isLight ? '#000000' : ((isHovered || isSelected) ? '#ffffff' : 'rgba(200,200,210,0.9)'));
        ctx.fillText(label, n.x, n.y + r + 2 / s + lbgPad);
    }

    ctx.globalAlpha = 1;
    ctx.restore();
}

// Helper to lighten a hex color
function lightenColor(hex, amount) {
    const num = parseInt(hex.replace('#', ''), 16);
    const r = Math.min(255, (num >> 16) + amount);
    const g = Math.min(255, ((num >> 8) & 0x00ff) + amount);
    const b = Math.min(255, (num & 0x0000ff) + amount);
    return '#' + (0x1000000 + (r << 16) + (g << 8) + b).toString(16).slice(1);
}

// ── Graph interaction (mouse events) ─────────────────────────

function graphScreenToWorld(clientX, clientY) {
    const canvas = document.getElementById('graphCanvas');
    const rect = canvas.getBoundingClientRect();
    const x = (clientX - rect.left - graphTransform.x) / graphTransform.scale;
    const y = (clientY - rect.top - graphTransform.y) / graphTransform.scale;
    return { x, y };
}

function graphHitTest(wx, wy) {
    const s = graphTransform.scale;
    for (let i = graphNodes.length - 1; i >= 0; i--) {
        const n = graphNodes[i];
        if (graphTypeFilter[n.type] === false) continue;
        const r = Math.max(GRAPH_NODE_MIN_RADIUS / s, (n.radius || GRAPH_NODE_BASE_RADIUS) / s);
        const hitR = r + 4 / s;
        const dx = n.x - wx, dy = n.y - wy;
        if (dx * dx + dy * dy <= hitR * hitR) return i;
    }
    return -1;
}

function setupGraphEvents() {
    const canvas = document.getElementById('graphCanvas');
    if (!canvas || canvas._graphEventsAttached) return;
    canvas._graphEventsAttached = true;

    // Create tooltip inside graph container so it's visible in fullscreen
    graphTooltipEl = document.createElement('div');
    graphTooltipEl.className = 'graph-tooltip';
    const graphContainer = document.getElementById('knowledgeGraphContainer');
    if (graphContainer) {
        graphContainer.appendChild(graphTooltipEl);
    } else {
        document.body.appendChild(graphTooltipEl);
    }

    canvas.addEventListener('mousedown', (e) => {
        graphWasDragging = false;
        const w = graphScreenToWorld(e.clientX, e.clientY);
        const idx = graphHitTest(w.x, w.y);
        if (idx >= 0) {
            graphDrag = { nodeIndex: idx, offsetX: graphNodes[idx].x - w.x, offsetY: graphNodes[idx].y - w.y };
            graphNodes[idx].pinned = true;
            canvas.style.cursor = 'grabbing';
        } else {
            graphPan = { startX: e.clientX, startY: e.clientY, origTx: graphTransform.x, origTy: graphTransform.y };
            canvas.style.cursor = 'grabbing';
        }
    });

    canvas.addEventListener('mousemove', (e) => {
        const w = graphScreenToWorld(e.clientX, e.clientY);
        if (graphDrag) {
            graphWasDragging = true;
            closeGraphTooltip();
            const n = graphNodes[graphDrag.nodeIndex];
            n.x = w.x + graphDrag.offsetX;
            n.y = w.y + graphDrag.offsetY;
            n.vx = 0;
            n.vy = 0;
            renderGraph();
            return;
        }
        if (graphPan) {
            const dx = e.clientX - graphPan.startX;
            const dy = e.clientY - graphPan.startY;
            if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
                graphWasDragging = true;
                closeGraphTooltip();
            }
            graphTransform.x = graphPan.origTx + dx;
            graphTransform.y = graphPan.origTy + dy;
            renderGraph();
            return;
        }
        // Hover
        const idx = graphHitTest(w.x, w.y);
        if (idx !== graphHovered) {
            graphHovered = idx;
            canvas.style.cursor = idx >= 0 ? 'pointer' : 'grab';
            renderGraph();
        }
    });

    canvas.addEventListener('mouseup', (e) => {
        const wasDragging = graphWasDragging;
        if (graphDrag) {
            const dragIdx = graphDrag.nodeIndex;
            // Keep pinned if it was already pinned before drag
            if (!wasDragging) {
                // Click action
                graphNodes[dragIdx].pinned = false;
            }
            graphDrag = null;

            // Click to show/toggle info popup (only if not dragging)
            if (!wasDragging) {
                if (graphSelectedNode === dragIdx) {
                    closeGraphTooltip();
                } else if (graphTooltipEl) {
                    showGraphTooltipForNode(dragIdx, e.clientX, e.clientY);
                }
            }
        } else if (graphPan) {
            graphPan = null;
            // Click on empty space dismisses popup
            if (!wasDragging) {
                closeGraphTooltip();
            }
        }
        canvas.style.cursor = 'grab';
        graphWasDragging = false;
    });

    canvas.addEventListener('mouseleave', () => {
        graphDrag = null;
        graphPan = null;
        graphHovered = null;
        graphWasDragging = false;
        renderGraph();
    });

    canvas.addEventListener('dblclick', (e) => {
        const w = graphScreenToWorld(e.clientX, e.clientY);
        const idx = graphHitTest(w.x, w.y);
        if (idx >= 0) {
            // Toggle pin on double-click
            graphNodes[idx].pinned = !graphNodes[idx].pinned;
            renderGraph();
        }
    });

    canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        closeGraphTooltip();
        const rect = canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale = Math.min(Math.max(graphTransform.scale * delta, 0.15), 6);
        const ratio = newScale / graphTransform.scale;

        graphTransform.x = mouseX - (mouseX - graphTransform.x) * ratio;
        graphTransform.y = mouseY - (mouseY - graphTransform.y) * ratio;
        graphTransform.scale = newScale;
        renderGraph();
    }, { passive: false });
}

function showGraphTooltipForNode(nodeIdx, clientX, clientY) {
    const n = graphNodes[nodeIdx];
    graphSelectedNode = nodeIdx;
    const connected = getConnectedSet(nodeIdx);
    const connectedCount = connected.nodes.size - 1;

    let html = `<button class="graph-tooltip-close" onclick="closeGraphTooltip()" title="Close">&times;</button>`;
    html += `<div class="tooltip-title">${escapeHtml(n.label)}</div>`;
    html += `<div class="tooltip-type">${GRAPH_TYPE_ICONS[n.type] || ''} ${formatEntityName(n.type)} \u00b7 ${formatEntityName(n.confidence)} confidence</div>`;
    if (n.aliases && n.aliases.length > 0) {
        html += `<div style="margin-top:4px;font-size:11px;color:var(--text-secondary)">aka: ${escapeHtml(n.aliases.join(', '))}</div>`;
    }
    const attrKeys = n.attributes ? Object.keys(n.attributes) : [];
    if (attrKeys.length > 0) {
        html += '<div style="margin-top:4px;font-size:11px">';
        for (const k of attrKeys.slice(0, 4)) {
            html += `<div><strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(n.attributes[k]))}</div>`;
        }
        html += '</div>';
    }
    if (connectedCount > 0) {
        html += `<div class="tooltip-connections"><strong>${connectedCount}</strong> connection${connectedCount > 1 ? 's' : ''}</div>`;
    }

    graphTooltipEl.innerHTML = html;
    graphTooltipEl.style.pointerEvents = 'auto';

    // Position tooltip with viewport clamping
    const tipW = 300, tipH = 200;
    let tx = clientX + 16;
    let ty = clientY - 10;
    const vw = window.innerWidth, vh = window.innerHeight;
    if (tx + tipW > vw - 10) tx = clientX - tipW - 16;
    if (ty + tipH > vh - 10) ty = vh - tipH - 10;
    if (ty < 10) ty = 10;

    graphTooltipEl.style.left = tx + 'px';
    graphTooltipEl.style.top = ty + 'px';

    // Trigger CSS transition
    requestAnimationFrame(() => {
        graphTooltipEl.classList.add('visible');
    });

    renderGraph();
}

// ── Zoom controls ────────────────────────────────────────────

function graphZoomIn() {
    closeGraphTooltip();
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;
    const cx = canvas.clientWidth / 2;
    const cy = canvas.clientHeight / 2;
    const delta = 1.25;
    const newScale = Math.min(graphTransform.scale * delta, 6);
    const ratio = newScale / graphTransform.scale;
    graphTransform.x = cx - (cx - graphTransform.x) * ratio;
    graphTransform.y = cy - (cy - graphTransform.y) * ratio;
    graphTransform.scale = newScale;
    renderGraph();
}

function graphZoomOut() {
    closeGraphTooltip();
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;
    const cx = canvas.clientWidth / 2;
    const cy = canvas.clientHeight / 2;
    const delta = 0.8;
    const newScale = Math.max(graphTransform.scale * delta, 0.15);
    const ratio = newScale / graphTransform.scale;
    graphTransform.x = cx - (cx - graphTransform.x) * ratio;
    graphTransform.y = cy - (cy - graphTransform.y) * ratio;
    graphTransform.scale = newScale;
    renderGraph();
}

function graphFitToView() {
    closeGraphTooltip();
    if (graphNodes.length === 0) return;
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;

    const visible = graphNodes.filter(n => graphTypeFilter[n.type] !== false);
    if (visible.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of visible) {
        if (n.x < minX) minX = n.x;
        if (n.y < minY) minY = n.y;
        if (n.x > maxX) maxX = n.x;
        if (n.y > maxY) maxY = n.y;
    }

    const pad = 80;
    const bw = maxX - minX + pad * 2;
    const bh = maxY - minY + pad * 2;
    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    const scale = Math.min(cw / bw, ch / bh, 2);

    graphTransform.scale = scale;
    graphTransform.x = cw / 2 - ((minX + maxX) / 2) * scale;
    graphTransform.y = ch / 2 - ((minY + maxY) / 2) * scale;
    renderGraph();
}

// Attach events after DOM load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupGraphEvents);
} else {
    setupGraphEvents();
}

function showEntityGraph() {
    const container = document.getElementById('knowledgeGraphContainer');
    if (container) container.style.display = 'block';
    
    const viewBtn = document.getElementById('viewGraphBtn');
    if (viewBtn) viewBtn.style.display = 'none';
    
    const fitBtn = document.getElementById('graphFitBtn');
    if (fitBtn) fitBtn.style.display = 'inline-block';
    
    const resetBtn = document.getElementById('graphResetBtn');
    if (resetBtn) resetBtn.style.display = 'inline-block';


    loadGraph();
}

function closeEntityGraph() {
    if (document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
    }
    const container = document.getElementById('knowledgeGraphContainer');
    if (container) {
        container.classList.remove('is-fullscreen');
        container.style.display = 'none';
    }
    
    const viewBtn = document.getElementById('viewGraphBtn');
    if (viewBtn) viewBtn.style.display = 'inline-block';
    
    const fitBtn = document.getElementById('graphFitBtn');
    if (fitBtn) fitBtn.style.display = 'none';
    
    const resetBtn = document.getElementById('graphResetBtn');
    if (resetBtn) resetBtn.style.display = 'none';
    
    closeGraphTooltip();
    
    if (typeof graphSimRunning !== 'undefined' && graphSimRunning) {
        graphSimRunning = false;
        if (typeof graphAnimFrame !== 'undefined' && graphAnimFrame) {
            cancelAnimationFrame(graphAnimFrame);
        }
    }
}

function closeGraphTooltip() {
    graphSelectedNode = null;
    if (graphTooltipEl) {
        graphTooltipEl.classList.remove('visible');
        graphTooltipEl.style.pointerEvents = 'none';
        // After transition, hide completely
        setTimeout(() => {
            if (!graphTooltipEl.classList.contains('visible')) {
                graphTooltipEl.innerHTML = '';
            }
        }, 200);
    }
    renderGraph();
}

function resizeGraphCanvas() {
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;
    const container = canvas.parentElement;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = canvas.clientHeight * dpr;
    renderGraph();
}

function toggleGraphFullscreen() {
    closeGraphTooltip();
    const container = document.getElementById('knowledgeGraphContainer');
    if (!container) return;

    const isFull = document.fullscreenElement || container.classList.contains('is-fullscreen');
    if (!isFull) {
        if (container.requestFullscreen) {
            container.requestFullscreen().catch(() => {
                container.classList.add('is-fullscreen');
                resizeGraphCanvas();
            });
        } else {
            container.classList.add('is-fullscreen');
            resizeGraphCanvas();
        }
    } else {
        if (document.fullscreenElement && document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
        container.classList.remove('is-fullscreen');
        resizeGraphCanvas();
    }
}

document.addEventListener('fullscreenchange', () => {
    const container = document.getElementById('knowledgeGraphContainer');
    if (container && !document.fullscreenElement) {
        container.classList.remove('is-fullscreen');
    }
    resizeGraphCanvas();
});

window.addEventListener('resize', () => {
    const container = document.getElementById('knowledgeGraphContainer');
    if (container && container.style.display !== 'none') {
        resizeGraphCanvas();
    }
});

