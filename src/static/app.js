/* ── Grasp Dashboard — Frontend Logic ──────────────────────── */

const API_BASE = '';
let isStreaming = false;
let queryHistory = JSON.parse(localStorage.getItem('grasp_history') || '[]');
let currentUser = null;

// ── Initialization ────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    await checkAuth();
    refreshStatus();
    renderHistory();
    initOnboarding();
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

// ── Contribution Modal ────────────────────────────────────

let selectedContentType = 'document';
let selectedFile = null;

function openContributeModal() {
    document.getElementById('contributeModal').classList.add('active');
    // Reset form
    document.getElementById('contributeTitle').value = '';
    document.getElementById('contributeContent').value = '';

    // If logged in, auto-populate and lock the name field
    const nameInput = document.getElementById('contributeName');
    if (currentUser) {
        const fullName = `${currentUser.first_name || ''} ${currentUser.last_name || ''}`.trim();
        nameInput.value = fullName;
        nameInput.readOnly = true;
        nameInput.style.opacity = '0.7';
        nameInput.style.cursor = 'not-allowed';
    } else {
        // Pre-fill name from localStorage
        const savedName = localStorage.getItem('grasp_user_name') || '';
        nameInput.value = savedName;
        nameInput.readOnly = false;
        nameInput.style.opacity = '';
        nameInput.style.cursor = '';
    }

    selectedContentType = 'document';
    selectedFile = null;
    document.querySelectorAll('.type-pill').forEach(p => p.classList.remove('active'));
    document.querySelector('.type-pill[data-type="document"]').classList.add('active');
    updateContentFields();
    clearFileSelection();
}

function closeContributeModal() {
    document.getElementById('contributeModal').classList.remove('active');
}

function selectContentType(btn) {
    document.querySelectorAll('.type-pill').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    selectedContentType = btn.dataset.type;
    updateContentFields();
}

function updateContentFields() {
    const fileUploadField = document.getElementById('fileUploadField');
    const textContentField = document.getElementById('textContentField');
    const textarea = document.getElementById('contributeContent');

    if (selectedContentType === 'document') {
        // Show file upload, hide textarea
        fileUploadField.style.display = '';
        textContentField.style.display = 'none';
    } else {
        // Show textarea, hide file upload
        fileUploadField.style.display = 'none';
        textContentField.style.display = '';

        if (selectedContentType === 'code') {
            textarea.style.fontFamily = "'IBM Plex Mono', monospace";
            textarea.style.fontSize = '12.5px';
            textarea.placeholder = 'Paste your code here...';
        } else {
            textarea.style.fontFamily = "'Satoshi', sans-serif";
            textarea.style.fontSize = '14px';
            textarea.placeholder = 'Write your note here...';
        }
    }
}

// ── File Handling ─────────────────────────────────────────

function handleFileSelect(input) {
    const file = input.files[0];
    if (!file) return;

    // Validate extension
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['txt', 'md', 'pdf', 'docx'].includes(ext)) {
        showToast('Unsupported file type. Use .docx, .pdf, .txt, or .md', 'warning');
        input.value = '';
        return;
    }

    // Validate size (10 MB)
    if (file.size > 10 * 1024 * 1024) {
        showToast('File too large. Maximum size is 10 MB', 'warning');
        input.value = '';
        return;
    }

    selectedFile = file;
    document.getElementById('fileDropzone').style.display = 'none';
    document.getElementById('fileSelected').style.display = 'flex';
    document.getElementById('fileSelectedName').textContent = file.name;

    // Auto-fill title from filename if empty
    const titleInput = document.getElementById('contributeTitle');
    if (!titleInput.value.trim()) {
        titleInput.value = file.name.replace(/\.[^.]+$/, '').replace(/[-_]/g, ' ');
    }
}

