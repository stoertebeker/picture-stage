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
