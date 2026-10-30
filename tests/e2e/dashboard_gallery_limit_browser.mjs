import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

// Use a dedicated, empty local test account with gallery_limit_override=1.
// PLAYWRIGHT_MODULE=/tmp/picture-stage-playwright/node_modules/playwright/index.mjs \
//   E2E_EMAIL=... E2E_PASSWORD=... node tests/e2e/dashboard_gallery_limit_browser.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
assert.ok(process.env.E2E_EMAIL && process.env.E2E_PASSWORD, 'Dedicated E2E_EMAIL and E2E_PASSWORD are required');
const browser = await chromium.launch({ executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome', headless: true });
const context = await browser.newContext({ baseURL, locale: 'de-DE', viewport: { width: 1280, height: 900 } });
const page = await context.newPage();
const errors = [];
const galleries = new Set();
function observe(tab) {
    tab.on('pageerror', (error) => errors.push(error.message));
    tab.on('console', (message) => {
        if (message.type() !== 'error') return;
        // The app has no favicon; its unrelated browser request is a known 404.
        if (new URL(message.location().url || baseURL, baseURL).pathname === '/favicon.ico') return;
        errors.push(message.text());
    });
}
observe(page);
async function openCreate(tab, name) {
    await tab.locator('#gallery-create-controls [data-open-dialog=createModal]').click();
    await tab.locator('#createModal [name=name]').fill(name);
}
async function create(tab, name) {
    await openCreate(tab, name);
    const response = tab.waitForResponse((r) => r.url() === `${baseURL}/dashboard/galleries` && r.request().method() === 'POST');
    await tab.locator('#createModal button[type=submit]').click();
    assert.equal((await response).status(), 200);
    await tab.locator('#createModal').waitFor({ state: 'hidden' });
    const link = tab.locator('#gallery-grid h2 a').filter({ hasText: name });
    await link.waitFor();
    const path = await link.getAttribute('href');
    galleries.add(path);
    return path;
}
async function assertLimited(tab, text) {
    await tab.locator('#gallery-limit-notice').waitFor();
    assert.ok((await tab.locator('#gallery-limit-notice').innerText()).includes(text));
    assert.equal(await tab.locator('#gallery-create-controls [data-open-dialog=createModal]').isDisabled(), true);
    assert.equal(await tab.locator('#gallery-create-controls').count(), 1);
    assert.equal(await tab.locator('#gallery-grid #gallery-create-controls').count(), 0, 'OOB controls are outside the card grid');
    assert.equal(await tab.locator('#dashboard-gallery-count').innerText(), '1');
    assert.equal(await tab.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}
async function deleteGallery(tab, path) {
    const dialog = `deleteModal-${path.split('/').pop()}`;
    await tab.locator(`[data-open-dialog="${dialog}"]`).click();
    await tab.locator(`#${dialog} button[type=submit]`).click();
    await tab.waitForURL((url) => url.pathname === '/dashboard');
    await tab.locator(`#${dialog}`).waitFor({ state: 'detached' });
    assert.equal(await tab.locator('#gallery-limit-notice').count(), 0);
    assert.equal(await tab.locator('#gallery-create-controls [data-open-dialog=createModal]').isEnabled(), true);
    assert.equal(await tab.locator('#dashboard-gallery-count').innerText(), '0');
    galleries.delete(path);
}
async function screenshot(name) {
    if (process.env.E2E_SCREENSHOTS !== '1') return;
    await mkdir('.playwright', { recursive: true });
    await page.screenshot({ path: `.playwright/${name}.png` });
}
try {
    await page.goto('/login');
    await page.locator('[name=email]').fill(process.env.E2E_EMAIL);
    await page.locator('[name=password]').fill(process.env.E2E_PASSWORD);
    await page.locator('form[action="/login"] button[type=submit]').click();
    await page.waitForURL((url) => url.pathname === '/dashboard');
    assert.equal(await page.locator('#gallery-grid h2 a').count(), 0, 'Test account must start empty');
    await page.evaluate(() => { window.galleryLimitNoReload = true; });
    const first = await create(page, `Limit E2E ${Date.now()} DE`);
    await assertLimited(page, 'Galerie-Limit erreicht (1)');
    assert.equal(await page.evaluate(() => window.galleryLimitNoReload), true, 'Successful HTMX creation does not reload');
    await page.waitForTimeout(5500); // Notice must outlive the five-second success toast.
    await assertLimited(page, 'Lösche eine bestehende Galerie');
    await screenshot('dashboard-limit-desktop');
    await page.reload();
    await assertLimited(page, 'Galerie-Limit erreicht (1)');
    await deleteGallery(page, first);

    await page.setViewportSize({ width: 375, height: 812 });
    await page.evaluate(() => localStorage.setItem('theme', 'light'));
    await context.request.get('/set-lang/en');
    await page.goto('/dashboard');
    const second = await create(page, `Limit E2E ${Date.now()} EN`);
    await assertLimited(page, 'Gallery limit reached (1)');
    await page.locator('[data-toast-kind=success]').waitFor({ state: 'hidden' });
    await screenshot('dashboard-limit-mobile');
    await deleteGallery(page, second);

    // Leave this tab's dialog open, then consume the last slot in a second tab.
    const rejectedName = `Limit E2E ${Date.now()} rejected`;
    await openCreate(page, rejectedName);
    await page.evaluate(() => { window.galleryLimitNoReload = true; });
    const other = await context.newPage();
    observe(other);
    await other.goto('/dashboard');
    const third = await create(other, `Limit E2E ${Date.now()} other tab`);
    await assertLimited(other, 'Gallery limit reached (1)');
    const rejected = page.waitForResponse((r) => r.url() === `${baseURL}/dashboard/galleries` && r.request().method() === 'POST');
    await page.locator('#createModal button[type=submit]').click();
    const rejectedResponse = await rejected;
    assert.equal(rejectedResponse.headers()['hx-refresh'], 'true');
    assert.equal(rejectedResponse.headers()['hx-trigger'], undefined);
    await page.waitForFunction(() => !window.galleryLimitNoReload);
    await assertLimited(page, 'Gallery limit reached (1)');
    assert.equal(await page.locator('#gallery-grid h2 a').count(), 1);
    assert.equal(await page.getByText(rejectedName, { exact: true }).count(), 0);
    assert.equal(await page.locator('[data-toast-kind=success]').count(), 0, 'Rejected creation has no success toast');
    await deleteGallery(page, third);
    assert.deepEqual(errors, []);
    console.log('Playwright PASS: DE desktop/dark, EN mobile/light, persistent notice, OOB last-slot update, delete reset, stale second tab.');
} finally {
    for (const path of galleries) {
        const csrf = (await context.cookies()).find((cookie) => cookie.name === 'csrf_token')?.value;
        await context.request.post(`${path}/delete`, { form: { confirm_delete: 'yes', csrf_token: csrf || '' } });
    }
    await browser.close();
}
