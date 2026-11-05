import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';

// A disposable HTTP fixture exercises the real scripts and real HTMX under
// strict CSP. Database/authorization behavior is covered by integration tests.
// PLAYWRIGHT_MODULE=... E2E_HTMX_FILE=... node tests/e2e/lookup_retry_browser.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
assert.ok(process.env.E2E_HTMX_FILE, 'E2E_HTMX_FILE must point at the production HTMX library');
const scripts = {
    '/mount/static/lookup-retry.js': await readFile('frontend/static/js/lookup-retry.js'),
    '/mount/static/components.js': await readFile('frontend/static/js/components.js'),
    '/mount/static/htmx.js': await readFile(process.env.E2E_HTMX_FILE),
};
assert.ok(!scripts['/mount/static/htmx.js'].toString().includes('0.0.0-placeholder'));
const counts = { initial: 0, password: 0, grid: 0, selections: 0, complete: 0 };
const passwordBodies = [];
const selectionBodies = [];
const selectionHeaders = [];
const errors = [];
const shell = (content) => `<!doctype html><html><body><main>${content}</main>
<script src="/mount/static/lookup-retry.js"></script><script src="/mount/static/htmx.js"></script>
<script src="/mount/static/components.js"></script></body></html>`;
const gate = '<form data-lookup-password method="post" action="/mount/g/legacy/verify-password">' +
    '<input name="guest_csrf_token" value="csrf-proof" type="hidden">' +
    '<input name="password" type="password"><button>Unlock</button></form>';
const viewer = '<button id="grid" hx-get="/mount/g/legacy/gallery-images?offset=6&filter=selected" ' +
    'hx-target="#result" hx-swap="outerHTML">Load grid</button><div id="result"></div>';
const server = createServer(async (request, response) => {
    response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; form-action 'self'");
    if (scripts[request.url]) {
        response.setHeader('Content-Type', 'text/javascript');
        response.end(scripts[request.url]);
        return;
    }
    let body = '';
    for await (const chunk of request) body += chunk;
    const retry = (value) => {
        response.writeHead(503, {
            'Content-Type': 'text/html', 'X-Picture-Stage-Lookup': 'before-effects', 'Retry-After': '1',
            'Set-Cookie': `gallery_lookup=${value}; Path=/mount/g/legacy; HttpOnly; SameSite=Lax`,
        });
        response.end(shell('<div data-lookup-wait>Loading</div>'));
    };
    response.setHeader('Content-Type', 'text/html');
    if (request.url === '/mount/g/legacy' && request.method === 'GET') {
        if (++counts.initial <= 2) return retry(`initial-${counts.initial}`);
        response.end(shell(request.headers.cookie?.includes('unlocked=yes') ? viewer : gate));
    } else if (request.url === '/mount/g/legacy/verify-password') {
        counts.password++;
        passwordBodies.push(body);
        if (counts.password <= 2) return retry(`password-${counts.password}`);
        if (new URLSearchParams(body).get('password') === 'wrong') {
            response.writeHead(401);
            response.end(shell(gate + '<p role="alert">Invalid password</p>'));
        } else {
            response.setHeader('Set-Cookie', 'unlocked=yes; Path=/mount/g/legacy; HttpOnly; SameSite=Lax');
            response.end(shell(viewer));
        }
    } else if (request.url === '/mount/g/legacy/gallery-images?offset=6&filter=selected') {
        if (++counts.grid <= 2) return retry(`grid-${counts.grid}`);
        response.end('<div id="result">Selected page six</div>');
    } else if (request.url === '/mount/g/legacy/selections') {
        counts.selections++;
        selectionBodies.push(body);
        selectionHeaders.push(request.headers['x-guest-csrf-token']);
        if (counts.selections <= 2) return retry(`selection-${counts.selections}`);
        response.writeHead(201);
        response.end('{}');
    } else if (request.url === '/mount/g/legacy/complete') {
        counts.complete++;
        response.writeHead(503); // Generic failure must never replay a mutation.
        response.end('{}');
    } else {
        response.writeHead(404);
        response.end('Not found');
    }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const baseURL = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome', headless: true });
const context = await browser.newContext({ baseURL });
const page = await context.newPage();
page.on('pageerror', (error) => errors.push(error.message));
try {
    await page.goto('/mount/g/legacy');
    await page.locator('[name=password]').waitFor();
    assert.equal(counts.initial, 3);
    await page.locator('[name=password]').fill('wrong');
    await page.locator('button').click();
    await page.getByRole('alert').waitFor();
    assert.equal(counts.password, 3);
    assert.ok(passwordBodies.every((body) => body === 'guest_csrf_token=csrf-proof&password=wrong'));
    // A 401 replaces the gate without losing its delegated submit listener.
    await page.locator('[name=password]').fill('correct');
    await page.locator('button').click();
    await page.locator('#grid').waitFor();
    assert.equal(counts.password, 4);
    await page.locator('#grid').click();
    await page.getByText('Selected page six').waitFor();
    assert.equal(counts.grid, 3);
    const state = await page.evaluate(async () => {
        const component = guestViewerComponent();
        component.galleryUrl = '/mount/g/legacy';
        component.guestCsrfToken = 'csrf-proof';
        component.images = [{id: 'one', selected: false, favorited: false}];
        component.imageById = {one: component.images[0]};
        component.selectionSaveFailed = 'save failed';
        const first = component.toggleSelect('one');
        const second = component.toggleFavorite('one');
        await Promise.all([first, second]);
        await component.completeReview();
        return {selected: component.images[0].selected, favorited: component.images[0].favorited, error: component.selectionError};
    });
    assert.equal(counts.selections, 4);
    assert.deepEqual(selectionBodies.slice(0, 3), Array(3).fill('{"image_id":"one","action":"select"}'));
    assert.equal(JSON.parse(selectionBodies[3]).action, 'favorite');
    assert.deepEqual(selectionHeaders, Array(4).fill('csrf-proof'));
    assert.deepEqual(state, {selected: true, favorited: true, error: 'save failed'});
    assert.equal(counts.complete, 1);
    const lookup = (await context.cookies()).find((cookie) => cookie.name === 'gallery_lookup');
    assert.equal(lookup.path, '/mount/g/legacy');
    assert.equal(lookup.httpOnly, true);
    assert.deepEqual(errors, []);
    console.log('Playwright PASS: initial GET, password/401 retry, real HTMX params/outer swap, ordered mutation replay and generic503 non-replay.');
} finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
}
