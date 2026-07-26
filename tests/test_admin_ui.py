from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_user_management_uses_dedicated_semantic_columns() -> None:
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    table_section = javascript.split("function renderUsersTable()", 1)[1].split(
        "async function approveUserAction", 1
    )[0]

    assert 'class="users-table"' in table_section
    assert table_section.count('<col class="user-col-') == 7
    for label in (
        "User",
        "Authentication",
        "Job role",
        "System access",
        "Status",
        "Joined",
        "Actions",
    ):
        assert f'data-label="{label}"' in table_section
    assert "user-row-actions" in table_section
    assert "dropdown-menu-content" not in table_section


def test_user_management_has_container_responsive_card_layout() -> None:
    stylesheet = (ROOT / "src" / "static" / "styles.css").read_text(encoding="utf-8")
    html = (ROOT / "src" / "static" / "admin.html").read_text(encoding="utf-8")

    assert 'class="admin-content-container"' in html
    assert ".admin-content-container," in stylesheet
    assert "container: user-management / inline-size" in stylesheet
    assert "@container user-management (max-width: 900px)" in stylesheet
    assert "@container user-management (max-width: 560px)" in stylesheet
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in stylesheet
    assert ".shadcn-select-trigger" in stylesheet
    assert ".shadcn-select-content" in stylesheet


def test_current_user_is_pinned_before_selected_user_sort() -> None:
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    sort_section = javascript.split("filtered.sort((a, b) => {", 1)[1].split("const totalPages", 1)[
        0
    ]

    assert "currentAdminUser.id === a.id" in sort_section
    assert "currentAdminUser.id === b.id" in sort_section
    assert "if (aIsCurrentUser !== bIsCurrentUser) return aIsCurrentUser ? -1 : 1;" in sort_section
    assert 'data-current-user="true"' in javascript


def test_user_editor_closes_when_clicking_outside_user_table() -> None:
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")

    assert "let editingUserId = null;" in javascript
    assert "clickTarget.closest('.users-table-shell')" in javascript
    assert "cancelUserEdit(editingUserId);" in javascript
    assert "editingUserId = userId;" in javascript
    assert "if (editingUserId === userId) editingUserId = null;" in javascript


def test_job_role_and_access_use_edit_gated_shadcn_selects() -> None:
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    table_section = javascript.split("function renderUsersTable()", 1)[1].split(
        "async function approveUserAction", 1
    )[0]

    assert "function shadcnSelectMarkup" in javascript
    assert 'class="shadcn-select-trigger"' in javascript
    assert 'class="shadcn-select-content"' in javascript
    assert 'role="listbox"' in javascript
    assert 'role="option"' in javascript
    assert "function handleShadcnSelectTriggerKeydown" in javascript
    assert "function handleShadcnSelectItemKeydown" in javascript
    assert "onclick=\"enableUserEdit('${user.id}')\"" in table_section
    assert 'id="system-role-display-${user.id}"' in table_section
    assert table_section.count("hidden: status === 'approved'") == 2
    assert "for (const field of ['role', 'access'])" in javascript


def test_admin_sidebar_buttons_match_user_actions_without_sharing_classes() -> None:
    html = (ROOT / "src" / "static" / "admin.html").read_text(encoding="utf-8")
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "src" / "static" / "styles.css").read_text(encoding="utf-8")

    assert html.count('class="admin-sidebar-button') == 4
    assert "admin-nav-item" not in html
    assert "document.querySelectorAll('.admin-sidebar-button')" in javascript
    assert ".admin-nav-item" not in stylesheet


