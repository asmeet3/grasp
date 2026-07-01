/* ── Grasp Login/Register — Frontend Logic ─────────────────── */

const API_BASE = '';
let isRegisterMode = false;
let googleClientId = null;

// ── Theme ─────────────────────────────────────────────────

function initTheme() {
    const saved = localStorage.getItem('grasp_theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    }
}
initTheme();

// ── Initialization ────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    // If already logged in, redirect to home
    const token = localStorage.getItem('grasp_session_token');
    if (token) {
        try {
            const res = await fetch(`${API_BASE}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${token}` },
            });
            if (res.ok) {
                window.location.href = '/';
                return;
            }
        } catch (e) {
            // Token invalid, continue to login page
        }
        localStorage.removeItem('grasp_session_token');
    }

    // Check if Google sign-in is configured
    let googleConfigured = false;
    try {
        const res = await fetch(`${API_BASE}/api/auth/config`);
        const config = await res.json();
        if (config.google_enabled && config.google_client_id) {
            googleClientId = config.google_client_id;
            googleConfigured = true;
            loadGoogleScript();
        }
    } catch (e) {
        // Google config check failed — button stays visible but will show error on click
    }
});

// ── DOB Auto-Formatting ──────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    const dobInput = document.getElementById('regDob');
    if (dobInput) {
        dobInput.addEventListener('input', (e) => {
            let val = e.target.value.replace(/[^\d]/g, ''); // strip non-digits
            let formatted = '';
            if (val.length > 0) formatted += val.substring(0, 2);
            if (val.length > 2) formatted += ' / ' + val.substring(2, 4);
            if (val.length > 4) formatted += ' / ' + val.substring(4, 8);
            e.target.value = formatted;
        });
    }
});

function parseDob(dobStr) {
    /** Parse DD / MM / YYYY → { day, month, year } or null. */
    const match = dobStr.match(/^(\d{2})\s*\/\s*(\d{2})\s*\/\s*(\d{4})$/);
    if (!match) return null;
    const day = parseInt(match[1], 10);
    const month = parseInt(match[2], 10);
    const year = parseInt(match[3], 10);
    if (month < 1 || month > 12 || day < 1 || day > 31 || year < 1900 || year > new Date().getFullYear()) return null;
    // Quick validity check
    const date = new Date(year, month - 1, day);
    if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
    return { day, month, year, iso: `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}` };
}

// ── Toggle Login / Register ───────────────────────────────

function toggleAuthMode() {
    isRegisterMode = !isRegisterMode;
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const title = document.getElementById('authTitle');
    const subtitle = document.getElementById('authSubtitle');
    const toggleText = document.getElementById('toggleText');
    const toggleBtn = document.getElementById('toggleBtn');
    const googleBtnText = document.getElementById('googleBtnText');

    hideError();

    if (isRegisterMode) {
        loginForm.style.display = 'none';
        registerForm.style.display = 'block';
        title.textContent = 'Create your account';
        subtitle.textContent = 'Register to access the institutional brain';
        toggleText.textContent = 'Already have an account?';
        toggleBtn.textContent = 'Sign in';
        if (googleBtnText) googleBtnText.textContent = 'Sign up with Google';
    } else {
        loginForm.style.display = 'block';
        registerForm.style.display = 'none';
        title.textContent = 'Welcome back';
        subtitle.textContent = 'Sign in to access your organization\'s knowledge';
        toggleText.textContent = 'Don\'t have an account?';
        toggleBtn.textContent = 'Create one';
        if (googleBtnText) googleBtnText.textContent = 'Sign in with Google';
    }
    
    if (typeof renderGoogleButton === 'function') {
        renderGoogleButton();
    }
}

function showLogin() {
    isRegisterMode = false;

    // Reset all form/element visibility properly
    document.getElementById('loginForm').style.display = 'block';
    document.getElementById('registerForm').style.display = 'none';
    document.getElementById('pendingMsg').style.display = 'none';
    document.getElementById('authDivider').style.display = '';
    document.getElementById('googleSignInBtn').style.display = '';
    document.getElementById('authToggle').style.display = '';

    // Reset titles
    document.getElementById('authTitle').textContent = 'Welcome back';
    document.getElementById('authSubtitle').textContent = 'Sign in to access your organization\'s knowledge';
    const googleBtnText = document.getElementById('googleBtnText');
    if (googleBtnText) googleBtnText.textContent = 'Sign in with Google';

    hideError();
    
    if (typeof renderGoogleButton === 'function') {
        renderGoogleButton();
    }
}

// ── Error Display ─────────────────────────────────────────

function showError(message) {
    const errorEl = document.getElementById('authError');
    const textEl = document.getElementById('authErrorText');
    textEl.textContent = message;
    errorEl.style.display = 'flex';
    errorEl.classList.add('shake');
    setTimeout(() => errorEl.classList.remove('shake'), 600);
}

