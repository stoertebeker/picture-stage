import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { spawnSync } from 'node:child_process';

// Uses real Jinja templates, production components and actual HTMX/Alpine CSP.
// E2E_PYTHON=.venv/bin/python E2E_HTMX_FILE=... E2E_ALPINE_FILE=... node ...
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const rendered = spawnSync(process.env.E2E_PYTHON || '.venv/bin/python', ['tests/e2e/render_guest_pages.py'], {
    encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, env: {...process.env, ENVIRONMENT: 'development', CAPTCHA_ENABLED: 'false', PYTHONPATH: '.'},
});
assert.equal(rendered.status, 0, rendered.stderr);
const fixture = JSON.parse(rendered.stdout);
const scripts = {
    'components.js': await readFile('frontend/static/js/components.js'),
    'app.js': await readFile('frontend/static/js/app.js'),
    'lookup-retry.js': await readFile('frontend/static/js/lookup-retry.js'),
    'htmx.min.js': await readFile(process.env.E2E_HTMX_FILE || 'frontend/static/js/htmx.min.js'),
    'alpine.min.js': await readFile(process.env.E2E_ALPINE_FILE || 'frontend/static/js/alpine.min.js'),
};
for (const name of ['htmx.min.js', 'alpine.min.js']) {
    assert.ok(!scripts[name].toString().includes('0.0.0-placeholder'), `${name} must be the real library`);
}
let pageLoads = 0;
const queries = [];
let refreshPlan = [];
const heldRefreshes = [];
const errors = [];
const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://fixture');
    response.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:");
    const script = scripts[url.pathname.split('/').at(-1)];
    if (script) { response.setHeader('Content-Type', 'text/javascript'); response.end(script); return; }
    if (url.pathname.endsWith('styles.css')) {
        response.setHeader('Content-Type', 'text/css');
        response.end('.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.group{height:250px;position:relative}.group img{height:250px;width:100%;object-fit:cover}.fixed{position:fixed}.inset-0{inset:0;background:#111;z-index:50}.bottom-6{bottom:24px}.right-6{right:24px}.fixed img{height:70vh}.hidden{display:none}.sm\\:flex{display:flex}');
        return;
    }
    if (url.pathname.startsWith('/media/')) {
        response.setHeader('Content-Type', 'image/gif');
        response.end(Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64'));
        return;
    }
    response.setHeader('Content-Type', 'text/html');
    if (url.pathname === '/mount/g/fixture') { response.end(fixture.initial); return; }
    if (url.pathname === '/mount/g/fixture/gallery-images') {
        pageLoads++;
        const query = {sort_by: url.searchParams.get('sort_by') || 'sort_order',
            sort_dir: url.searchParams.get('sort_dir') || 'asc', filter: url.searchParams.get('filter') || 'all',
            cursor: url.searchParams.get('cursor') || ''};
        queries.push(query);
        const key = [query.sort_by, query.sort_dir, query.filter, query.cursor].join('|');
        assert.ok(fixture.responses[key], key);
        const action = query.cursor ? null : refreshPlan.shift();
        if (action === 'hold') {
            heldRefreshes.push({response, body: fixture.responses[key]});
            return;
        }
        if (action === 503 || action === 500 || action === 'marked503') {
            response.statusCode = action === 'marked503' ? 503 : action;
            if (action === 'marked503') response.setHeader('X-Picture-Stage-Lookup', 'before-effects');
            response.end('Temporary refresh failure');
            return;
        }
        response.end(fixture.responses[key]);
        return;
    }
    response.writeHead(404); response.end('');
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({executablePath: process.env.CHROME_EXECUTABLE || '/opt/google/chrome/chrome', headless: true});
const page = await browser.newPage({viewport: {width: 1200, height: 700}});
page.on('pageerror', (error) => errors.push(error.message));
const base = `http://127.0.0.1:${server.address().port}`;
const state = (expression) => page.evaluate((expression) => {
    const viewer = Alpine.$data(document.querySelector('[x-data="guestViewer"]'));
    return expression.split('.').reduce((value, key) => value[key], viewer);
}, expression);
try {
    await page.goto(`${base}/mount/g/fixture`);
    await page.waitForFunction(() => window.Alpine && Alpine.$data(document.querySelector('[x-data="guestViewer"]')).activeOrder.length === 30);
    assert.equal(pageLoads, 0, 'initial render must not fetch unrequested pages');
    assert.equal(await state('images.length'), 30);
    await page.evaluate(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).openLightbox(29));
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).lightboxIndex === 30);
    assert.equal(pageLoads, 1, 'lightbox crosses page boundary before scrolling');
    assert.equal(await page.evaluate(() => window.scrollY), 0);
    assert.equal(await state('currentImage.filename'), '31.jpg');
    assert.equal(await page.locator('#image-grid .group').count(), 60);
    assert.equal(await page.locator('#image-grid .grid .grid').count(), 0, 'append stays flat');
    await page.keyboard.press('ArrowLeft');
    assert.equal(await state('currentImage.filename'), '30.jpg');
    assert.equal(pageLoads, 1);
    await page.evaluate(() => { const v = Alpine.$data(document.querySelector('[x-data="guestViewer"]')); v.lightboxIndex = 59; });
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).lightboxIndex === 60);
    assert.equal(await state('currentImage.filename'), '61.jpg');
    assert.equal(pageLoads, 2);
    assert.equal(await page.locator('#image-grid .group').count(), 61);
    assert.equal(await page.locator('#image-grid .grid .grid').count(), 0);
    assert.equal(await state('hasNextImage'), false);
    await page.reload();
    await page.waitForFunction(() => window.Alpine && Alpine.$data(document.querySelector('[x-data="guestViewer"]')).activeOrder.length === 30);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).activeOrder.length >= 60);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).activeOrder.length === 61);
    assert.equal(await page.locator('#image-grid .group').count(), 61);
    assert.equal(await page.locator('#image-grid .grid .grid').count(), 0);
    // Refresh controls stay coherent with the page cursor's query context.
    await page.reload();
    await page.waitForFunction(() => window.Alpine && Alpine.$data(document.querySelector('[x-data="guestViewer"]')).activeOrder.length === 30);
    await page.locator('[name=sort_by]').selectOption('filename', {force: true});
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).pageQuery.sort_by === 'filename');
    await page.locator('button[hx-get*="filter=selected"]').click();
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).pageQuery.filter === 'selected');
    assert.equal(queries.at(-1).sort_by, 'filename');
    await page.locator('[name=sort_dir]').selectOption('desc', {force: true});
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).pageQuery.sort_dir === 'desc');
    assert.equal(queries.at(-1).filter, 'selected');
    assert.equal(queries.at(-1).sort_by, 'filename');
    assert.equal(await page.locator('[name=filter]').inputValue(), 'selected');
    assert.ok((await page.locator('button[hx-get*="filter=selected"]').getAttribute('class')).includes('text-accent'));
    await page.evaluate(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).openLightbox(29));
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).lightboxIndex === 30);
    assert.equal(await state('filteredCount'), 31);
    assert.equal(await state('activeOrder.length'), 31);
    assert.equal(queries.at(-1).filter, 'selected');
    assert.equal(queries.at(-1).sort_by, 'filename');
    assert.equal(queries.at(-1).sort_dir, 'desc');
    const resetViewer = async () => {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.reload();
        await page.waitForFunction(() => window.Alpine && Alpine.$data(document.querySelector('[x-data="guestViewer"]')).activeOrder.length === 30);
    };
    const clickFilter = (filter) => page.locator(`button[hx-get*="filter=${filter}"]`).click();
    const waitRestored = () => page.waitForFunction(() => {
        const v = Alpine.$data(document.querySelector('[x-data="guestViewer"]'));
        return v.pageGeneration > 0 && !v._refreshing && v.pageQuery.filter === 'all';
    });
    for (const failure of [503, 500, 'network']) {
        await resetViewer();
        if (failure === 'network') {
            await page.route('**/gallery-images*', (route) => {
                const url = new URL(route.request().url());
                if (!url.searchParams.has('cursor')) route.abort('failed');
                else route.continue();
            });
        } else refreshPlan = [failure];
        await clickFilter('selected');
        await waitRestored();
        if (failure === 'network') await page.unroute('**/gallery-images*');
        assert.equal(await state('activeOrder.length'), 30);
        assert.equal(await page.locator('[name=filter]').inputValue(), 'all');
        await page.evaluate(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).openLightbox(29));
        await page.keyboard.press('ArrowRight');
        await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).lightboxIndex === 30);
        assert.equal(await state('currentImage.filename'), '31.jpg');
        assert.equal(queries.at(-1).filter, 'all');
    }
    // A late failure of an older generation cannot unlock/reset the newer one.
    await resetViewer();
    refreshPlan = ['hold', 'hold'];
    await clickFilter('selected');
    await page.waitForFunction(() => Alpine.$data(document.querySelector('[x-data="guestViewer"]')).pageGeneration === 1);
    await clickFilter('unrated');
    const heldDeadline = Date.now() + 5000;
    while (heldRefreshes.length < 2 && Date.now() < heldDeadline) await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(heldRefreshes.length, 2);
    const old = heldRefreshes.shift();
    old.response.writeHead(500); old.response.end('Old failure');
    await page.waitForTimeout(100);
    assert.equal(await state('_refreshing'), true);
    assert.equal(await state('pageQuery.filter'), 'unrated');
    const current = heldRefreshes.shift(); current.response.end(current.body);
    await page.waitForFunction(() => !Alpine.$data(document.querySelector('[x-data="guestViewer"]'))._refreshing);
    assert.equal(await state('pageQuery.filter'), 'unrated');
    // Marked503s remain pending and retry the exact query in sequence.
    await resetViewer();
    refreshPlan = ['marked503', 'marked503'];
    const retryStart = queries.length;
    await clickFilter('selected');
    await page.waitForTimeout(100);
    assert.equal(await state('_refreshing'), true);
    await page.waitForFunction(() => {
        const v = Alpine.$data(document.querySelector('[x-data="guestViewer"]'));
        return !v._refreshing && v.pageQuery.filter === 'selected';
    });
    assert.equal(queries.length - retryStart, 3);
    assert.ok(queries.slice(retryStart).every((query) => query.filter === 'selected' && !query.cursor));
    // Superseded marked retries cannot later replace an accepted newer filter.
    await resetViewer();
    refreshPlan = ['marked503'];
    const staleStart = queries.length;
    await clickFilter('selected');
    await page.waitForTimeout(100);
    await clickFilter('unrated');
    await page.waitForFunction(() => {
        const v = Alpine.$data(document.querySelector('[x-data="guestViewer"]'));
        return !v._refreshing && v.pageQuery.filter === 'unrated';
    });
    await page.waitForTimeout(1200);
    assert.equal(queries.length - staleStart, 2);
    assert.equal(await state('pageQuery.filter'), 'unrated');
    assert.deepEqual(errors, []);
    console.log('Guest pagination browser passed: 61 images, boundary/cache/flat append, filter/sort continuity,503/500/network recovery, stale failure isolation and ordered/superseded marked503 retries.');
} finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
}
