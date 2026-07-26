(function initializeToastManager() {
    const DEFAULT_DURATION = 5000;
    const EXIT_DURATION = 180;
    let nextToastId = 0;
    const toastElements = new Map();
    const toastTimers = new Map();

    const typeIcons = {
        success: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4.5 10.5 3.25 3.25 7.75-8" /></svg>',
        info: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="M10 9v5M10 6.25h.01" /></svg>',
        warning: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8.7 3.25 2.2 14.5A1.5 1.5 0 0 0 3.5 16.75h13a1.5 1.5 0 0 0 1.3-2.25L11.3 3.25a1.5 1.5 0 0 0-2.6 0Z" /><path d="M10 7.25v4.25M10 14h.01" /></svg>',
        error: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="m7.5 7.5 5 5M12.5 7.5l-5 5" /></svg>',
        loading: '<svg class="shadcn-toast-spinner" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 3a7 7 0 0 1 7 7" /></svg>',
    };

    function getViewport() {
        let viewport = document.getElementById('toastContainer');
        if (!viewport) {
            viewport = document.createElement('div');
            viewport.id = 'toastContainer';
            document.body.appendChild(viewport);
        }
        viewport.className = 'shadcn-toast-viewport';
        viewport.setAttribute('role', 'region');
        viewport.setAttribute('aria-label', 'Notifications');
        return viewport;
    }

    function clearToastTimer(id) {
        const timer = toastTimers.get(id);
        if (timer) window.clearTimeout(timer);
        toastTimers.delete(id);
    }

    function scheduleClose(id, duration) {
        clearToastTimer(id);
        if (duration === Infinity || duration <= 0) return;
        toastTimers.set(id, window.setTimeout(() => close(id), duration));
    }

    function close(id) {
        const element = toastElements.get(id);
        if (!element) return;
        clearToastTimer(id);
        element.dataset.state = 'closed';
        window.setTimeout(() => {
            element.remove();
            toastElements.delete(id);
        }, EXIT_DURATION);
    }

    function appendTextContent(parent, className, value) {
        if (value === undefined || value === null || value === '') return;
        const element = document.createElement('div');
        element.className = className;
        element.textContent = String(value);
        parent.appendChild(element);
    }

    function render(id, options, isUpdate = false) {
        const type = options.type || 'info';
        let element = toastElements.get(id);
        if (!element) {
            element = document.createElement('div');
            toastElements.set(id, element);
            getViewport().appendChild(element);
        }

        element.className = `shadcn-toast shadcn-toast-${type}`;
        element.dataset.toastId = id;
        element.dataset.state = isUpdate ? 'updating' : 'open';
        element.setAttribute('role', type === 'error' ? 'alert' : 'status');
        element.setAttribute('aria-live', options.priority === 'high' || type === 'error' ? 'assertive' : 'polite');
        element.replaceChildren();

        const icon = document.createElement('span');
        icon.className = 'shadcn-toast-icon';
        icon.innerHTML = typeIcons[type] || typeIcons.info;
        element.appendChild(icon);

        const content = document.createElement('div');
        content.className = 'shadcn-toast-content';
        appendTextContent(content, 'shadcn-toast-title', options.title);
        appendTextContent(content, 'shadcn-toast-description', options.description);
        element.appendChild(content);

        if (options.actionProps && options.actionProps.children) {
            const action = document.createElement('button');
            action.type = 'button';
            action.className = 'shadcn-toast-action';
            action.textContent = String(options.actionProps.children);
            action.addEventListener('click', event => options.actionProps.onClick?.(event));
            element.appendChild(action);
        }

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'shadcn-toast-close';
        closeButton.setAttribute('aria-label', 'Close notification');
        closeButton.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8" /></svg>';
        closeButton.addEventListener('click', () => close(id));
        element.appendChild(closeButton);

        window.requestAnimationFrame(() => {
            if (element.dataset.state === 'updating') element.dataset.state = 'open';
        });
        scheduleClose(id, options.duration ?? (type === 'loading' ? Infinity : DEFAULT_DURATION));
        return id;
    }

    function add(options) {
        const normalized = typeof options === 'string' ? { description: options } : { ...options };
        const id = `toast-${Date.now()}-${++nextToastId}`;
        return render(id, normalized);
    }

    function update(id, options) {
        if (!toastElements.has(id)) return add(options);
        return render(id, { ...options }, true);
    }

    function resolvePromiseState(state, value, type) {
        const resolved = typeof state === 'function' ? state(value) : state;
        if (typeof resolved === 'string') return { type, description: resolved };
        return { ...(resolved || {}), type: resolved?.type || type };
    }

    function promise(promiseOrFactory, states) {
        const id = add(resolvePromiseState(states.loading, undefined, 'loading'));
        let operation;
        try {
            operation = typeof promiseOrFactory === 'function' ? promiseOrFactory() : promiseOrFactory;
        } catch (error) {
            update(id, resolvePromiseState(states.error, error, 'error'));
            return Promise.reject(error);
        }

        return Promise.resolve(operation).then(
            value => {
                update(id, resolvePromiseState(states.success, value, 'success'));
                return value;
            },
            error => {
                update(id, resolvePromiseState(states.error, error, 'error'));
                throw error;
            },
        );
    }

    window.toast = Object.freeze({ add, close, promise, update });
    window.showToast = (message, type = 'info') => add({ type, description: message });
})();