function hideError() {
    document.getElementById('authError').style.display = 'none';
}

// ── Pending Approval ──────────────────────────────────────

function showPending() {
    document.getElementById('loginForm').style.display = 'none';
    document.getElementById('registerForm').style.display = 'none';
    document.getElementById('authDivider').style.display = 'none';
    document.getElementById('googleSignInBtn').style.display = 'none';
    document.getElementById('authToggle').style.display = 'none';
    document.getElementById('pendingMsg').style.display = 'block';
    document.getElementById('authTitle').textContent = 'Almost there';
    document.getElementById('authSubtitle').textContent = '';
    hideError();
}

// ── Email Login ───────────────────────────────────────────

async function handleLogin(e) {
    e.preventDefault();
    hideError();

    const email = document.getElementById('loginEmail').value.trim();
    const password = document.getElementById('loginPassword').value;
    const btn = document.getElementById('loginSubmitBtn');

    if (!email || !password) {
        showError('Please fill in all fields');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Signing in...';

    try {
        const res = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });

        const data = await res.json();

        if (data.error) {
            showError(data.error);
            return;
        }

        if (data.pending) {
            showPending();
            return;
        }

        if (data.token) {
            localStorage.setItem('grasp_session_token', data.token);
            localStorage.setItem('grasp_user', JSON.stringify(data.user));
            window.location.href = '/';
        }
    } catch (e) {
        showError('Connection error. Please try again.');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Sign In';
    }
}

// ── Email Register ────────────────────────────────────────

async function handleRegister(e) {
    e.preventDefault();
    hideError();

    const firstName = document.getElementById('regFirstName').value.trim();
    const lastName = document.getElementById('regLastName').value.trim();
    const dob = document.getElementById('regDob').value;
    const email = document.getElementById('regEmail').value.trim();
    const password = document.getElementById('regPassword').value;
    const confirmPassword = document.getElementById('regConfirmPassword').value;
    const btn = document.getElementById('registerSubmitBtn');

    if (!firstName || !lastName || !dob || !email || !password || !confirmPassword) {
        showError('Please fill in all fields');
        return;
    }

    const parsedDob = parseDob(dob);
    if (!parsedDob) {
        showError('Please enter a valid date of birth in DD / MM / YYYY format');
        return;
    }

    if (password.length < 8) {
        showError('Password must be at least 8 characters');
        return;
    }

    if (password !== confirmPassword) {
        showError('Passwords do not match');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Creating account...';

    try {
        const res = await fetch(`${API_BASE}/api/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                first_name: firstName,
                last_name: lastName,
                dob: parsedDob.iso,
                email,
                password,
                confirm_password: confirmPassword,
            }),
        });

        const data = await res.json();

        if (data.error) {
            showError(data.error);
            return;
        }

        // Registration success — show pending
        showPending();
    } catch (e) {
        showError('Connection error. Please try again.');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Create Account';
    }
}

// ── Google Sign-In ────────────────────────────────────────

function loadGoogleScript() {
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.onload = initGoogleSignIn;
    document.head.appendChild(script);
}

function initGoogleSignIn() {
    if (!googleClientId || !window.google) return;

    window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: handleGoogleCredential,
    });
    
    renderGoogleButton();
}

function renderGoogleButton() {
    if (!googleClientId || !window.google) return;
    const overlay = document.getElementById('googleBtnOverlay');
    const wrapper = document.getElementById('googleSignInBtn');
    if (!overlay || !wrapper) return;

    // Render the official Google button into the invisible overlay
    overlay.innerHTML = '';

    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

    window.google.accounts.id.renderButton(
        overlay,
        { 
            theme: isDark ? 'filled_black' : 'outline',
            size: 'large',
            shape: 'rectangular',
            type: 'standard',
            text: isRegisterMode ? 'signup_with' : 'signin_with',
            width: wrapper.offsetWidth || 360,
        }
    );

    // Reveal the wrapper
    wrapper.style.display = '';
}

async function handleGoogleCredential(response) {
    hideError();

    try {
        const endpoint = isRegisterMode
            ? `${API_BASE}/api/auth/register/google`
            : `${API_BASE}/api/auth/login/google`;

        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id_token: response.credential }),
        });

        const data = await res.json();

        if (data.error) {
            showError(data.error);
            return;
        }

        if (data.pending) {
            showPending();
            return;
        }

        if (data.token) {
            localStorage.setItem('grasp_session_token', data.token);
            localStorage.setItem('grasp_user', JSON.stringify(data.user));
            window.location.href = '/';
        }
    } catch (e) {
        showError('Google sign-in failed. Please try again.');
    }
}