def test_agent_management_is_available_to_operator_and_administrator() -> None:
    html = (ROOT / "src" / "static" / "admin.html").read_text(encoding="utf-8")
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")

    assert 'id="navAgents"' in html
    assert 'id="screenAgents"' in html
    assert 'id="agentEditorModal"' in html
    assert "role === 'operator' || role === 'administrator'" in javascript
    assert "/api/agents/emergency-stop" in javascript
    assert "/api/agents/${agentId}/run" in javascript
    assert "['reviewer', 'Reviewer']" not in javascript

    user_button = stylesheet.split(".contribute-btn {", 1)[1].split("}", 1)[0]
    admin_button = stylesheet.split(".admin-sidebar-button {", 1)[1].split("}", 1)[0]
    for declaration in (
        "width: 100%",
        "padding: 9px",
        "background: transparent",
        "border: 1px solid transparent",
        "border-radius: var(--radius-sm)",
        "color: var(--text-secondary)",
        "font-size: 12px",
        "font-weight: 400",
        "font-family: 'Inter', sans-serif",
        "cursor: pointer",
        "transition: all var(--transition-base)",
        "display: flex",
        "align-items: center",
        "gap: 8px",
    ):
        assert declaration in user_button
        assert declaration in admin_button


def test_admin_dashboard_uses_isolated_item_and_table_components() -> None:
    html = (ROOT / "src" / "static" / "admin.html").read_text(encoding="utf-8")
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "src" / "static" / "styles.css").read_text(encoding="utf-8")

    dashboard_html = html.split('id="screenHome"', 1)[1].split('id="screenUsers"', 1)[0]
    status_renderer = javascript.split("function updateSyncStatusCard", 1)[1].split(
        "async function loadSyncHistory", 1
    )[0]
    history_renderer = javascript.split("async function loadSyncHistory", 1)[1].split(
        "// Pending changes", 1
    )[0]

    assert "admin-dashboard-page" in dashboard_html
    assert "admin-dashboard-page-header" in dashboard_html
    assert "admin-dashboard-section" in dashboard_html
    assert "admin-card" not in dashboard_html
    assert "admin-dashboard-item-media" in status_renderer
    assert "admin-dashboard-item-content" in status_renderer
    assert "admin-dashboard-item-actions" in status_renderer
    assert '<table class="admin-dashboard-table">' in history_renderer
    assert "<thead>" in history_renderer
    assert "<tbody>${rows}</tbody>" in history_renderer
    assert "history.slice(-10).reverse()" in history_renderer
    assert ".admin-dashboard-item" in stylesheet
    assert ".admin-dashboard-table" in stylesheet
    assert ".admin-dashboard-item:hover" not in stylesheet
    assert ".admin-dashboard-table tbody tr:hover" not in stylesheet
    assert ".admin-dashboard-primary-action:hover" in stylesheet
    assert "#usersCard" in stylesheet


def test_revocation_uses_typed_frontend_confirmation_dialog() -> None:
    html = (ROOT / "src" / "static" / "admin.html").read_text(encoding="utf-8")
    javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "src" / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'id="revokeUserModal" role="dialog" aria-modal="true"' in html
    assert 'id="revokeUserConfirmInput"' in html
    assert 'id="revokeUserConfirmBtn"' in html
    assert "Type <strong>Confirm</strong> to continue" in html
    assert "onclick=\"openRevokeUserModal('${user.id}', this)\"" in javascript
    assert "input.value.trim() === 'Confirm'" in javascript
    assert "async function confirmRevokeUserAction()" in javascript
    assert ".revoke-confirm-btn:disabled" in stylesheet


def test_admin_key_gate_is_only_shown_for_bootstrap() -> None:
    html = (ROOT / "src" / "static" / "admin.html").read_text(encoding="utf-8")
    admin_javascript = (ROOT / "src" / "static" / "admin.js").read_text(encoding="utf-8")
    login_javascript = (ROOT / "src" / "static" / "login.js").read_text(encoding="utf-8")

    assert 'class="auth-gate" id="authGate" style="display:none"' in html
    assert "/api/admin/bootstrap/status" in admin_javascript
    assert "if (bootstrapStatus.bootstrap_required)" in admin_javascript
    assert "redirectToAdminLogin();" in admin_javascript
    assert "destination === '/admin' ? '/admin' : '/'" in login_javascript
