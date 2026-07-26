from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "static"


def test_frontends_load_shared_toast_manager_before_page_scripts() -> None:
    index_html = (STATIC / "index.html").read_text(encoding="utf-8")
    admin_html = (STATIC / "admin.html").read_text(encoding="utf-8")

    assert index_html.index("/static/toast.js") < index_html.index("/static/app.js")
    assert admin_html.index("/static/toast.js") < admin_html.index("/static/admin.js")
    assert 'id="toastContainer"' in index_html
    assert 'id="toastContainer"' in admin_html


def test_toast_manager_matches_shadcn_typed_and_promise_api() -> None:
    javascript = (STATIC / "toast.js").read_text(encoding="utf-8")

    for toast_type in ("success", "info", "warning", "error", "loading"):
        assert f"{toast_type}: '<svg" in javascript
    assert "function add(options)" in javascript
    assert "function close(id)" in javascript
    assert "function update(id, options)" in javascript
    assert "function promise(promiseOrFactory, states)" in javascript
    assert "window.toast = Object.freeze({ add, close, promise, update });" in javascript
    assert "appendTextContent" in javascript
    assert "element.textContent = String(value);" in javascript
    assert "aria-live" in javascript
    assert "Close notification" in javascript


def test_toast_styles_are_isolated_responsive_and_accessible() -> None:
    stylesheet = (STATIC / "styles.css").read_text(encoding="utf-8")

    assert ".shadcn-toast-viewport" in stylesheet
    assert ".shadcn-toast-content" in stylesheet
    assert ".shadcn-toast-action" in stylesheet
    assert ".shadcn-toast-close:focus-visible" in stylesheet
    assert ".shadcn-toast-loading" in stylesheet
    assert "@media (max-width: 560px)" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "\n.toast {" not in stylesheet
    assert ".toast.success" not in stylesheet


def test_async_mutations_use_promise_toasts_while_validation_is_immediate() -> None:
    app_javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    admin_javascript = (STATIC / "admin.js").read_text(encoding="utf-8")

    assert app_javascript.count("toast.promise(") >= 3
    assert admin_javascript.count("toast.promise(") >= 10
    assert "function showToast(" not in app_javascript
    assert "function showToast(" not in admin_javascript
    assert "showToast('Please enter your name', 'warning')" in app_javascript
    assert "showToast('Please select a role before approving', 'warning')" in admin_javascript
    assert "loading: 'Starting knowledge sync...'" in admin_javascript
    assert "loading: 'Submitting contribution for review...'" in app_javascript
