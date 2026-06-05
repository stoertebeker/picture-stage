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
    const stored = localStorage.getItem('theme');
    const theme = stored === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
})();

function toggleTheme() {
    const html = document.documentElement;
    const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

// Wire any [data-theme-toggle] button to toggleTheme.
function wireThemeToggles() {
    document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
        if (btn.dataset.themeWired === '1') return;
        btn.addEventListener('click', toggleTheme);
        btn.dataset.themeWired = '1';
    });
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
