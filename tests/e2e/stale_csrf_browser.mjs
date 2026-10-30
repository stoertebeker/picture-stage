import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

// Real HTMX uploads, native submits and a genuine back/forward-cache restore.
// PLAYWRIGHT_MODULE=/tmp/picture-stage-playwright/node_modules/playwright/index.mjs \
//   E2E_PASSWORD=... node tests/e2e/stale_csrf_browser.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
const email = process.env.E2E_EMAIL || 'testadmin@example.com';
const password = process.env.E2E_PASSWORD;
assert.ok(password, 'E2E_PASSWORD is required');
const browser = await chromium.launch({
    executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome',
    headless: true,
    ignoreDefaultArgs: ['--disable-back-forward-cache'],
});
const context = await browser.newContext({ baseURL, locale: 'de-DE', viewport: { width: 1280, height: 900 } });
await context.addInitScript(({ origin }) => {
    if (location.origin !== origin) return;
    sessionStorage.setItem('d18fDocumentLoads', String(Number(sessionStorage.getItem('d18fDocumentLoads')) + 1));
    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            sessionStorage.setItem('d18fRestores', String(Number(sessionStorage.getItem('d18fRestores')) + 1));
        }
    });
}, { origin: new URL(baseURL).origin });
const page = await context.newPage();
const galleries = new Set();
const errors = [];
let expectedCsrfFailure = false;
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const location = message.location().url;
    if (location.endsWith('/favicon.ico')) return;
    if (expectedCsrfFailure && location.endsWith('/upload') && message.text().includes('403')) return;
    errors.push(message.text());
});
async function cookie(name) {
    return (await context.cookies(baseURL)).find((entry) => entry.name === name)?.value;
}
async function createGallery(name) {
    await page.goto('/dashboard');
    await page.locator('[data-open-dialog=createModal]').first().click();
    await page.locator('#createModal [name=name]').fill(name);
    await page.locator('#createModal button[type=submit]').click();
    const link = page.locator('#gallery-grid h2 a').filter({ hasText: name });
    await link.waitFor();
    const path = await link.getAttribute('href');
    galleries.add(path);
    await page.goto(path);
    return path;
}
async function staleDom(token) {
    await page.evaluate((value) => {
        document.querySelector('meta[name=csrf-token]').content = value;
        document.querySelectorAll('input[name=csrf_token]').forEach((input) => { input.value = value; });
        document.body.setAttribute('hx-headers', JSON.stringify({
            'X-CSRF-Token': value, 'x-csrf-token': `${value}-duplicate`, 'X-Existing': 'preserved',
        }));
    }, token);
}
async function rotateCsrfWhileDomStaysStale() {
    await context.addCookies([{ name: 'csrf_token', value: 'legacy-cookie', url: baseURL, sameSite: 'Lax' }]);
    const refreshed = await context.request.get('/dashboard');
    assert.equal(refreshed.status(), 200);
    return cookie('csrf_token');
}
async function upload(path, label, expectedReady, status = 200) {
    const token = await cookie('csrf_token');
    const existingHeader = await page.evaluate(() => JSON.parse(document.body.getAttribute('hx-headers'))['X-Existing']);
    const png = await page.evaluate(() => {
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 48;
        const drawing = canvas.getContext('2d');
        drawing.fillStyle = '#4267a0';
        drawing.fillRect(0, 0, 64, 48);
        return canvas.toDataURL('image/png').split(',')[1];
    });
    const responsePromise = page.waitForResponse((response) =>
        response.url() === `${baseURL}${path}/upload` && response.request().method() === 'POST');
    await page.locator('[x-data=uploadZone] input[type=file]').setInputFiles({
        name: `d18f-${label}.png`, mimeType: 'image/png', buffer: Buffer.from(png, 'base64'),
    });
    const response = await responsePromise;
    assert.equal(response.status(), status);
    if (status === 200) {
        assert.equal(response.request().headers()['x-csrf-token'], token, 'Only the current cookie token is sent');
        if (existingHeader !== undefined) assert.equal(response.request().headers()['x-existing'], existingHeader);
        await page.waitForFunction((count) =>
            document.querySelectorAll('#image-grid [data-processing-status=ready]').length === count,
        expectedReady, { timeout: 45_000 });
    }
    return response;
}
async function assertDomMatchesCookie() {
    const token = await cookie('csrf_token');
    const state = await page.evaluate(() => ({
        meta: document.querySelector('meta[name=csrf-token]').content,
        fields: [...document.querySelectorAll('input[name=csrf_token]')].map((input) => input.value),
        headers: JSON.parse(document.body.getAttribute('hx-headers')),
    }));
    assert.equal(state.meta, token);
    assert.ok(state.fields.every((value) => value === token));
    assert.equal(state.headers['X-CSRF-Token'], token);
    assert.equal(Object.keys(state.headers).filter((key) => key.toLowerCase() === 'x-csrf-token').length, 1);
}
async function loadNativeExpiryDocument(path) {
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
    await Promise.all([
        page.waitForURL(`**${path}/expiry`),
        page.evaluate(({ path, tomorrow }) => {
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = `${path}/expiry`;
            for (const [name, value] of Object.entries({
                csrf_token: document.querySelector('meta[name=csrf-token]').content,
                expires_at: tomorrow,
            })) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = name;
                input.value = value;
                form.append(input);
            }
            document.body.append(form);
            form.requestSubmit();
        }, { path, tomorrow }),
    ]);
    await page.waitForLoadState('networkidle');
}
async function assertNativePostRecoveryUsesGet(path) {
    let posts = 0;
    const countPosts = (request) => {
        if (request.url() === `${baseURL}${path}/expiry` && request.method() === 'POST') posts++;
    };
    page.on('request', countPosts);
    await loadNativeExpiryDocument(path);
    // Chrome may evict POST documents from BFCache. Exercise the same handler
    // on a genuinely POST-loaded page and inspect its real network requests;
    // a genuine GET-page BFCache restoration is tested separately below.
    await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle' }),
        page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))),
    ]);
    console.log('Native POST persisted-event recovery:', JSON.stringify({ posts, path: new URL(page.url()).pathname }));
    page.off('request', countPosts);
    assert.equal(posts, 1, 'BFCache recovery never repeats the previously successful POST');
    assert.equal(new URL(page.url()).pathname, path, 'Recovery navigates to the canonical GET gallery');
}
try {
    await page.goto('/login');
    await page.locator('[name=email]').fill(email);
    await page.locator('[name=password]').fill(password);
    await page.locator('form[action="/login"] button[type=submit]').click();
    await page.waitForURL((url) => url.pathname === '/dashboard');
    const path = await createGallery(`CSRF recovery E2E ${Date.now()}`);
    await assertNativePostRecoveryUsesGet(path);
    const documentResponse = await page.reload();
    assert.match(documentResponse.headers()['cache-control'], /private/);
    assert.match(documentResponse.headers()['cache-control'], /no-cache/);

    // Background GET rotates the cookie without updating the open document.
    await staleDom('legacy-rendered-token');
    const rotated = await rotateCsrfWhileDomStaysStale();
    assert.notEqual(await page.locator('meta[name=csrf-token]').getAttribute('content'), rotated);
    await upload(path, 'rotated-cookie', 1);
    await assertDomMatchesCookie();

    // Same user's session can rotate in another tab, too.
    const oldSession = await cookie('session');
    const oldCsrf = await cookie('csrf_token');
    const renewed = await context.request.post('/api/v1/auth/login', { data: { email, password } });
    assert.equal(renewed.status(), 200);
    const newSession = (await renewed.json()).access_token;
    assert.notEqual(newSession, oldSession);
    await context.addCookies([{ name: 'session', value: newSession, url: baseURL, httpOnly: true, sameSite: 'Lax' }]);
    assert.equal((await context.request.get('/dashboard')).status(), 200);
    assert.notEqual(await cookie('csrf_token'), oldCsrf);
    await staleDom(oldCsrf);
    await upload(path, 'session-rotation', 2);

    // A real restored page must refresh once, even though JS cannot inspect
    // the HttpOnly session cookie to detect a change while it was frozen.
    const recoveryQuery = '?csrf_recovery_probe=keep&sort=name';
    await page.goto(`${path}${recoveryQuery}`);
    await staleDom('legacy-bfcache-token');
    await page.waitForLoadState('networkidle');
    const beforeRestore = await page.evaluate(() => Number(sessionStorage.getItem('d18fRestores')));
    await page.goto('/legal/impressum');
    const beforeLoads = await page.evaluate(() => Number(sessionStorage.getItem('d18fDocumentLoads')));
    await rotateCsrfWhileDomStaysStale();
    await page.goBack();
    await page.waitForFunction((count) => Number(sessionStorage.getItem('d18fRestores')) > count, beforeRestore);
    await page.waitForLoadState('networkidle');
    await page.waitForFunction((count) => Number(sessionStorage.getItem('d18fDocumentLoads')) === count + 1, beforeLoads);
    assert.equal(new URL(page.url()).pathname, path);
    assert.equal(new URL(page.url()).search, recoveryQuery, 'A restored GET keeps its query');
    await assertDomMatchesCookie();
    await upload(path, 'bfcache', 3);

    // A genuinely old script has no new handlers. Its rejected upload must
    // cause one GET reload and no automatic unsafe request replay.
    let serveNewClient = false;
    await page.route('**/static/js/app.js?*', async (route) => {
        if (!serveNewClient) {
            await route.fulfill({ contentType: 'application/javascript', body: 'window.__d18fLegacyClient = true;' });
        } else {
            await route.continue();
        }
    });
    await page.goto(path);
    await loadNativeExpiryDocument(path);
    assert.equal(await page.evaluate(() => window.__d18fLegacyClient), true);
    await context.addCookies([{ name: 'csrf_token', value: 'unsigned-legacy-cookie', url: baseURL, sameSite: 'Lax' }]);
    await staleDom('unsigned-legacy-cookie');
    let reloads = 0;
    let uploadPosts = 0;
    let repeatedNativePosts = 0;
    const navigationMethods = [];
    const countRecovery = (request) => {
        if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
            reloads++;
            navigationMethods.push(request.method());
        }
        if (request.url() === `${baseURL}${path}/upload` && request.method() === 'POST') uploadPosts++;
        if (request.url() === `${baseURL}${path}/expiry` && request.method() === 'POST') repeatedNativePosts++;
    };
    page.on('request', countRecovery);
    expectedCsrfFailure = true;
    serveNewClient = true;
    const rejected = await upload(path, 'old-client-rejected', 3, 403);
    assert.equal(rejected.headers()['hx-redirect'], path);
    await page.waitForFunction(() => !window.__d18fLegacyClient && window.__psAppInit);
    await page.waitForLoadState('networkidle');
    assert.equal(reloads, 1);
    assert.deepEqual(navigationMethods, ['GET']);
    assert.equal(uploadPosts, 1, 'The denied POST is never replayed');
    assert.equal(repeatedNativePosts, 0, 'The original native POST document is never replayed');
    page.off('request', countRecovery);
    expectedCsrfFailure = false;
    await page.unroute('**/static/js/app.js?*');
    await staleDom('legacy-after-reload');
    await upload(path, 'old-client-retry', 4);
    if (process.env.E2E_SCREENSHOTS === '1') {
        await mkdir('.playwright', { recursive: true });
        await page.screenshot({ path: '.playwright/stale-csrf-recovered.png' });
    }

    // The same synchronization also repairs conventional POST forms.
    const nativePath = await createGallery(`CSRF native E2E ${Date.now()}`);
    await staleDom('legacy-native-token');
    const nativeToken = await rotateCsrfWhileDomStaysStale();
    await page.locator('[data-open-dialog=deleteModal]').click();
    const submittedPromise = page.waitForResponse((response) =>
        response.url() === `${baseURL}${nativePath}/delete` && response.request().method() === 'POST');
    await page.locator('#deleteModal button[type=submit]').click();
    const native = await submittedPromise;
    assert.equal(native.status(), 303);
    assert.equal(new URLSearchParams(native.request().postData()).get('csrf_token'), nativeToken);
    await page.waitForURL((url) => url.pathname === '/dashboard');
    galleries.delete(nativePath);
    assert.deepEqual(errors, []);
    console.log('Playwright PASS: real uploads with stale DOM, cookie/session rotation, genuine BFCache restore, one safe legacy-client refresh without POST replay, native form repair.');
} finally {
    try {
        await page.goto('/dashboard');
        const token = await page.locator('meta[name=csrf-token]').getAttribute('content');
        for (const path of galleries) {
            const removed = await context.request.post(`${path}/delete`, { form: { csrf_token: token, confirm_delete: 'yes' } });
            assert.ok([200, 404].includes(removed.status()), `Cleanup failed for ${path}`);
        }
    } finally {
        await browser.close();
    }
}
