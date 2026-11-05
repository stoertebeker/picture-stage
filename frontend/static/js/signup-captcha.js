// Use only the external ALTCHA distribution and a same-origin worker. Relative
// module URLs preserve ASGI root_path and never require blob: or a public CDN.
const container = document.querySelector('[data-signup-captcha]');
if (container) {
    const form = container.closest('form');
    const widget = container.querySelector('altcha-widget');
    const status = container.querySelector('[data-captcha-status]');
    let verified = false;
    form.addEventListener('submit', (event) => {
        if (!verified) {
            event.preventDefault();
            status.textContent = status.dataset.error;
        }
    });
    widget.addEventListener('statechange', (event) => {
        verified = event.detail.state === 'verified' && Boolean(event.detail.payload);
        status.hidden = verified;
        if (event.detail.state === 'error') status.textContent = status.dataset.error;
    });
    try {
        if (!window.isSecureContext || !window.crypto?.subtle || !window.Worker) {
            throw new Error('Browser verification unavailable');
        }
        await import('../vendor/altcha-3.5.0/dist/external/altcha.js');
        window.$altcha.algorithms.set('PBKDF2/SHA-256', () => new Worker(
            new URL('../vendor/altcha-3.5.0/dist/workers/pbkdf2.js', import.meta.url),
        ));
        await import('../vendor/altcha-3.5.0/dist/i18n/de.js');
    } catch {
        status.textContent = status.dataset.error;
    }
}
