import assert from 'node:assert/strict';

// Exercise actual FastAPI HTML handlers against a disposable seeded gallery.
// E2E_BASE_URL=http://... E2E_GUEST_TOKEN=... E2E_GUEST_PASSWORD=... \
// PLAYWRIGHT_MODULE=... node tests/e2e/protected_gallery_lookup_browser.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
assert.ok(process.env.E2E_GUEST_TOKEN && process.env.E2E_GUEST_PASSWORD);
const path = `/g/${encodeURIComponent(process.env.E2E_GUEST_TOKEN)}`;
const browser = await chromium.launch({ executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome', headless: true });
const context = await browser.newContext({ baseURL });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
try {
    await page.goto(path);
    await page.locator('[data-lookup-password] [name=password]').fill(process.env.E2E_GUEST_PASSWORD);
    const verified = page.waitForResponse((response) =>
        new URL(response.url()).pathname === path + '/verify-password' && response.status() === 200);
    const canonical = page.waitForResponse((response) =>
        new URL(response.url()).pathname === path && response.request().method() === 'GET' && response.status() === 200);
    await page.locator('[data-lookup-password] button[type=submit]').click();
    await verified;
    const viewer = await canonical;
    assert.ok(!(await viewer.text()).includes('data-lookup-password'));
    await page.waitForLoadState('load');
    assert.equal(await page.locator('[data-lookup-password]').count(), 0);
    const cookie = (await context.cookies()).find((item) => item.name.startsWith('guest_session_'));
    assert.ok(cookie && cookie.httpOnly);
    await page.reload();
    assert.equal(await page.locator('[data-lookup-password]').count(), 0);
    assert.equal((await context.cookies()).find((item) => item.name === cookie.name).value, cookie.value);
    await context.clearCookies({ name: cookie.name });
    await page.reload();
    await page.locator('[data-lookup-password]').waitFor();
    assert.deepEqual(errors, []);
    console.log('Playwright PASS: actual protected POST -> canonicalGET viewer -> stable grant reload -> missing-grant password gate.');
} finally {
    await browser.close();
}
