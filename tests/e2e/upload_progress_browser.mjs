import assert from 'node:assert/strict';

// Browser regression test for the complete upload path. Start the Compose app
// and a disposable Chrome instance first, for example:
//   google-chrome --headless=new --no-sandbox --remote-debugging-port=9222 \
//     --user-data-dir=/tmp/picture-stage-e2e about:blank
//   E2E_PASSWORD=... node tests/e2e/upload_progress_browser.mjs

const debugBase = process.env.CHROME_DEBUG_URL || 'http://127.0.0.1:9222';
const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8000';
const email = process.env.E2E_EMAIL || 'testadmin@example.com';
const password = process.env.E2E_PASSWORD;
const fileCount = Number(process.env.E2E_FILE_COUNT || 3);

if (!password) {
    throw new Error('E2E_PASSWORD is required');
}

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
socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
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
    const result = await command('Runtime.evaluate', {
        expression,
        awaitPromise,
        returnByValue: true,
    });
    if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.exception?.description || 'Browser evaluation failed');
    }
    return result.result.value;
}

async function waitFor(expression, description, timeoutMs = 15_000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (await evaluate(expression)) return;
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(`Timed out waiting for ${description}`);
}

async function navigate(url) {
    await command('Page.navigate', { url });
    await waitFor("document.readyState === 'complete'", `navigation to ${url}`);
}

async function deleteGallery(galleryUrl, galleryName) {
    await navigate(galleryUrl);
    const endpoint = `${new URL(galleryUrl).pathname}/delete`;
    const result = await evaluate(
        `(async () => {
            const token = document.querySelector('meta[name=csrf-token]').content;
            const response = await fetch(${JSON.stringify(endpoint)}, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRF-Token': token,
                },
                body: new URLSearchParams({ confirm_name: ${JSON.stringify(galleryName)} }),
            });
            return {
                ok: response.ok,
                status: response.status,
                path: new URL(response.url).pathname,
            };
        })()`,
        true,
    );
    assert.deepEqual(result, { ok: true, status: 200, path: '/dashboard' });
}

await command('Page.enable');
await command('Runtime.enable');
await command('Network.enable');
await command('Network.clearBrowserCookies');

const galleryName = `Browser upload E2E ${Date.now()}`;
let galleryUrl = null;

