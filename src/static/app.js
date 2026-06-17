/* ── Grasp Dashboard — Frontend Logic ──────────────────────── */

const API_BASE = '';
let isStreaming = false;
let queryHistory = JSON.parse(localStorage.getItem('grasp_history') || '[]');

// ── Initialization ────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    refreshStatus();
    checkPendingChanges();
    renderHistory();
    setInterval(refreshStatus, 30000);
    setInterval(checkPendingChanges, 15000);
});

// ── Status Polling ────────────────────────────────────────

async function refreshStatus() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const data = await res.json();

        // System status
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        if (data.status === 'syncing') {
            dot.className = 'status-dot syncing';
            text.textContent = 'Syncing';
        } else {
            dot.className = 'status-dot online';
            text.textContent = 'Online';
        }

        // Last sync
        const lastSync = document.getElementById('lastSyncTime');
        if (data.last_sync && data.last_sync.timestamp) {
            lastSync.textContent = timeAgo(data.last_sync.timestamp);
        }

        // Doc count
        const docCount = document.getElementById('docCount');
        if (data.document_stats && data.document_stats.total !== undefined) {
            docCount.textContent = data.document_stats.total.toLocaleString();
        }

        // Next sync
        const nextSync = document.getElementById('nextSync');
        if (data.next_scheduled) {
            nextSync.textContent = timeAgo(data.next_scheduled, true);
        }

        // Connectors
        const container = document.getElementById('connectorsContainer');
        const connectors = data.connector_health || {};
        const names = { confluence: 'Confluence', jira: 'Jira', sharepoint: 'SharePoint', slack: 'Slack', notion: 'Notion' };
        const icons = { confluence: '📘', jira: '🔷', sharepoint: '📂', slack: '💬', notion: '📝' };

        container.innerHTML = Object.entries(names).map(([key, name]) => {
            const health = connectors[key];
            const dotClass = health === true ? 'healthy' : health === false ? 'unhealthy' : 'unknown';
            return `<div class="connector-item"><span class="connector-dot ${dotClass}"></span>${icons[key]} ${name}</div>`;
        }).join('');

    } catch (e) {
        console.error('Status refresh failed:', e);
    }
}

// ── Pending Changes ───────────────────────────────────────

