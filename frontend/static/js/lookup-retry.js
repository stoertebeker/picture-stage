/* Bounded token lookup continuation. Loaded externally to retain strict CSP. */
(function () {
    if (window.__psLookupRetry) return;
    window.__psLookupRetry = true;

    if (document.querySelector('[data-lookup-wait]')) {
        setTimeout(() => window.location.reload(), 1000);
    }

    // HTMX retains the original query parameters, headers and swap target.
    // A generic 503 never authorizes replaying a request.
    document.addEventListener('htmx:beforeSwap', (event) => {
        const detail = event.detail;
        if (detail.xhr.status !== 503 ||
            detail.xhr.getResponseHeader('X-Picture-Stage-Lookup') !== 'before-effects') return;
        detail.shouldSwap = false;
        detail.isError = false;
        const config = detail.requestConfig;
        if (!config || config.verb.toLowerCase() !== 'get') return;
        const target = detail.target;
        const source = config.elt;
        setTimeout(() => {
            if (!target.isConnected || !source.isConnected) return;
            htmx.ajax(config.verb, config.path, {
                source, target, values: config.parameters, headers: config.headers,
                swap: source.getAttribute('hx-swap') || 'innerHTML',
            });
        }, 1000);
    });

    // Preserve a password and its CSRF proof in memory while windows advance.
    // Only this form uses fetch: its ordinary response remains a full document.
    document.addEventListener('submit', async (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-lookup-password')) return;
        event.preventDefault();
        if (form.dataset.lookupPending) return;
        form.dataset.lookupPending = 'true';
        const body = new URLSearchParams(new FormData(form));
        try {
            const response = await guestLookupFetch(form.action, {method: 'POST', body});
            if (response.ok) {
                // The verified session is already in its HttpOnly cookie. A
                // normal GET initializes all viewer scripts in a new document.
                const url = new URL(form.action);
                url.pathname = url.pathname.replace(/\/verify-password$/, '');
                window.location.replace(url.href);
            } else if (response.status === 401) {
                const page = new DOMParser().parseFromString(await response.text(), 'text/html');
                document.querySelector('main').replaceWith(page.querySelector('main'));
            } else {
                const message = document.createElement('p');
                message.setAttribute('role', 'alert');
                message.textContent = `Request failed (${response.status})`;
                form.append(message);
            }
        } catch (_error) {
            // Allow a deliberate resubmission after a transport failure.
        } finally {
            delete form.dataset.lookupPending;
        }
    });
})();
