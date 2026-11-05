/* Theme bootstrap and toggle.
 *
 * Default = Dark (Editorial Dark first). User override persists in
 * localStorage under "theme" = "dark" | "light".
 *
 * The bootstrap IIFE runs synchronously in <head>/<body> load order
 * (before Alpine) so the initial data-theme attribute is on <html>
 * before any pixel paints — no flash of unstyled content.
 */

(function () {
    /* Idempotency guard: hx-target="body" swaps (galleries/detail.html
     * rename/status forms) re-inject and re-execute this script. Without
     * the guard every document-level listener below registers once per
     * swap — double toasts, InvalidStateError from showModal() on an
     * already-open dialog. */
    if (window.__psAppInit) return;
    window.__psAppInit = true;

    // Cookies can rotate while a tab keeps an older document alive. Resolve
    // the token when sending, rather than trusting a render-time hx-headers.
    function currentCsrfToken() {
        const cookie = document.cookie.split(';').map((part) => part.trim())
            .find((part) => part.startsWith('csrf_token='));
        return cookie ? cookie.slice('csrf_token='.length) : null;
    }

    function setCsrfHeader(headers, token) {
        // XHR combines differently-cased duplicates into one header value.
        for (const key of Object.keys(headers)) {
            if (key.toLowerCase() === 'x-csrf-token') delete headers[key];
        }
        headers['X-CSRF-Token'] = token;
    }

    function sameOrigin(path) {
        try {
            return new URL(path, document.baseURI).origin === window.location.origin;
        } catch {
            return false;
        }
    }

    function syncCsrfToken() {
        const token = currentCsrfToken();
        if (!token || !document.querySelector('meta[name="csrf-token"]')) return null;
        document.querySelectorAll('meta[name="csrf-token"]').forEach((meta) => { meta.content = token; });
        document.querySelectorAll('input[name="csrf_token"]').forEach((input) => { input.value = token; });
        document.querySelectorAll('[hx-headers]').forEach((element) => {
            try {
                const headers = JSON.parse(element.getAttribute('hx-headers'));
                if (!headers || Array.isArray(headers) || typeof headers !== 'object') return;
                if (!Object.keys(headers).some((key) => key.toLowerCase() === 'x-csrf-token')) return;
                setCsrfHeader(headers, token);
                element.setAttribute('hx-headers', JSON.stringify(headers));
            } catch {
                // Never evaluate dynamic header expressions to synchronize a token.
            }
        });
        return token;
    }

    document.addEventListener('htmx:configRequest', (event) => {
        const detail = event.detail;
        if (!detail || !sameOrigin(detail.path || window.location.href)) return;
        const token = syncCsrfToken();
        if (!token) return;
        setCsrfHeader(detail.headers, token);
        if (detail.parameters && Object.prototype.hasOwnProperty.call(detail.parameters, 'csrf_token')) {
            detail.parameters.csrf_token = token;
        }
    });
    // Capture before HTMX or the browser collects native form values.
    document.addEventListener('submit', (event) => {
        if (event.target instanceof HTMLFormElement && sameOrigin(event.target.action)) syncCsrfToken();
    }, true);
    document.addEventListener('htmx:afterSwap', syncCsrfToken);
    window.addEventListener('pageshow', (event) => {
        syncCsrfToken();
        if (event.persisted && document.body.dataset.csrfRecoveryUrl && document.querySelector('meta[name="csrf-token"]')) {
            // A restored document cannot inspect the HttpOnly session cookie.
            // Fetch its current authentication state and signed token via GET.
            const recovery = document.body.dataset.csrfRecoveryUrl || '/dashboard';
            window.location.replace(sameOrigin(recovery) ? recovery : '/dashboard');
        }
    });
    syncCsrfToken();

    (function () {
        const stored = localStorage.getItem('theme');
        const theme = stored === 'light' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', theme);
    })();

    // Reflect the current theme into aria-pressed on every toggle button so screen
    // readers expose the toggle state (aria-pressed="true" === dark mode active).
    function syncThemeToggleState() {
        const pressed = document.documentElement.getAttribute('data-theme') === 'dark';
        document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
            btn.setAttribute('aria-pressed', String(pressed));
        });
    }

    function toggleTheme() {
        const html = document.documentElement;
        const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        syncThemeToggleState();
    }

    // Wire any [data-theme-toggle] button to toggleTheme.
    function wireThemeToggles() {
        document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
            if (btn.dataset.themeWired === '1') return;
            btn.addEventListener('click', toggleTheme);
            btn.dataset.themeWired = '1';
        });
        // Align aria-pressed with the theme the IIFE restored from localStorage
        // (also covers toggles swapped in via HTMX, which re-calls this function).
        syncThemeToggleState();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wireThemeToggles);
    } else {
        wireThemeToggles();
    }

    // Re-wire after HTMX swaps in case a swapped fragment contains new toggles.
    document.addEventListener('htmx:afterSwap', wireThemeToggles);

    // HTMX Upload Progress (if htmx is loaded).
    if (typeof htmx !== 'undefined') {
        htmx.on('htmx:xhr:progress', function (evt) {
            const progress = document.getElementById('upload-progress');
            if (progress) {
                progress.setAttribute('value', (evt.detail.loaded / evt.detail.total) * 100);
            }
        });
    }

    /* Delegated dialog control (u3s — CSP-safe).
     *
     * Replaces inline Alpine `$refs.x.showModal()` / `$el.closest('dialog').close()`
     * expressions, which the @alpinejs/csp build cannot evaluate. One document-level
     * listener also covers HTMX-swapped fragments without re-wiring.
     *   - [data-open-dialog="id"]        → document.getElementById(id).showModal()
     *   - [data-close-dialog]            → nearest enclosing <dialog>.close()
     *   - <dialog data-backdrop-close>   → a click on the dialog itself (backdrop,
     *                                      since content sits in a padded inner div)
     */
    document.addEventListener('click', (e) => {
        const opener = e.target.closest('[data-open-dialog]');
        if (opener) {
            document.getElementById(opener.dataset.openDialog)?.showModal();
            return;
        }
        const closer = e.target.closest('[data-close-dialog]');
        if (closer) {
            closer.closest('dialog')?.close();
            return;
        }
        if (e.target.matches('dialog[data-backdrop-close]')) {
            e.target.close();
        }
    });

    /* Auto-open a <dialog data-auto-open> (e.g. to re-show a form that came back
     * with server-side validation errors). Replaces inline
     * x-init="$nextTick(() => $refs.x.showModal())". Runs on load and after HTMX
     * swaps that may bring in a flagged dialog.
     */
    function openAutoDialogs() {
        document.querySelectorAll('dialog[data-auto-open]').forEach((d) => {
            if (!d.open) d.showModal();
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', openAutoDialogs);
    } else {
        openAutoDialogs();
    }
    document.addEventListener('htmx:afterSwap', openAutoDialogs);

    /* Close a dialog after a successful HTMX request (replaces inline hx-on and
     * @submit="$refs.x.close()"). Put [data-close-dialog-on-success] on the form;
     * a form is also reset so it's clean when reopened.
     */
    document.addEventListener('htmx:afterRequest', (e) => {
        if (!(e.detail && e.detail.successful)) return;
        const el = e.target.closest('[data-close-dialog-on-success]');
        if (!el) return;
        el.closest('dialog')?.close();
        if (el.tagName === 'FORM') el.reset();
        // Optionally remove an element (e.g. an empty-state placeholder) on success.
        const removeId = el.dataset.removeOnSuccess;
        if (removeId) document.getElementById(removeId)?.remove();
    });

    /* Toast system (ps-ux-13).
     *
     * Triggered by:
     *   - HX-Trigger response header: {"showToast": {"kind": "...", "message": "..."}}
     *   - direct JS call: window.showToast({kind: 'success', message: '...'})
     *
     * The macro toast_container() in _macros/toast.html renders the host element
     * with id="toast-container". Toasts auto-dismiss after 5000ms unless duration
     * is overridden in the event detail.
     */
    const TOAST_KIND_CLASS = {
        success: 'bg-emerald-700/30 border border-emerald-700/60 text-emerald-100',
        info:    'bg-sky-700/30 border border-sky-700/60 text-sky-100',
        warn:    'bg-amber-700/30 border border-amber-700/60 text-amber-100',
        danger:  'bg-red-700/30 border border-red-700/60 text-red-100',
    };

    function showToast(detail) {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const kind = TOAST_KIND_CLASS[detail.kind] ? detail.kind : 'info';
        const cls = TOAST_KIND_CLASS[kind];
        const role = (kind === 'warn' || kind === 'danger') ? 'alert' : 'status';

        const el = document.createElement('div');
        el.setAttribute('role', role);
        el.setAttribute('data-toast-kind', kind);
        el.className = `pointer-events-auto flex items-start gap-3 max-w-sm px-4 py-3 rounded-md shadow-md backdrop-blur-sm ${cls}`;

        const msg = document.createElement('span');
        msg.className = 'text-sm flex-1';
        msg.textContent = detail.message || '';
        el.appendChild(msg);

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Schliessen');
        btn.className = 'text-current opacity-70 hover:opacity-100 transition-opacity';
        btn.innerHTML = '<svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 18L18 6M6 6l12 12" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        btn.addEventListener('click', () => el.remove());
        el.appendChild(btn);

        container.appendChild(el);

        const duration = typeof detail.duration === 'number' ? detail.duration : 5000;
        if (duration > 0) {
            setTimeout(() => el.remove(), duration);
        }
    }

    window.showToast = showToast;

    document.addEventListener('showToast', function (evt) {
        showToast(evt.detail || {});
    });
})();