function clearFileSelection(event) {
    if (event) event.stopPropagation();
    selectedFile = null;
    const fileInput = document.getElementById('contributeFile');
    if (fileInput) fileInput.value = '';
    const dropzone = document.getElementById('fileDropzone');
    const selected = document.getElementById('fileSelected');
    if (dropzone) dropzone.style.display = '';
    if (selected) selected.style.display = 'none';
}

// Drag and drop support
document.addEventListener('DOMContentLoaded', () => {
    // Wait a tick for the modal to be in the DOM
    setTimeout(() => {
        const dropzone = document.getElementById('fileDropzone');
        if (!dropzone) return;

        ['dragenter', 'dragover'].forEach(evt => {
            dropzone.addEventListener(evt, e => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add('drag-over');
            });
        });

        ['dragleave', 'drop'].forEach(evt => {
            dropzone.addEventListener(evt, e => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove('drag-over');
            });
        });

        dropzone.addEventListener('drop', e => {
            const file = e.dataTransfer.files[0];
            if (file) {
                // Create a DataTransfer to set the file input
                const dt = new DataTransfer();
                dt.items.add(file);
                document.getElementById('contributeFile').files = dt.files;
                handleFileSelect(document.getElementById('contributeFile'));
            }
        });
    }, 100);
});

// ── Submit ────────────────────────────────────────────────