async function checkPendingChanges() {
    try {
        const res = await fetch(`${API_BASE}/api/changes/pending`);
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
        const res = await fetch(`${API_BASE}/api/changes/pending`);
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

        // By type breakdown
        if (cs.by_type && Object.keys(cs.by_type).length > 0) {
            html += '<div style="margin-bottom:16px"><strong style="font-size:12px;color:var(--text-secondary)">By Type:</strong>';
            for (const [type, counts] of Object.entries(cs.by_type)) {
                const parts = [];
                if (counts.added) parts.push(`+${counts.added}`);
                if (counts.modified) parts.push(`~${counts.modified}`);
                if (counts.deleted) parts.push(`-${counts.deleted}`);
                html += `<div style="font-size:12px;padding:2px 0;color:var(--text-secondary)">&nbsp;&nbsp;${type}: ${parts.join(', ')}</div>`;
            }
            html += '</div>';
        }

        // File list
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
            headers: { 'Content-Type': 'application/json' },
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
        const res = await fetch(`${API_BASE}/api/changes/reject`, { method: 'POST' });
        const data = await res.json();
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
        const res = await fetch(`${API_BASE}/api/sync/trigger`, { method: 'POST' });
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

// ── Query Submission ──────────────────────────────────────

function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitQuery();
    }
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

function askQuestion(text) {
    document.getElementById('queryInput').value = text;
    submitQuery();
}

async function submitQuery() {
    const input = document.getElementById('queryInput');
    const question = input.value.trim();
    if (!question || isStreaming) return;

    isStreaming = true;
    input.value = '';
    input.style.height = 'auto';
    document.getElementById('sendBtn').disabled = true;

    // Remove welcome
    const welcome = document.getElementById('welcome');
    if (welcome) welcome.remove();

    const chatArea = document.getElementById('chatArea');

    // User message
    const userMsg = document.createElement('div');
    userMsg.className = 'message message-user';
    userMsg.textContent = question;
    chatArea.appendChild(userMsg);

    // Assistant message
    const assistantMsg = document.createElement('div');
    assistantMsg.className = 'message message-assistant';
    assistantMsg.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
    chatArea.appendChild(assistantMsg);

    chatArea.scrollTop = chatArea.scrollHeight;

    // Stream response via SSE
    try {
        const response = await fetch(`${API_BASE}/api/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let buffer = '';
        let lastWasData = false;

        assistantMsg.innerHTML = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Parse SSE events from buffer
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('event: done')) {
                    lastWasData = false;
                    break;
                } else if (line.startsWith('event: error')) {
                    lastWasData = false;
                } else if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    // Insert newline between consecutive data: lines (multi-line SSE data)
                    if (lastWasData && data !== '') {
                        fullText += '\n';
                    }
                    fullText += data;
                    lastWasData = data !== '';
                } else if (line.trim() === '') {
                    // Empty line = end of SSE event, reset multi-line tracking
                    lastWasData = false;
                }
            }

            assistantMsg.innerHTML = renderMarkdown(fullText);
            chatArea.scrollTop = chatArea.scrollHeight;
        }

        // Save to history
        addToHistory(question, fullText);

    } catch (e) {
        assistantMsg.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
    }

    isStreaming = false;
    document.getElementById('sendBtn').disabled = false;
    chatArea.scrollTop = chatArea.scrollHeight;
}

// ── Markdown Rendering ────────────────────────────────────

function renderMarkdown(text) {
    if (!text) return '';

    // Escape HTML
    let html = escapeHtml(text);

    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="lang-$1">$2</code></pre>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Headings
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Bold and italic
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    // Numbered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

    // Line breaks to paragraphs
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';

    // Clean up empty paragraphs
    html = html.replace(/<p><\/p>/g, '');
    html = html.replace(/<p>(<h[123]>)/g, '$1');
    html = html.replace(/(<\/h[123]>)<\/p>/g, '$1');
    html = html.replace(/<p>(<pre>)/g, '$1');
    html = html.replace(/(<\/pre>)<\/p>/g, '$1');
    html = html.replace(/<p>(<ul>)/g, '$1');
    html = html.replace(/(<\/ul>)<\/p>/g, '$1');

    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
}

// ── History Management ────────────────────────────────────

function addToHistory(question, answer) {
    queryHistory.unshift({
        question,
        answer: answer.substring(0, 200),
        timestamp: new Date().toISOString(),
    });
    queryHistory = queryHistory.slice(0, 20);
    localStorage.setItem('grasp_history', JSON.stringify(queryHistory));
    renderHistory();
}

function renderHistory() {
    const container = document.getElementById('historyContainer');
    if (!queryHistory.length) {
        container.innerHTML = '<div style="font-size:12px;color:var(--text-tertiary);padding:8px 0">No queries yet</div>';
        return;
    }

    container.innerHTML = queryHistory.map(h =>
        `<div class="history-item" onclick="askQuestion('${escapeHtml(h.question).replace(/'/g, "\\'")}')">${escapeHtml(h.question)}</div>`
    ).join('');
}

// ── Utility ───────────────────────────────────────────────

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
    toast.style.cssText = `
        position: fixed; bottom: 24px; right: 24px; z-index: 9999;
        padding: 12px 20px; border-radius: 10px; font-size: 13px; font-weight: 500;
        color: white; max-width: 400px;
        animation: modalIn 0.2s ease-out;
        ${type === 'success' ? 'background: #059669;' : type === 'error' ? 'background: #dc2626;' : type === 'warning' ? 'background: #d97706;' : 'background: #4f46e5;'}
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
