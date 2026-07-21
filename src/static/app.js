/* ── Grasp Dashboard — Frontend Logic ──────────────────────── */

const API_BASE = '';
let isStreaming = false;
let currentUser = null;

// ── Chat Thread State ─────────────────────────────────────
let chatThreads = JSON.parse(localStorage.getItem('grasp_chats') || '[]');
let currentChatId = null;  // null = welcome screen / fresh state
let chatToDelete = null;

// Migrate: clear old format if present
if (localStorage.getItem('grasp_history')) {
    localStorage.removeItem('grasp_history');
}

// ── Initialization ────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    await checkAuth();
    await fetchChats();
    refreshStatus();
    renderChatList();
    initOnboarding();
    setInterval(refreshStatus, 30000);
});

async function fetchChats() {
    const token = localStorage.getItem('grasp_session_token');
    if (!token) return;
    try {
        const res = await fetch(`${API_BASE}/api/chats`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
            const data = await res.json();
            // Restore legacy createdAt properties if needed
            chatThreads = data.threads.map(t => ({
                ...t,
                createdAt: t.created_at || t.createdAt
            }));
            localStorage.setItem('grasp_chats', JSON.stringify(chatThreads));
        }
    } catch (e) {
        console.error('Failed to fetch chats', e);
    }
}

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

    // If no active chat, create one
    if (!currentChatId) {
        const chat = createChatThread(question);
        currentChatId = chat.id;
    }

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

    // Build history from current chat thread (prior messages only)
    const currentChat = chatThreads.find(c => c.id === currentChatId);
    const history = currentChat ? currentChat.messages.map(m => ({ role: m.role, content: m.content })) : [];

    // Stream response via SSE
    try {
        const response = await fetch(`${API_BASE}/api/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, history: history.length > 0 ? history : null }),
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let displayedText = '';
        let buffer = '';
        let lastWasData = false;
        let isDone = false;

        assistantMsg.innerHTML = '';

        let lastRenderTime = 0;
        const RENDER_INTERVAL = 80; // ms — throttle markdown re-renders

        function typeWriter() {
            if (!isStreaming) return;

            if (displayedText.length < fullText.length) {
                const diff = fullText.length - displayedText.length;

                // Add characters at a controlled pace to ensure a smooth typing effect
                // even if the backend sends large chunks at once.
                let charsToAdd = 2;
                if (diff > 20) charsToAdd = 4;
                if (diff > 50) charsToAdd = 8;
                if (diff > 100) charsToAdd = 12;
                if (diff > 200) charsToAdd = 20;
                if (diff > 400) charsToAdd = 30;

                displayedText += fullText.slice(displayedText.length, displayedText.length + charsToAdd);

                // Throttle markdown rendering to avoid freezing the browser
                const now = performance.now();
                if (now - lastRenderTime > RENDER_INTERVAL) {
                    lastRenderTime = now;
                    const cursorHtml = '<span style="display:inline-block;width:6px;height:15px;background:var(--accent-primary);margin-left:4px;vertical-align:middle;animation:pulse 1s infinite"></span>';
                    try {
                        assistantMsg.innerHTML = renderMarkdown(displayedText) + cursorHtml;
                    } catch (e) {
                        // Fallback to raw text if markdown parsing fails on partial text
                        assistantMsg.textContent = displayedText;
                    }
                    chatArea.scrollTop = chatArea.scrollHeight;
                }
                requestAnimationFrame(typeWriter);
            } else if (!isDone) {
                requestAnimationFrame(typeWriter);
            } else {
                console.log('[Grasp] Stream finished. fullText length:', fullText.length);
                if (!fullText.trim()) {
                    fullText = '*No response was generated. Please try again.*';
                }
                assistantMsg.innerHTML = renderMarkdown(fullText);
                chatArea.scrollTop = chatArea.scrollHeight;
                // Save Q/A to current chat thread
                saveToChatThread(currentChatId, question, fullText);
                isStreaming = false;
                document.getElementById('sendBtn').disabled = false;
            }
        }

        requestAnimationFrame(typeWriter);

        let streamFinished = false;
        while (!streamFinished) {
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
                    streamFinished = true;
                    isDone = true;
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

    // Strip \r from SSE wire format (\r\n → \n) so multiline regex anchors work
    text = text.replace(/\r/g, '');

    // Escape HTML
    let html = escapeHtml(text);

    // Code blocks (must be first to protect contents)
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="lang-$1">$2</code></pre>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Tables — detect and convert markdown tables to HTML
    html = html.replace(/((?:^\|.+\|[ \t]*\n)+)/gm, function(tableBlock) {
        const rows = tableBlock.trim().split('\n');
        if (rows.length < 2) return tableBlock;

        // Check if second row is a separator row (|---|---|...)
        const separatorMatch = rows[1].match(/^\|[\s\-:|]+\|$/);
        if (!separatorMatch) return tableBlock;

        // Parse alignment from separator row
        const sepCells = rows[1].split('|').slice(1, -1);
        const aligns = sepCells.map(cell => {
            const trimmed = cell.trim();
            if (trimmed.startsWith(':') && trimmed.endsWith(':')) return 'center';
            if (trimmed.endsWith(':')) return 'right';
            return 'left';
        });

        // Build header
        const headerCells = rows[0].split('|').slice(1, -1);
        let tableHtml = '<table><thead><tr>';
        headerCells.forEach((cell, i) => {
            const align = aligns[i] || 'left';
            tableHtml += `<th style="text-align:${align}">${cell.trim()}</th>`;
        });
        tableHtml += '</tr></thead><tbody>';

        // Build body rows (skip header and separator)
        for (let r = 2; r < rows.length; r++) {
            const cells = rows[r].split('|').slice(1, -1);
            if (cells.length === 0) continue;
            tableHtml += '<tr>';
            cells.forEach((cell, i) => {
                const align = aligns[i] || 'left';
                tableHtml += `<td style="text-align:${align}">${cell.trim()}</td>`;
            });
            tableHtml += '</tr>';
        }
        tableHtml += '</tbody></table>';
        return tableHtml;
    });

    // Horizontal rules
    html = html.replace(/^(?:---+|\*\*\*+|___+)$/gm, '<hr>');

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
    html = html.replace(/^\d+\. (.+)$/gm, '<oli>$1</oli>');
    html = html.replace(/(<oli>.*<\/oli>\n?)+/g, function(match) {
        return '<ol>' + match.replace(/<\/?oli>/g, function(tag) {
            return tag.replace('oli', 'li');
        }) + '</ol>';
    });

    // Line breaks to paragraphs
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';

    // Clean up empty paragraphs and unwrap block elements from <p>
    html = html.replace(/<p><\/p>/g, '');
    html = html.replace(/<p>(<h[123]>)/g, '$1');
    html = html.replace(/(<\/h[123]>)<\/p>/g, '$1');
    html = html.replace(/<p>(<pre>)/g, '$1');
    html = html.replace(/(<\/pre>)<\/p>/g, '$1');
    html = html.replace(/<p>(<ul>)/g, '$1');
    html = html.replace(/(<\/ul>)<\/p>/g, '$1');
    html = html.replace(/<p>(<ol>)/g, '$1');
    html = html.replace(/(<\/ol>)<\/p>/g, '$1');
    html = html.replace(/<p>(<table>)/g, '$1');
    html = html.replace(/(<\/table>)<\/p>/g, '$1');
    html = html.replace(/<p>(<hr>)/g, '$1');
    html = html.replace(/(<hr>)<\/p>/g, '$1');

    return html;
}

// ── Chat Thread Management ────────────────────────────────

function generateChatId() {
    return 'chat_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8);
}

function createChatThread(firstQuestion) {
    const chat = {
        id: generateChatId(),
        title: firstQuestion.length > 50 ? firstQuestion.substring(0, 50) + '…' : firstQuestion,
        createdAt: new Date().toISOString(),
        messages: [],
    };
    chatThreads.unshift(chat);
    // Cap total chats at 30
    chatThreads = chatThreads.slice(0, 30);
    persistChats();
    renderChatList();
    return chat;
}

function saveToChatThread(chatId, question, answer) {
    const chat = chatThreads.find(c => c.id === chatId);
    if (!chat) return;
    chat.messages.push({ role: 'user', content: question });
    chat.messages.push({ role: 'assistant', content: answer });
    persistChats();
    renderChatList();
}

function persistChats() {
    localStorage.setItem('grasp_chats', JSON.stringify(chatThreads));
    syncCurrentChat();
}

async function syncCurrentChat() {
    const token = localStorage.getItem('grasp_session_token');
    if (!token || !currentChatId) return;
    
    const chat = chatThreads.find(c => c.id === currentChatId);
    if (!chat) return;

    try {
        await fetch(`${API_BASE}/api/chats`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                id: chat.id,
                title: chat.title,
                messages: chat.messages,
                created_at: chat.createdAt
            })
        });
    } catch (e) {
        console.error('Failed to sync chat to DB', e);
    }
}

function startNewChat() {
    if (isStreaming) return;
    currentChatId = null;

    const chatArea = document.getElementById('chatArea');
    chatArea.innerHTML = '';

    // Re-create welcome screen
    const welcome = document.createElement('div');
    welcome.className = 'welcome';
    welcome.id = 'welcome';

    const isOnboarding = localStorage.getItem('grasp_onboarding') === 'true';

    welcome.innerHTML = `
        <div class="welcome-motif">
            <div class="motif-line"></div>
            <div class="motif-diamond"></div>
            <div class="motif-line"></div>
        </div>
        <h1>Ask <span class="accent-word">anything</span> about your company</h1>
        <p>Grasp searches across Confluence, Jira, SharePoint, Slack, and Notion to find the answer.</p>

        <div class="onboarding-banner" id="onboardingBanner" style="display:${isOnboarding ? 'flex' : 'none'}">
            <div class="onboarding-banner-icon"><img src="/icons/onboarding-dark.png" class="theme-icon-dark" alt="Onboarding" style="width:24px;height:24px;"><img src="/icons/onboarding-light.png" class="theme-icon-light" alt="Onboarding" style="width:24px;height:24px;"></div>
            <div>
                <strong>Welcome aboard!</strong> Onboarding mode is active. These prompts are designed to quickly
                familiarize you with company history, conventions, and current priorities.
            </div>
        </div>

        <div class="suggestion-chips" id="defaultChips" style="display:${isOnboarding ? 'none' : ''}">
            <div class="suggestion-chip stagger-1" onclick="askQuestion(this.textContent)">What's the current architecture of our backend?</div>
            <div class="suggestion-chip stagger-2" onclick="askQuestion(this.textContent)">What features are in progress this sprint?</div>
            <div class="suggestion-chip stagger-3" onclick="askQuestion(this.textContent)">Any recent incidents or outages?</div>
            <div class="suggestion-chip stagger-4" onclick="askQuestion(this.textContent)">What decisions were made in last week's meetings?</div>
        </div>

        <div class="suggestion-chips" id="onboardingChips" style="display:${isOnboarding ? '' : 'none'}">
            <div class="suggestion-chip stagger-1 onboarding-chip" onclick="askQuestion(this.textContent)">What is the company's history and founding story?</div>
            <div class="suggestion-chip stagger-2 onboarding-chip" onclick="askQuestion(this.textContent)">What are the key conventions and coding standards?</div>
            <div class="suggestion-chip stagger-3 onboarding-chip" onclick="askQuestion(this.textContent)">What projects are currently in progress?</div>
            <div class="suggestion-chip stagger-4 onboarding-chip" onclick="askQuestion(this.textContent)">Who are the key team members and their roles?</div>
            <div class="suggestion-chip stagger-5 onboarding-chip" onclick="askQuestion(this.textContent)">What tools and platforms does the company use?</div>
        </div>
    `;

    chatArea.appendChild(welcome);
    renderChatList();
}

function loadChat(chatId) {
    if (isStreaming) return;
    const chat = chatThreads.find(c => c.id === chatId);
    if (!chat) return;

    currentChatId = chatId;
    const chatArea = document.getElementById('chatArea');
    chatArea.innerHTML = '';

    // Render all messages from this chat thread
    for (const msg of chat.messages) {
        const div = document.createElement('div');
        if (msg.role === 'user') {
            div.className = 'message message-user';
            div.textContent = msg.content;
        } else {
            div.className = 'message message-assistant';
            div.innerHTML = renderMarkdown(msg.content);
        }
        chatArea.appendChild(div);
    }

    chatArea.scrollTop = chatArea.scrollHeight;
    renderChatList();
}

function openDeleteChatModal(chatId, event) {
    if (event) event.stopPropagation();
    chatToDelete = chatId;
    const modal = document.getElementById('deleteChatModal');
    if (modal) modal.classList.add('active');
}

function closeDeleteChatModal() {
    chatToDelete = null;
    const modal = document.getElementById('deleteChatModal');
    if (modal) modal.classList.remove('active');
}

async function confirmDeleteChat() {
    if (!chatToDelete) return;
    const chatId = chatToDelete;
    closeDeleteChatModal();

    chatThreads = chatThreads.filter(c => c.id !== chatId);
    localStorage.setItem('grasp_chats', JSON.stringify(chatThreads));
    
    if (currentChatId === chatId) {
        startNewChat();
    } else {
        renderChatList();
    }

    const token = localStorage.getItem('grasp_session_token');
    if (token) {
        try {
            await fetch(`${API_BASE}/api/chats/${chatId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` }
            });
        } catch (e) {
            console.error('Failed to delete chat in DB', e);
        }
    }
}

function renderChatList() {
    const container = document.getElementById('chatListContainer');
    if (!container) return;

    if (!chatThreads.length) {
        container.innerHTML = '<li style="font-size:12px;color:var(--text-tertiary);padding:6px 8px">No chats yet</li>';
        return;
    }

    container.innerHTML = chatThreads.map(chat => {
        const isActive = chat.id === currentChatId;
        const timeStr = timeAgo(chat.createdAt);
        const msgCount = chat.messages.filter(m => m.role === 'user').length;
        return `<li class="shadcn-sidebar-menu-item">
            <button class="shadcn-sidebar-menu-button chat-thread-item${isActive ? ' chat-thread-active' : ''}" onclick="loadChat('${chat.id}')">
                <svg class="shadcn-menu-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                <div class="chat-thread-info">
                    <div class="chat-thread-title">${escapeHtml(chat.title)}</div>
                    <div class="chat-thread-meta">${msgCount} msg${msgCount !== 1 ? 's' : ''} · ${timeStr}</div>
                </div>
                <button class="chat-delete-btn" onclick="openDeleteChatModal('${chat.id}', event)" aria-label="Delete chat" title="Delete chat">✕</button>
            </button>
        </li>`;
    }).join('');
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
    const provider = document.getElementById('sidebarProvider');
    if (!sidebar) return;
    const isExpanded = sidebar.getAttribute('data-state') === 'expanded';
    const newState = isExpanded ? 'collapsed' : 'expanded';
    sidebar.setAttribute('data-state', newState);
    if (provider) provider.setAttribute('data-sidebar-state', newState);
    localStorage.setItem('grasp_sidebar_collapsed', isExpanded ? '1' : '0');
}

function initSidebar() {
    const collapsed = localStorage.getItem('grasp_sidebar_collapsed');
    if (collapsed === '1') {
        const sidebar = document.getElementById('sidebar');
        const provider = document.getElementById('sidebarProvider');
        if (sidebar) sidebar.setAttribute('data-state', 'collapsed');
        if (provider) provider.setAttribute('data-sidebar-state', 'collapsed');
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
            // Always overwrite cache with fresh server data
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
            // Silently retry in background so stale data (e.g. missing profile_picture) self-corrects
            setTimeout(async () => {
                try {
                    const retryRes = await fetch(`${API_BASE}/api/auth/me`, {
                        headers: { 'Authorization': `Bearer ${token}` },
                    });
                    if (retryRes.ok) {
                        const freshUser = await retryRes.json();
                        localStorage.setItem('grasp_user', JSON.stringify(freshUser));
                        if (JSON.stringify(freshUser) !== JSON.stringify(currentUser)) {
                            currentUser = freshUser;
                            populateUserProfile(currentUser);
                        }
                    }
                } catch (_) { /* ignore */ }
            }, 3000);
        }
    }
}


function populateUserProfile(user) {
    if (!user) return;

    const section = document.getElementById('userProfileSection');
    const avatar = document.getElementById('userAvatar');
    const dropdownAvatar = document.getElementById('dropdownAvatar');
    const name = document.getElementById('userProfileName');
    const dropdownName = document.getElementById('dropdownUserName');
    const menuRole = document.getElementById('userMenuRole');
    const dropdownRole = document.getElementById('dropdownUserRole');

    if (section) section.style.display = '';

    const fullName = `${user.first_name || ''} ${user.last_name || ''}`.trim() || '—';
    const initial = (user.first_name || '?')[0].toUpperCase();
    const avatarHtml = user.profile_picture
        ? `<img src="${user.profile_picture}" alt="Avatar" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`
        : initial;

    if (avatar) avatar.innerHTML = avatarHtml;
    if (dropdownAvatar) dropdownAvatar.innerHTML = avatarHtml;
    if (name) name.textContent = fullName;
    if (dropdownName) dropdownName.textContent = fullName;

    if (user.role) {
        const roleClass = getRoleClass(user.role);
        const rolePill = `<span class="role-pill ${roleClass}">${user.role}</span>`;
        if (menuRole) menuRole.innerHTML = rolePill;
        if (dropdownRole) dropdownRole.innerHTML = rolePill;
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
    const bodyMap = { connectors: 'connectorsSectionBody', chats: 'chatsSectionBody' };
    const chevronMap = { connectors: 'connectorsChevron', chats: 'chatsChevron' };
    const toggleMap = { connectors: 'connectorsToggle', chats: 'chatsToggle' };

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

        // Hide avatar upload for Google users (synced automatically)
        const avatarUpload = document.getElementById('settingsAvatarUploadContainer');
        const avatarGoogleHint = document.getElementById('settingsGoogleAvatarHint');
        if (avatarUpload) {
            avatarUpload.style.display = currentUser.auth_method === 'google' ? 'none' : '';
        }
        if (avatarGoogleHint) {
            avatarGoogleHint.style.display = currentUser.auth_method === 'google' ? '' : 'none';
        }

        // Show DOB hint for Google users (Google doesn't provide DOB)
        const dobHint = document.getElementById('settingsGoogleDobHint');
        if (dobHint) {
            dobHint.style.display = currentUser.auth_method === 'google' && !currentUser.dob ? '' : 'none';
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

// ── Delete Account ────────────────────────────────────────

function openDeleteAccountModal() {
    const modal = document.getElementById('deleteAccountModal');
    if (!modal) return;

    // Reset state
    const pwdField = document.getElementById('deleteAccountPwdField');
    const typeField = document.getElementById('deleteAccountTypeField');
    const pwdInput = document.getElementById('deleteAccountPwd');
    const typeInput = document.getElementById('deleteAccountTypeInput');
    const errEl = document.getElementById('deleteAccountError');

    if (pwdInput) pwdInput.value = '';
    if (typeInput) typeInput.value = '';
    if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }

    // Show the right confirmation field based on auth method
    const isGoogle = currentUser && currentUser.auth_method === 'google';
    if (pwdField) pwdField.style.display = isGoogle ? 'none' : '';
    if (typeField) typeField.style.display = isGoogle ? '' : 'none';

    modal.classList.add('active');
}

function closeDeleteAccountModal() {
    const modal = document.getElementById('deleteAccountModal');
    if (modal) modal.classList.remove('active');
}

async function confirmDeleteAccount() {
    const btn = document.getElementById('deleteAccountConfirmBtn');
    const errEl = document.getElementById('deleteAccountError');

    const showErr = (msg) => {
        if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
    };

    const isGoogle = currentUser && currentUser.auth_method === 'google';

    // Client-side validation
    if (isGoogle) {
        const typeInput = document.getElementById('deleteAccountTypeInput');
        if (!typeInput || typeInput.value.trim() !== 'DELETE') {
            showErr('Please type DELETE exactly to confirm.');
            return;
        }
    } else {
        const pwdInput = document.getElementById('deleteAccountPwd');
        if (!pwdInput || !pwdInput.value) {
            showErr('Please enter your password to confirm.');
            return;
        }
    }

    if (btn) { btn.disabled = true; btn.textContent = 'Deleting…'; }
    if (errEl) { errEl.style.display = 'none'; }

    try {
        const token = localStorage.getItem('grasp_session_token');
        const payload = {};
        if (!isGoogle) {
            payload.password = document.getElementById('deleteAccountPwd').value;
        }

        const res = await fetch(`${API_BASE}/api/auth/account`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify(payload),
        });

        if (res.ok) {
            // Clear session and redirect
            localStorage.removeItem('grasp_session_token');
            localStorage.removeItem('grasp_user');
            currentUser = null;
            showToast('Your account has been deleted.', 'success');
            setTimeout(() => { window.location.href = '/login'; }, 1200);
        } else {
            const err = await res.json();
            showErr(err.detail || 'Failed to delete account. Please try again.');
            if (btn) { btn.disabled = false; btn.textContent = 'Yes, Delete My Account'; }
        }
    } catch (e) {
        showErr(`Error: ${e.message}`);
        if (btn) { btn.disabled = false; btn.textContent = 'Yes, Delete My Account'; }
    }
}