async function submitContribution() {
    const name = document.getElementById('contributeName').value.trim();
    const title = document.getElementById('contributeTitle').value.trim();

    // Validate name (mandatory)
    if (!name) {
        showToast('Please enter your name', 'warning');
        document.getElementById('contributeName').focus();
        return;
    }

    if (!title) {
        showToast('Please enter a title', 'warning');
        document.getElementById('contributeTitle').focus();
        return;
    }

    // Save name to localStorage for convenience
    localStorage.setItem('grasp_user_name', name);

    const btn = document.getElementById('contributeSubmitBtn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';

    try {
        let res;

        if (selectedContentType === 'document') {
            // File upload path
            if (!selectedFile) {
                showToast('Please select a file to upload', 'warning');
                btn.disabled = false;
                btn.textContent = '✦ Submit for Review';
                return;
            }

            const formData = new FormData();
            formData.append('file', selectedFile);
            formData.append('title', title);
            formData.append('submitted_by', name);

            res = await fetch(`${API_BASE}/api/contributions/upload`, {
                method: 'POST',
                body: formData,
            });
        } else {
            // Text content path (code / plain_text)
            const content = document.getElementById('contributeContent').value.trim();
            if (!content) {
                showToast('Please enter some content', 'warning');
                document.getElementById('contributeContent').focus();
                btn.disabled = false;
                btn.textContent = '✦ Submit for Review';
                return;
            }

            res = await fetch(`${API_BASE}/api/contributions/submit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title,
                    content,
                    content_type: selectedContentType,
                    submitted_by: name,
                }),
            });
        }

        const data = await res.json();
        if (res.ok) {
            closeContributeModal();
            showToast(data.message || 'Contribution submitted for review ✓', 'success');
        } else {
            showToast(`Error: ${data.detail || 'Submission failed'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '✦ Submit for Review';
    }
}

// ── Theme Toggle ──────────────────────────────────────────

function initTheme() {
    const saved = localStorage.getItem('grasp_theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    }
    updateThemeIcon();
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

// Apply theme immediately (before DOMContentLoaded)
initTheme();

// ── Sidebar Collapse ─────────────────────────────────────

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    sidebar.classList.toggle('collapsed');
    localStorage.setItem('grasp_sidebar_collapsed', sidebar.classList.contains('collapsed') ? '1' : '0');
}

function initSidebar() {
    const collapsed = localStorage.getItem('grasp_sidebar_collapsed');
    if (collapsed === '1') {
        const sidebar = document.getElementById('sidebar');
        if (sidebar) sidebar.classList.add('collapsed');
    }
}

initSidebar();

// ── My Submissions ────────────────────────────────────────

function openMySubmissions() {
    document.getElementById('mySubmissionsModal').classList.add('active');
    document.getElementById('submissionsResults').innerHTML = '';
    loadMySubmissions();
}

function closeMySubmissions() {
    document.getElementById('mySubmissionsModal').classList.remove('active');
}

async function loadMySubmissions() {
    const results = document.getElementById('submissionsResults');
    results.innerHTML = '<p style="color:var(--text-tertiary);font-size:12px;text-align:center;padding:16px">Loading...</p>';

    try {
        // The server reads the grasp_user cookie automatically when no query param is given
        const headers = {};
        const token = localStorage.getItem('grasp_session_token');
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const res = await fetch(`${API_BASE}/api/contributions/my`, {
            credentials: 'same-origin',
            headers,
        });

        if (!res.ok) {
            // 422 means no cookie/name found — user hasn't submitted anything yet
            if (res.status === 422) {
                results.innerHTML = '<div style="text-align:center;padding:24px"><p style="color:var(--text-tertiary);font-size:13px">No submissions yet</p><p style="color:var(--text-tertiary);font-size:11px;margin-top:6px">Submit a contribution first and your history will appear here automatically.</p></div>';
                return;
            }
            throw new Error('Failed to load submissions');
        }

        const data = await res.json();

        if (!data.contributions || data.contributions.length === 0) {
            results.innerHTML = '<div style="text-align:center;padding:24px"><p style="color:var(--text-tertiary);font-size:13px">No submissions found</p></div>';
            return;
        }

        const statusColors = { pending: 'pending', approved: 'approved', rejected: 'rejected' };
        const typeIcons = { document: '📄', code: '💻', plain_text: '📝' };

        let html = '';
        for (const c of data.contributions) {
            const icon = typeIcons[c.content_type] || '📄';
            const statusClass = statusColors[c.status] || 'pending';

            html += `<div class="submission-item">
                <div class="submission-item-header">
                    <span class="submission-item-title">${icon} ${escapeHtml(c.title)}</span>
                    <span class="contribution-status-pill ${statusClass}">${c.status}</span>
                </div>
                <div class="submission-item-meta">
                    Submitted ${timeAgo(c.submitted_at)}${c.classified_as ? ` · Classified as <strong>${c.classified_as}</strong>` : ''}
                </div>`;

            if (c.admin_notes) {
                html += `<div class="submission-admin-notes">
                    <div class="submission-admin-notes-label">Admin Notes</div>
                    ${escapeHtml(c.admin_notes)}
                </div>`;
            }

            html += '</div>';
        }
        results.innerHTML = html;
    } catch (e) {
        results.innerHTML = `<p style="color:var(--danger);text-align:center;padding:16px">${e.message}</p>`;
    }
}

// ── Authentication ────────────────────────────────────────

async function checkAuth() {
    const token = localStorage.getItem('grasp_session_token');
    if (!token) {
        window.location.href = '/login';
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/api/auth/me`, {
            headers: { 'Authorization': `Bearer ${token}` },
        });

        if (res.status === 401) {
            localStorage.removeItem('grasp_session_token');
            localStorage.removeItem('grasp_user');
            window.location.href = '/login';
            return;
        }

        if (res.ok) {
            currentUser = await res.json();
            localStorage.setItem('grasp_user', JSON.stringify(currentUser));
            populateUserProfile(currentUser);
            showOnboardingIntro();
        }
    } catch (e) {
        // Network error — use cached user data if available
        const cached = localStorage.getItem('grasp_user');
        if (cached) {
            currentUser = JSON.parse(cached);
            populateUserProfile(currentUser);
        }
    }
}

function populateUserProfile(user) {
    if (!user) return;

    const section = document.getElementById('userProfileSection');
    const avatar = document.getElementById('userAvatar');
    const name = document.getElementById('userProfileName');
    const menuRole = document.getElementById('userMenuRole');

    if (section) section.style.display = '';

    // Show profile picture or initial
    if (avatar) {
        if (user.profile_picture) {
            avatar.innerHTML = `<img src="${user.profile_picture}" alt="Avatar" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
        } else {
            avatar.textContent = (user.first_name || '?')[0].toUpperCase();
        }
    }

    if (name) name.textContent = `${user.first_name || ''} ${user.last_name || ''}`.trim() || '—';
    if (menuRole && user.role) {
        const roleClass = getRoleClass(user.role);
        menuRole.innerHTML = `<span class="role-pill ${roleClass}">${user.role}</span>`;
    }
}

function getRoleClass(role) {
    switch (role) {
        case 'Intern': return 'role-intern';
        case 'Junior Associate': return 'role-junior';
        case 'Associate': return 'role-associate';
        case 'Senior Associate': return 'role-senior';
        case 'Team Lead': return 'role-lead';
        case 'Manager': return 'role-manager';
        case 'Director': return 'role-director';
        case 'Principal': return 'role-principal';
        case 'Vice President': return 'role-vp';
        case 'Partner': return 'role-partner';
        default: return '';
    }
}

function logout() {
    localStorage.removeItem('grasp_session_token');
    localStorage.removeItem('grasp_user');
    currentUser = null;
    window.location.href = '/login';
}

// ── User Menu (3-dot) ─────────────────────────────────────

function toggleUserMenu(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('userMenuDropdown');
    if (!dropdown) return;
    const isVisible = dropdown.style.display !== 'none';
    dropdown.style.display = isVisible ? 'none' : 'block';
}

function closeUserMenuDropdown() {
    const dropdown = document.getElementById('userMenuDropdown');
    if (dropdown) dropdown.style.display = 'none';
}

// Close user menu when clicking outside
document.addEventListener('click', (e) => {
    const dropdown = document.getElementById('userMenuDropdown');
    const btn = document.getElementById('userMenuBtn');
    if (dropdown && btn && !btn.contains(e.target)) {
        dropdown.style.display = 'none';
    }
});

// ── Sidebar Section Toggles ───────────────────────────────

function toggleSidebarSection(section) {
    const bodyMap = { connectors: 'connectorsSectionBody', queries: 'queriesSectionBody' };
    const chevronMap = { connectors: 'connectorsChevron', queries: 'queriesChevron' };
    const toggleMap = { connectors: 'connectorsToggle', queries: 'queriesToggle' };

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

// ── Onboarding Mode ───────────────────────────────────────

function initOnboarding() {
    const isOnboarding = localStorage.getItem('grasp_onboarding') === 'true';
    const checkbox = document.getElementById('onboardingCheckbox');
    if (checkbox) {
        checkbox.checked = isOnboarding;
        applyOnboardingState(isOnboarding);
    }
}

function toggleOnboarding() {
    const checkbox = document.getElementById('onboardingCheckbox');
    const isOn = checkbox ? checkbox.checked : false;
    localStorage.setItem('grasp_onboarding', isOn.toString());
    applyOnboardingState(isOn);
}

function applyOnboardingState(isOn) {
    const defaultChips = document.getElementById('defaultChips');
    const onboardingChips = document.getElementById('onboardingChips');
    const onboardingBanner = document.getElementById('onboardingBanner');

    if (defaultChips) defaultChips.style.display = isOn ? 'none' : '';
    if (onboardingChips) onboardingChips.style.display = isOn ? '' : 'none';
    if (onboardingBanner) onboardingBanner.style.display = isOn ? 'flex' : 'none';
}

function showOnboardingIntro() {
    const seen = localStorage.getItem('grasp_seen_onboarding_intro');
    if (!seen) {
        const modal = document.getElementById('onboardingIntroModal');
        if (modal) {
            modal.style.display = 'flex';
        }
    }
}

function dismissOnboardingIntro() {
    localStorage.setItem('grasp_seen_onboarding_intro', 'true');
    const modal = document.getElementById('onboardingIntroModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// ── Settings Modal ──────────────────────────────────────

/** Pending profile picture data URL (256×256 PNG) waiting to be saved. */
let _pendingProfilePicture = null;

function openSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (!modal) return;

    // Reset state
    _pendingProfilePicture = null;
    clearSettingsAvatarError();
    document.getElementById('settingsPwdError').style.display = 'none';
    document.getElementById('settingsCurrentPwd').value = '';
    document.getElementById('settingsNewPwd').value = '';
    document.getElementById('settingsConfirmPwd').value = '';

    // Pre-fill with current user data
    if (currentUser) {
        document.getElementById('settingsFirstName').value = currentUser.first_name || '';
        document.getElementById('settingsLastName').value = currentUser.last_name || '';
        document.getElementById('settingsDob').value = currentUser.dob || '';

        // Avatar preview
        const initial = document.getElementById('settingsAvatarInitial');
        const img = document.getElementById('settingsAvatarImg');
        if (currentUser.profile_picture) {
            img.src = currentUser.profile_picture;
            img.style.display = '';
            if (initial) initial.style.display = 'none';
        } else {
            img.style.display = 'none';
            img.src = '';
            if (initial) {
                initial.style.display = '';
                initial.textContent = (currentUser.first_name || '?')[0].toUpperCase();
            }
        }

        // Hide password section for Google users
        const pwdSection = document.getElementById('settingsPasswordSection');
        if (pwdSection) {
            pwdSection.style.display = currentUser.auth_method === 'google' ? 'none' : '';
        }
    }

    modal.classList.add('active');
}

function closeSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) modal.classList.remove('active');
    _pendingProfilePicture = null;
}

function clearSettingsAvatarError() {
    const err = document.getElementById('settingsAvatarError');
    if (err) { err.style.display = 'none'; err.textContent = ''; }
}

function showSettingsAvatarError(msg) {
    const err = document.getElementById('settingsAvatarError');
    if (err) { err.textContent = msg; err.style.display = ''; }
}

// ── Interactive Crop Modal ─────────────────────────────────

/** Crop modal state */
const _crop = {
    img: null,          // HTMLImageElement of the full-res original
    zoom: 1,            // current zoom multiplier (1 = fit-to-canvas)
    panX: 0,            // image center X offset in canvas px
    panY: 0,            // image center Y offset in canvas px
    dragging: false,
    lastX: 0,
    lastY: 0,
    canvasSize: 0,      // width=height of the square canvas in CSS px
    baseScale: 1,       // scale so that the image fills the canvas at zoom=1
};

function openCropModal(img) {
    _crop.img = img;

    const modal = document.getElementById('cropModal');
    // Show the modal first so getBoundingClientRect returns real dimensions
    modal.classList.add('active');

    // Defer sizing until the next frame (after the modal is painted)
    requestAnimationFrame(() => {
        const canvas = document.getElementById('cropCanvas');
        const wrapper = document.getElementById('cropCanvasWrapper');

        // Size the canvas to match the wrapper's rendered square
        const wRect = wrapper.getBoundingClientRect();
        const size = Math.min(wRect.width || 460, wRect.height || 460);
        _crop.canvasSize = size;
        canvas.width = size;
        canvas.height = size;
        canvas.style.width = size + 'px';
        canvas.style.height = size + 'px';

        // Compute base scale so the image fills the canvas (cover)
        const scaleX = size / img.naturalWidth;
        const scaleY = size / img.naturalHeight;
        _crop.baseScale = Math.max(scaleX, scaleY);

        // Center
        _crop.zoom = 1;
        _crop.panX = 0;
        _crop.panY = 0;

        // Reset slider
        const slider = document.getElementById('cropZoomSlider');
        if (slider) { slider.value = 1; }

        // Update SVG mask circle to match canvas center
        _updateCropMask(size);

        renderCropCanvas();
        _attachCropEvents();
    });
}

function _updateCropMask(size) {
    const r = size * 0.44; // circle is 88% of canvas size
    const cx = size / 2;
    const cy = size / 2;
    const circle = document.getElementById('cropMaskCircle');
    const border = document.getElementById('cropBorderCircle');
    if (circle) { circle.setAttribute('cx', cx); circle.setAttribute('cy', cy); circle.setAttribute('r', r); }
    if (border)  { border.setAttribute('cx', cx); border.setAttribute('cy', cy); border.setAttribute('r', r); }
}

function renderCropCanvas() {
    const canvas = document.getElementById('cropCanvas');
    if (!canvas || !_crop.img) return;
    const ctx = canvas.getContext('2d');
    const s = _crop.canvasSize;
    ctx.clearRect(0, 0, s, s);

    const scale = _crop.baseScale * _crop.zoom;
    const imgW = _crop.img.naturalWidth * scale;
    const imgH = _crop.img.naturalHeight * scale;

    // Image is drawn centered + panned
    const dx = (s - imgW) / 2 + _crop.panX;
    const dy = (s - imgH) / 2 + _crop.panY;

    ctx.drawImage(_crop.img, dx, dy, imgW, imgH);
}

function closeCropModal() {
    const modal = document.getElementById('cropModal');
    if (modal) modal.classList.remove('active');
    _detachCropEvents();
    // Reset the file input so the same file can be re-selected
    const input = document.getElementById('settingsAvatarFile');
    if (input) input.value = '';
}

function confirmCrop() {
    if (!_crop.img) return;

    const s = _crop.canvasSize;
    const r = s * 0.44;  // must match _updateCropMask
    const cx = s / 2;
    const cy = s / 2;

    // Extract the circular crop region into a 256×256 output canvas
    const outCanvas = document.createElement('canvas');
    outCanvas.width = 256;
    outCanvas.height = 256;
    const outCtx = outCanvas.getContext('2d');

    // Scale from canvas coords to output coords
    const outR = 128; // 256/2
    const ratio = outR / r;

    // Compute where the circle region sits in image space
    const scale = _crop.baseScale * _crop.zoom;
    const imgW = _crop.img.naturalWidth * scale;
    const imgH = _crop.img.naturalHeight * scale;
    const imgLeft = (s - imgW) / 2 + _crop.panX;
    const imgTop  = (s - imgH) / 2 + _crop.panY;

    // Top-left of the circular region in canvas space
    const regionLeft = cx - r;
    const regionTop  = cy - r;

    // In image coordinates
    const srcX = (regionLeft - imgLeft) / scale;
    const srcY = (regionTop  - imgTop)  / scale;
    const srcW = (r * 2) / scale;
    const srcH = (r * 2) / scale;

    // Clip to circle then draw
    outCtx.beginPath();
    outCtx.arc(128, 128, 128, 0, Math.PI * 2);
    outCtx.clip();
    outCtx.drawImage(_crop.img, srcX, srcY, srcW, srcH, 0, 0, 256, 256);

    _pendingProfilePicture = outCanvas.toDataURL('image/png');

    // Update the settings modal preview
    const previewImg  = document.getElementById('settingsAvatarImg');
    const previewInit = document.getElementById('settingsAvatarInitial');
    if (previewImg) {
        previewImg.src = _pendingProfilePicture;
        previewImg.style.display = '';
    }
    if (previewInit) previewInit.style.display = 'none';

    // Subtle pulse on the avatar preview
    const previewEl = document.getElementById('settingsAvatarPreview');
    if (previewEl) {
        previewEl.classList.remove('crop-confirmed');
        void previewEl.offsetWidth; // reflow
        previewEl.classList.add('crop-confirmed');
    }

    clearSettingsAvatarError();

    // Close crop modal
    const modal = document.getElementById('cropModal');
    if (modal) modal.classList.remove('active');
    _detachCropEvents();
}

// ── Crop canvas event wiring ───────────────────────────────

function _onCropMouseDown(e) {
    _crop.dragging = true;
    _crop.lastX = e.clientX;
    _crop.lastY = e.clientY;
}

function _onCropMouseMove(e) {
    if (!_crop.dragging) return;
    const dx = e.clientX - _crop.lastX;
    const dy = e.clientY - _crop.lastY;
    _crop.lastX = e.clientX;
    _crop.lastY = e.clientY;
    _clampAndPan(dx, dy);
}

function _onCropMouseUp() { _crop.dragging = false; }

function _onCropTouchStart(e) {
    if (e.touches.length === 1) {
        _crop.dragging = true;
        _crop.lastX = e.touches[0].clientX;
        _crop.lastY = e.touches[0].clientY;
    }
}

function _onCropTouchMove(e) {
    if (!_crop.dragging || e.touches.length !== 1) return;
    e.preventDefault();
    const dx = e.touches[0].clientX - _crop.lastX;
    const dy = e.touches[0].clientY - _crop.lastY;
    _crop.lastX = e.touches[0].clientX;
    _crop.lastY = e.touches[0].clientY;
    _clampAndPan(dx, dy);
}

function _onCropTouchEnd() { _crop.dragging = false; }

function _onCropWheel(e) {
    e.preventDefault();
    const delta = -e.deltaY * 0.001;
    _setZoom(_crop.zoom + delta * _crop.zoom);
}

function _clampAndPan(dx, dy) {
    _crop.panX += dx;
    _crop.panY += dy;
    _clampPan();
    renderCropCanvas();
}

function _clampPan() {
    if (!_crop.img) return;
    const s = _crop.canvasSize;
    const scale = _crop.baseScale * _crop.zoom;
    const imgW = _crop.img.naturalWidth * scale;
    const imgH = _crop.img.naturalHeight * scale;
    // Keep the image covering the canvas at all times
    const maxPanX = Math.max(0, (imgW - s) / 2);
    const maxPanY = Math.max(0, (imgH - s) / 2);
    _crop.panX = Math.max(-maxPanX, Math.min(maxPanX, _crop.panX));
    _crop.panY = Math.max(-maxPanY, Math.min(maxPanY, _crop.panY));
}

function _setZoom(z) {
    _crop.zoom = Math.min(3, Math.max(1, z));
    _clampPan();
    renderCropCanvas();
    const slider = document.getElementById('cropZoomSlider');
    if (slider) slider.value = _crop.zoom;
}

function _attachCropEvents() {
    const wrapper = document.getElementById('cropCanvasWrapper');
    if (!wrapper) return;
    wrapper.addEventListener('mousedown',  _onCropMouseDown);
    wrapper.addEventListener('wheel',      _onCropWheel, { passive: false });
    wrapper.addEventListener('touchstart', _onCropTouchStart, { passive: true });
    wrapper.addEventListener('touchmove',  _onCropTouchMove,  { passive: false });
    wrapper.addEventListener('touchend',   _onCropTouchEnd);
    document.addEventListener('mousemove', _onCropMouseMove);
    document.addEventListener('mouseup',   _onCropMouseUp);

    const slider = document.getElementById('cropZoomSlider');
    if (slider) slider.addEventListener('input', _onSliderInput);
}

function _detachCropEvents() {
    const wrapper = document.getElementById('cropCanvasWrapper');
    if (wrapper) {
        wrapper.removeEventListener('mousedown',  _onCropMouseDown);
        wrapper.removeEventListener('wheel',      _onCropWheel);
        wrapper.removeEventListener('touchstart', _onCropTouchStart);
        wrapper.removeEventListener('touchmove',  _onCropTouchMove);
        wrapper.removeEventListener('touchend',   _onCropTouchEnd);
    }
    document.removeEventListener('mousemove', _onCropMouseMove);
    document.removeEventListener('mouseup',   _onCropMouseUp);
    const slider = document.getElementById('cropZoomSlider');
    if (slider) slider.removeEventListener('input', _onSliderInput);
}

function _onSliderInput(e) {
    _setZoom(parseFloat(e.target.value));
}

// ── Profile Picture Upload → opens Crop Modal ─────────────

/**
 * Validates an image file (≥256×256) then opens the interactive crop modal.
 */
function handleProfilePictureUpload(file) {
    clearSettingsAvatarError();
    _pendingProfilePicture = null;
    if (!file) return;

    if (!file.type.startsWith('image/')) {
        showSettingsAvatarError('Please upload a valid image file.');
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        const img = new Image();
        img.onload = () => {
            if (img.naturalWidth < 256 || img.naturalHeight < 256) {
                showSettingsAvatarError(
                    `Image too small (${img.naturalWidth}×${img.naturalHeight} px). ` +
                    'Please upload an image that is at least 256 × 256 px.'
                );
                const input = document.getElementById('settingsAvatarFile');
                if (input) input.value = '';
                return;
            }
            // Open the interactive crop modal
            openCropModal(img);
        };
        img.onerror = () => showSettingsAvatarError('Could not read the image. Please try a different file.');
        img.src = e.target.result;
    };
    reader.readAsDataURL(file);
}

function handleAvatarDrop(event) {
    event.preventDefault();
    document.getElementById('settingsAvatarDropzone').classList.remove('drag-over');
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) handleProfilePictureUpload(file);
}


async function saveSettings() {
    const btn = document.getElementById('settingsSaveBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

    const token = localStorage.getItem('grasp_session_token');
    let profileSaved = false;
    let passwordChanged = false;
    let errors = [];

    // ── 1. Save profile (name / dob / picture) ─────────────────────
    try {
        const profilePayload = {
            first_name: document.getElementById('settingsFirstName').value.trim() || null,
            last_name: document.getElementById('settingsLastName').value.trim() || null,
            dob: document.getElementById('settingsDob').value || null,
            profile_picture: _pendingProfilePicture || null,
        };

        const profileRes = await fetch(`${API_BASE}/api/auth/profile`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify(profilePayload),
        });

        if (profileRes.ok) {
            const updated = await profileRes.json();
            currentUser = { ...currentUser, ...updated };
            localStorage.setItem('grasp_user', JSON.stringify(currentUser));
            populateUserProfile(currentUser);
            profileSaved = true;
        } else {
            const err = await profileRes.json();
            errors.push(err.detail || 'Failed to save profile');
        }
    } catch (e) {
        errors.push(`Profile save error: ${e.message}`);
    }

    // ── 2. Change password (only if fields are filled) ─────────────
    const currentPwd = document.getElementById('settingsCurrentPwd').value;
    const newPwd = document.getElementById('settingsNewPwd').value;
    const confirmPwd = document.getElementById('settingsConfirmPwd').value;
    const pwdError = document.getElementById('settingsPwdError');

    if (currentPwd || newPwd || confirmPwd) {
        // Validate client-side first
        if (!currentPwd) {
            errors.push('Please enter your current password.');
        } else if (newPwd.length < 8) {
            errors.push('New password must be at least 8 characters.');
        } else if (newPwd !== confirmPwd) {
            errors.push('New passwords do not match.');
        } else {
            try {
                const pwdRes = await fetch(`${API_BASE}/api/auth/password`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`,
                    },
                    body: JSON.stringify({
                        current_password: currentPwd,
                        new_password: newPwd,
                        confirm_new_password: confirmPwd,
                    }),
                });

                if (pwdRes.ok) {
                    passwordChanged = true;
                } else {
                    const err = await pwdRes.json();
                    errors.push(err.detail || 'Failed to change password.');
                }
            } catch (e) {
                errors.push(`Password change error: ${e.message}`);
            }
        }
    }

    if (btn) { btn.disabled = false; btn.textContent = 'Save Changes'; }

    if (errors.length > 0) {
        if (pwdError) {
            pwdError.textContent = errors.join(' ');
            pwdError.style.display = '';
        }
        showToast(errors.join(' '), 'error');
        return;
    }

    closeSettingsModal();

    if (passwordChanged) {
        showToast('Password changed. Please sign in again.', 'success');
        setTimeout(() => logout(), 1500);
    } else if (profileSaved) {
        showToast('Settings saved ✓', 'success');
    }
}
