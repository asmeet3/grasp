/* ── Grasp Dashboard — Frontend Logic ──────────────────────── */

const API_BASE = '';
let isStreaming = false;
let queryHistory = JSON.parse(localStorage.getItem('grasp_history') || '[]');

// ── Initialization ────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    refreshStatus();
    renderHistory();
    setInterval(refreshStatus, 30000);
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

        // Connectors — pill badge style
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

    } catch (e) {
        console.error('Status refresh failed:', e);
    }
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
        let displayedText = '';
        let buffer = '';
        let lastWasData = false;
        let isDone = false;

        assistantMsg.innerHTML = '';

        function typeWriter() {
            if (!isStreaming) return;
            
            if (displayedText.length < fullText.length) {
                const diff = fullText.length - displayedText.length;
                
                // Add characters at a controlled pace to ensure a smooth typing effect
                // even if the backend sends large chunks at once.
                let charsToAdd = 1;
                if (diff > 20) charsToAdd = 2;
                if (diff > 50) charsToAdd = 3;
                if (diff > 100) charsToAdd = 4;
                if (diff > 200) charsToAdd = 6;
                if (diff > 400) charsToAdd = 8;
                
                displayedText += fullText.slice(displayedText.length, displayedText.length + charsToAdd);
                
                const cursorHtml = '<span style="display:inline-block;width:6px;height:15px;background:var(--accent-primary);margin-left:4px;vertical-align:middle;animation:pulse 1s infinite"></span>';
                assistantMsg.innerHTML = renderMarkdown(displayedText) + cursorHtml;
                chatArea.scrollTop = chatArea.scrollHeight;
                requestAnimationFrame(typeWriter);
            } else if (!isDone) {
                requestAnimationFrame(typeWriter);
            } else {
                assistantMsg.innerHTML = renderMarkdown(fullText);
                chatArea.scrollTop = chatArea.scrollHeight;
                addToHistory(question, fullText);
                isStreaming = false;
                document.getElementById('sendBtn').disabled = false;
            }
        }
        
        requestAnimationFrame(typeWriter);

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                isDone = true;
                break;
            }

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
        }

    } catch (e) {
        assistantMsg.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
        isStreaming = false;
        document.getElementById('sendBtn').disabled = false;
        chatArea.scrollTop = chatArea.scrollHeight;
    }
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
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease-out';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
