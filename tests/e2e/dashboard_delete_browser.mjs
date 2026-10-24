import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';

// Run against the local Compose app and a disposable Chrome/CDP instance:
// E2E_PASSWORD=... node tests/e2e/dashboard_delete_browser.mjs
const debugBase = process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222';
const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
const email = process.env.E2E_EMAIL || 'testadmin@example.com';
const password = process.env.E2E_PASSWORD;
assert.ok(password, 'E2E_PASSWORD is required');
const targets = await fetch(`${debugBase}/json/list`).then((response) => response.json());
const target = targets.find((item) => item.type === 'page');
assert.ok(target, 'Chrome must expose a page target');
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
});
let commandId = 0;
const pending = new Map();
const exceptions = [];
socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') {
        exceptions.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
        exceptions.push(message.params.args.map((arg) => arg.description || arg.value).join(' '));
    }
    if (!pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
});
function command(method, params = {}) {
    commandId += 1;
    return new Promise((resolve, reject) => {
        pending.set(commandId, { resolve, reject });
        socket.send(JSON.stringify({ id: commandId, method, params }));
    });
}
async function evaluate(expression, awaitPromise = false) {
    const result = await command('Runtime.evaluate', { expression, awaitPromise, returnByValue: true, userGesture: true });
    if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    }
    return result.result.value;
}
async function waitFor(expression, description) {
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
        if (await evaluate(expression)) return;
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const state = await evaluate(`[...document.querySelectorAll('dialog[open]')].map((dialog) => ({
        id: dialog.id,
        input: dialog.querySelector('[name=confirm_name]')?.value,
        state: window.Alpine ? JSON.stringify(window.Alpine.$data(dialog.parentElement)) : null,
    }))`);
    await screenshot('dashboard-delete-failure');
    throw new Error(`Timed out waiting for ${description}: ${JSON.stringify({ state, exceptions })}`);
}
async function navigate(path) {
    await command('Page.navigate', { url: `${baseUrl}${path}` });
    await waitFor(`location.pathname === ${JSON.stringify(path)} && document.readyState === 'complete'`, path);
}
async function click(selector) {
    await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
}
async function screenshot(name) {
    if (process.env.E2E_SCREENSHOTS !== '1') return;
    await mkdir('.playwright', { recursive: true });
    const result = await command('Page.captureScreenshot', { format: 'png' });
    await writeFile(`.playwright/${name}.png`, Buffer.from(result.data, 'base64'));
}
async function setConfirmation(dialogId, value) {
    await evaluate(`(() => {
        const input = document.getElementById(${JSON.stringify(dialogId)}).querySelector('[name=confirm_name]');
        input.value = ${JSON.stringify(value)};
        input.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
}
const galleries = [];
async function createGallery(name) {
    await click('[data-open-dialog=createModal]');
    await evaluate(`(() => {
        const form = document.querySelector('form[action="/dashboard/galleries"]');
        form.elements.name.value = ${JSON.stringify(name)};
        form.requestSubmit();
    })()`);
    await waitFor(`[...document.querySelectorAll('#gallery-grid h2 a')]
        .some((link) => link.textContent.trim() === ${JSON.stringify(name)})`, 'HTMX-created card');
    const path = await evaluate(`[...document.querySelectorAll('#gallery-grid h2 a')]
        .find((link) => link.textContent.trim() === ${JSON.stringify(name)}).getAttribute('href')`);
    const gallery = { name, path, dialogId: `deleteModal-${path.split('/').pop()}` };
    galleries.push(gallery);
    await waitFor("!document.getElementById('createModal').open", 'creation dialog closed');
    return gallery;
}
async function openDelete(gallery) {
    await click(`[data-open-dialog="${gallery.dialogId}"]`);
    await waitFor(`document.getElementById('${gallery.dialogId}').open`, 'delete dialog');
    assert.equal(await evaluate(`document.querySelectorAll('dialog[open]').length`), 1);
    assert.equal(await evaluate(`document.getElementById('${gallery.dialogId}').querySelector('strong').textContent`), gallery.name);
    assert.equal(await evaluate(`document.getElementById('${gallery.dialogId}').querySelector('button[type=submit]').disabled`), true);
}
async function submitDelete(gallery) {
    await setConfirmation(gallery.dialogId, gallery.name);
    await waitFor(`!document.getElementById('${gallery.dialogId}').querySelector('button[type=submit]').disabled`, 'matching name');
    await click(`#${gallery.dialogId} button[type=submit]`);
    await waitFor(`location.pathname === '/dashboard' && document.readyState === 'complete'
        && !document.getElementById('${gallery.dialogId}')`, 'dashboard after deletion');
    assert.equal(await evaluate(`fetch(${JSON.stringify(gallery.path)}).then((r) => r.status)`, true), 404);
}
try {
    await command('Page.enable');
    await command('Page.bringToFront');
    await command('Emulation.setFocusEmulationEnabled', { enabled: true });
    await command('Runtime.enable');
    await command('Network.enable');
    await command('Network.clearBrowserCookies');
    await command('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await navigate('/login');
    await evaluate(`(() => {
        const form = document.querySelector('form[action="/login"]');
        form.elements.email.value = ${JSON.stringify(email)};
        form.elements.password.value = ${JSON.stringify(password)};
        form.requestSubmit();
    })()`);
    await waitFor("location.pathname === '/dashboard' && !!window.Alpine && document.readyState === 'complete'", 'login');
    const stamp = Date.now();
    const first = await createGallery(`Dashboard E2E ${stamp} O'Brien "A" <b> & mehr`);
    const second = await createGallery(`Dashboard E2E ${stamp} B`);
    await openDelete(first);
    await setConfirmation(first.dialogId, second.name);
    assert.equal(await evaluate(`document.getElementById('${first.dialogId}').querySelector('button[type=submit]').disabled`), true);
    await setConfirmation(first.dialogId, first.name);
    await waitFor(`!document.getElementById('${first.dialogId}').querySelector('button[type=submit]').disabled`, 'enabled confirmation');
    await screenshot('dashboard-delete-desktop');
    await click(`#${first.dialogId} [data-close-dialog]`);
    assert.equal(await evaluate(`document.getElementById('${first.dialogId}').open`), false);
    assert.equal(await evaluate(`fetch(${JSON.stringify(first.path)}).then((r) => r.status)`, true), 200);
    await openDelete(first);
    assert.equal(await evaluate(`document.getElementById('${first.dialogId}').querySelector('[name=confirm_name]').value`), '');
    await evaluate(`document.getElementById('${first.dialogId}').requestClose()`);
    await waitFor(`!document.getElementById('${first.dialogId}').open`, 'native cancel closes dialog');
    // The same partial also serves the existing gallery-detail delete action.
    await navigate(first.path);
    const detailGallery = { ...first, dialogId: 'deleteModal' };
    await openDelete(detailGallery);
    await setConfirmation(detailGallery.dialogId, first.name);
    await waitFor("!document.getElementById('deleteModal').querySelector('button[type=submit]').disabled", 'detail name confirmation');
    await evaluate("document.getElementById('deleteModal').querySelector('[data-close-dialog]').click()");
    await navigate('/dashboard');
    await openDelete(first);
    await submitDelete(first);
    assert.equal(await evaluate(`!!document.getElementById('${second.dialogId}')`), true, 'Only the chosen gallery is deleted');
    await command('Emulation.setDeviceMetricsOverride', { width: 375, height: 812, deviceScaleFactor: 1, mobile: true });
    await evaluate("localStorage.setItem('theme', 'light')");
    await navigate('/dashboard');
    await openDelete(second);
    assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, 'No mobile horizontal overflow');
    await screenshot('dashboard-delete-mobile');
    await submitDelete(second);
    assert.deepEqual(exceptions, [], 'No Alpine/CSP or other browser errors');
    console.log('Dashboard delete passed: HTMX cards, independent dialogs, escaped names, cancel/native close/reset, desktop/mobile, light/dark, native form deletion and cleanup.');
} finally {
    try {
        // Idempotent cleanup touches only the dedicated galleries created above.
        await navigate('/dashboard');
        for (const gallery of galleries) {
            await evaluate(`fetch(${JSON.stringify(`${gallery.path}/delete`)}, {
                method: 'POST',
                body: new URLSearchParams({
                    confirm_name: ${JSON.stringify(gallery.name)},
                    csrf_token: document.querySelector('meta[name=csrf-token]').content,
                }),
            }).then((r) => r.status)`, true);
        }
    } finally {
        socket.close();
    }
}