try {
    await navigate(`${baseUrl}/login`);
    assert.ok(
        await evaluate("document.querySelector('input[name=csrf_token]').value"),
        'The first GET must render the CSRF token that it sets as a cookie',
    );
    await evaluate(
        `(() => {
            const form = document.querySelector('form[action="/login"]');
            form.elements.email.value = ${JSON.stringify(email)};
            form.elements.password.value = ${JSON.stringify(password)};
            form.requestSubmit();
        })()`,
    );
    await waitFor("location.pathname === '/dashboard'", 'successful login');
    await waitFor(
        "!!(window.htmx && document.querySelector('form[action=\"/dashboard/galleries\"]'))",
        'gallery creation form',
    );
    await evaluate(
        `(() => {
            const form = document.querySelector('form[action="/dashboard/galleries"]');
            form.elements.name.value = ${JSON.stringify(galleryName)};
            form.requestSubmit();
        })()`,
    );
    await waitFor(
        `[...document.querySelectorAll('a[href^="/galleries/"]')]
            .some((candidate) => candidate.textContent.trim() === ${JSON.stringify(galleryName)})`,
        'dedicated E2E gallery',
    );

    galleryUrl = await evaluate(
        `(() => {
            const link = [...document.querySelectorAll('a[href^="/galleries/"]')]
                .find((candidate) => candidate.textContent.trim() === ${JSON.stringify(galleryName)});
            return link ? link.href : null;
        })()`,
    );
    assert.ok(galleryUrl, 'The E2E gallery must have been created');
    // Reproduce a returning authenticated session whose readable CSRF cookie
    // expired independently. The gallery GET must render the replacement token
    // into HTMX headers/forms in the same response that sets the new cookie.
    await command('Network.deleteCookies', { name: 'csrf_token', url: baseUrl });
    await navigate(galleryUrl);
    await waitFor("!!(window.Alpine && document.querySelector('[x-data=uploadZone]'))", 'upload component');

    const csrf = await evaluate(`(() => ({
        cookie: document.cookie.split('; ').find((item) => item.startsWith('csrf_token='))?.split('=')[1] || '',
        header: JSON.parse(document.body.getAttribute('hx-headers'))['X-CSRF-Token'],
        hidden: document.querySelector('form[hx-post$="/upload"] input[name=csrf_token]').value,
        meta: document.querySelector('meta[name=csrf-token]').content,
    }))()`);
    assert.ok(csrf.cookie, JSON.stringify(csrf));
    assert.equal(csrf.header, csrf.cookie, JSON.stringify(csrf));
    assert.equal(csrf.hidden, csrf.cookie, JSON.stringify(csrf));
    assert.equal(csrf.meta, csrf.cookie, JSON.stringify(csrf));

    await evaluate(`(() => {
        window.__uploadE2E = { afterRequest: null, responseError: null };
        const form = document.querySelector('form[hx-post$="/upload"]');
        document.addEventListener('htmx:afterRequest', (event) => {
            if (event.target !== form) return;
            window.__uploadE2E.afterRequest = {
                status: event.detail.xhr.status,
                successful: event.detail.successful,
                failed: event.detail.failed,
            };
        });
        document.addEventListener('htmx:responseError', (event) => {
            if (event.target !== form) return;
            window.__uploadE2E.responseError = {
                status: event.detail.xhr.status,
                response: event.detail.xhr.responseText,
            };
        });
    })()`);

    await evaluate(
        `(async () => {
            const transfer = new DataTransfer();
            for (let index = 0; index < ${fileCount}; index += 1) {
                const canvas = document.createElement('canvas');
                canvas.width = 32;
                canvas.height = 32;
                const context = canvas.getContext('2d');
                context.fillStyle = 'hsl(' + (index * 60) + ' 80% 50%)';
                context.fillRect(0, 0, canvas.width, canvas.height);
                const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
                transfer.items.add(new File([blob], 'browser-e2e-' + index + '.png', { type: 'image/png' }));
            }
            const input = document.querySelector('input[type=file][name=files]');
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        })()`,
        true,
    );

    await waitFor('window.__uploadE2E.afterRequest !== null', 'upload response', 30_000);
    const outcome = await evaluate(`(() => {
        const root = document.querySelector('[x-data=uploadZone]');
        const component = Alpine.$data(root);
        return {
            request: window.__uploadE2E,
            state: component.state,
            statusText: component.statusText(),
            trackedImages: { ...component.trackedImages },
        };
    })()`);

    assert.equal(outcome.request.responseError, null, JSON.stringify(outcome));
    assert.equal(outcome.request.afterRequest.status, 200, JSON.stringify(outcome));
    assert.equal(outcome.request.afterRequest.successful, true, JSON.stringify(outcome));
    assert.notEqual(outcome.state, 'failed', JSON.stringify(outcome));
    assert.equal(Object.keys(outcome.trackedImages).length, fileCount, JSON.stringify(outcome));

    await waitFor(
        "['complete', 'failed'].includes(Alpine.$data(document.querySelector('[x-data=uploadZone]')).state)",
        'preview processing',
        30_000,
    );
    const finalState = await evaluate(
        "Alpine.$data(document.querySelector('[x-data=uploadZone]')).state",
    );
    assert.equal(finalState, 'complete');
    await deleteGallery(galleryUrl, galleryName);
    galleryUrl = null;
    console.log(JSON.stringify({ ...outcome, finalState }, null, 2));
} finally {
    if (galleryUrl) {
        try {
            await deleteGallery(galleryUrl, galleryName);
        } catch (cleanupError) {
            console.error(`E2E cleanup failed: ${cleanupError.message}`);
        }
    }
    socket.close();
}
