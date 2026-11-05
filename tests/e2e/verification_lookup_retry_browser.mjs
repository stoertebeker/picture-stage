import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';

// The production continuation script must reload verification GETs under CSP,
// retaining the exact token URL and HttpOnly, route-scoped progress cookie.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const script = await readFile('frontend/static/js/lookup-retry.js');
const path = '/mount/verify-email/old-link';
const cookies = [];
const errors = [];
let requests = 0;
const server = createServer((request, response) => {
    response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'");
    if (request.url === '/mount/static/lookup-retry.js') {
        response.setHeader('Content-Type', 'text/javascript');
        response.end(script);
        return;
    }
    if (request.url !== path || request.method !== 'GET') {
        response.writeHead(404);
        response.end();
        return;
    }
    cookies.push(request.headers.cookie || '');
    requests++;
    response.setHeader('Content-Type', 'text/html');
    if (requests < 3) {
        response.writeHead(503, {
            'X-Picture-Stage-Lookup': 'before-effects', 'Retry-After': '1',
            'Set-Cookie': `verification_lookup=window-${requests}; Path=${path}; HttpOnly; SameSite=Lax`,
        });
        response.end('<!doctype html><html><body><div data-lookup-wait role="status">Verifying</div>' +
            '<script src="/mount/static/lookup-retry.js"></script></body></html>');
    } else {
        response.end('<!doctype html><html><body><div role="status">Email verified</div></body></html>');
    }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({
    executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome', headless: true,
});
const context = await browser.newContext();
const page = await context.newPage();
page.on('pageerror', (error) => errors.push(error.message));
try {
    await page.goto(`http://127.0.0.1:${server.address().port}${path}`);
    await page.getByText('Email verified', { exact: true }).waitFor();
    assert.equal(requests, 3);
    assert.deepEqual(cookies, ['', 'verification_lookup=window-1', 'verification_lookup=window-2']);
    const cookie = (await context.cookies()).find((item) => item.name === 'verification_lookup');
    assert.equal(cookie.path, path);
    assert.equal(cookie.httpOnly, true);
    assert.deepEqual(errors, []);
    console.log('Playwright PASS: verification GET advances automatically with exact route-scoped HttpOnly cookie under strict CSP.');
} finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
}
