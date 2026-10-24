import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

// Playwright against the local Compose app, using the installed Chrome binary.
// Install Playwright locally, or separately without changing project dependencies:
// npm install --prefix /tmp/picture-stage-playwright --no-save playwright
// PLAYWRIGHT_MODULE=/tmp/picture-stage-playwright/node_modules/playwright/index.mjs \
//   E2E_PASSWORD=... node tests/e2e/dashboard_delete_browser.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
const password = process.env.E2E_PASSWORD;
assert.ok(password, 'E2E_PASSWORD is required');
const browser = await chromium.launch({
    executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome',
    headless: true,
});
const context = await browser.newContext({
    baseURL: baseUrl,
    locale: 'de-DE',
    viewport: { width: 1280, height: 900 },
});
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
});
const galleries = [];
async function screenshot(name) {
    if (process.env.E2E_SCREENSHOTS !== '1') return;
    await mkdir('.playwright', { recursive: true });
    await page.screenshot({ path: `.playwright/${name}.png` });
}
async function createGallery(name) {
    await page.locator('[data-open-dialog=createModal]').first().click();
    const form = page.locator('form[action="/dashboard/galleries"]');
    await form.locator('[name=name]').fill(name);
    await form.locator('button[type=submit]').click();
    const link = page.locator('#gallery-grid h2 a').filter({ hasText: name });
    await link.waitFor();
    const path = await link.getAttribute('href');
    const gallery = { name, path, dialogId: `deleteModal-${path.split('/').pop()}` };
    galleries.push(gallery);
    await page.locator('#createModal').waitFor({ state: 'hidden' });
    return gallery;
}
async function openDelete(gallery) {
    await page.locator(`[data-open-dialog="${gallery.dialogId}"]`).click();
    const dialog = page.locator(`#${gallery.dialogId}`);
    await dialog.waitFor({ state: 'visible' });
    assert.equal(await page.locator('dialog[open]').count(), 1);
    assert.equal(await dialog.locator('strong').textContent(), gallery.name);
    assert.equal(await dialog.locator('input:not([type=hidden])').count(), 0, 'No name entry is required');
    assert.equal(await dialog.locator('button[type=submit]').isEnabled(), true);
    assert.equal(await dialog.locator('[data-close-dialog]').evaluate((button) => button === document.activeElement), true);
    return dialog;
}
async function submitDelete(gallery) {
    const responsePromise = page.waitForResponse((response) =>
        response.url() === `${baseUrl}${gallery.path}/delete` && response.request().method() === 'POST');
    await page.locator(`#${gallery.dialogId} button[type=submit]`).click();
    const response = await responsePromise;
    assert.equal(response.status(), 303);
    await page.waitForURL((url) => url.pathname === '/dashboard');
    await page.waitForLoadState();
    assert.equal(await page.locator(`#${gallery.dialogId}`).count(), 0);
    const deleted = await context.request.get(gallery.path);
    assert.equal(deleted.status(), 404);
}
try {
    await page.goto('/login');
    const login = page.locator('form[action="/login"]');
    await login.locator('[name=email]').fill(process.env.E2E_EMAIL || 'testadmin@example.com');
    await login.locator('[name=password]').fill(password);
    await login.locator('button[type=submit]').click();
    await page.waitForURL((url) => url.pathname === '/dashboard');
    const stamp = Date.now();
    const first = await createGallery(`Dashboard E2E ${stamp} O'Brien "A" <b> & mehr`);
    const second = await createGallery(`Dashboard E2E ${stamp} B`);
    const firstDialog = await openDelete(first);
    await screenshot('dashboard-delete-desktop');
    await firstDialog.locator('[data-close-dialog]').click();
    await firstDialog.waitFor({ state: 'hidden' });
    assert.equal((await context.request.get(first.path)).status(), 200, 'Cancel keeps the gallery');
    await openDelete(first);
    await page.keyboard.press('Escape');
    await firstDialog.waitFor({ state: 'hidden' });
    assert.equal((await context.request.get(first.path)).status(), 200, 'Escape keeps the gallery');
    await openDelete(first);
    // Opening the dialog alone must not send the destructive POST.
    assert.equal((await context.request.get(first.path)).status(), 200);
    await submitDelete(first);
    assert.equal(await page.locator(`#${second.dialogId}`).count(), 1, 'The other gallery remains');

    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(() => localStorage.setItem('theme', 'light'));
    await page.goto(second.path);
    const detail = { ...second, dialogId: 'deleteModal' };
    const detailDialog = await openDelete(detail);
    await detailDialog.locator('[data-close-dialog]').click();
    await detailDialog.waitFor({ state: 'hidden' });
    assert.equal((await context.request.get(second.path)).status(), 200);
    await page.goto('/dashboard');
    await openDelete(second);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await screenshot('dashboard-delete-mobile');
    await submitDelete(second);
    // A second real submission from the detail view exercises both entry points.
    const third = await createGallery(`Dashboard E2E ${stamp} Detail`);
    await page.goto(third.path);
    await openDelete({ ...third, dialogId: 'deleteModal' });
    await submitDelete({ ...third, dialogId: 'deleteModal' });
    // 404 checks above deliberately produce network errors; Alpine/runtime errors must be absent.
    assert.deepEqual(errors.filter((message) => !message.includes('404 (Not Found)')), []);
    console.log('Playwright passed: real clicks, Escape/cancel, no name input, HTMX cards, escaped names, dashboard/detail deletion, desktop/mobile and dark/light.');
} finally {
    try {
        if (galleries.length > 0) {
            await page.goto('/dashboard');
            const csrf = await page.locator('meta[name=csrf-token]').getAttribute('content');
            for (const gallery of galleries) {
                const response = await context.request.post(`${gallery.path}/delete`, {
                    form: { confirm_delete: 'yes', csrf_token: csrf },
                });
                assert.ok([200, 404].includes(response.status()), `Cleanup failed for ${gallery.path}`);
            }
        }
    } finally {
        await browser.close();
    }
}
