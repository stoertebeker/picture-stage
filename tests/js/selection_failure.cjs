const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

function viewerWithFetch(fetch) {
    const context = vm.createContext({
        document: { addEventListener() {} },
        window: { location: { reload() {} } },
        fetch,
    });
    vm.runInContext(fs.readFileSync('frontend/static/js/components.js', 'utf8'), context);
    const viewer = vm.runInContext('guestViewerComponent()', context);
    viewer.images = [{ id: 'one', selected: false, favorited: false, comment: 'old' }];
    viewer.imageById = { one: viewer.images[0] };
    viewer.selectionSaveFailed = 'save failed';
    viewer.selectionLimitReached = 'limit reached';
    viewer.selectionCommentTooLong = 'comment too long';
    return viewer;
}

test('successful selection and comment persist only after server acknowledgement', async () => {
    const viewer = viewerWithFetch(async () => ({ ok: true }));
    await viewer.toggleSelect('one');
    await viewer.toggleFavorite('one');
    await viewer.submitComment('one', 'new');
    assert.equal(viewer.images[0].selected, true);
    assert.equal(viewer.images[0].favorited, true);
    assert.equal(viewer.images[0].comment, 'new');
    assert.equal(viewer.selectedCount, 1);
    assert.equal(viewer.favoritedCount, 1);
});

test('limit rejection and network failure leave previous state intact', async () => {
    const viewer = viewerWithFetch(async () => ({ ok: false, status: 409 }));
    await viewer.toggleSelect('one');
    assert.equal(viewer.images[0].selected, false);
    assert.equal(viewer.selectedCount, 0);
    assert.equal(viewer.selectionError, 'limit reached');
    const offline = viewerWithFetch(async () => { throw new Error('offline'); });
    await offline.toggleFavorite('one');
    await offline.submitComment('one', 'unsaved');
    assert.equal(offline.images[0].favorited, false);
    assert.equal(offline.images[0].comment, 'old');
    assert.equal(offline.selectionError, 'save failed');
});

test('rapid actions on the same image are saved in order', async () => {
    const resolvers = [];
    const actions = [];
    const viewer = viewerWithFetch((_url, options) => {
        actions.push(JSON.parse(options.body).action);
        return new Promise((resolve) => { resolvers.push(resolve); });
    });
    const first = viewer.toggleSelect('one');
    const second = viewer.toggleFavorite('one');
    const third = viewer.submitComment('one', 'new');
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(actions, ['select']);
    assert.equal(viewer.images[0].selected, false);
    resolvers.shift()({ ok: true });
    await first;
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(actions, ['select', 'favorite']);
    resolvers.shift()({ ok: true });
    await second;
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(actions, ['select', 'favorite', 'comment']);
    resolvers.shift()({ ok: true });
    await third;
    assert.equal(viewer.images[0].selected, true);
    assert.equal(viewer.images[0].favorited, true);
    assert.equal(viewer.images[0].comment, 'new');
});

test('comment limits count Unicode codepoints without changing an existing comment', async () => {
    let calls = 0;
    const viewer = viewerWithFetch(async () => { calls++; return { ok: true }; });
    viewer.selectionCommentLimit = 3;
    await viewer.submitComment('one', '🙂🙂🙂🙂');
    assert.equal(calls, 0);
    assert.equal(viewer.images[0].comment, 'old');
    assert.equal(viewer.selectionError, 'comment too long');
    await viewer.submitComment('one', '🙂🙂🙂');
    assert.equal(calls, 1);
    assert.equal(viewer.images[0].comment, '🙂🙂🙂');
});

test('closing the lightbox while a save is pending updates the original image', async () => {
    let resolveRequest;
    const viewer = viewerWithFetch(() => new Promise((resolve) => { resolveRequest = resolve; }));
    viewer.lightboxOpen = true;
    const first = viewer.submitComment('one', 'new');
    viewer.closeLightbox();
    await new Promise((resolve) => setImmediate(resolve));
    resolveRequest({ ok: true });
    await first;
    assert.equal(viewer.lightboxOpen, false);
    assert.equal(viewer.images[0].comment, 'new');
});

test('completion waits for queued saves and does not proceed after a failed save', async () => {
    let resolveRequest;
    const paths = [];
    const viewer = viewerWithFetch((url) => {
        paths.push(url);
        return new Promise((resolve) => { resolveRequest = resolve; });
    });
    const selection = viewer.toggleSelect('one');
    await new Promise((resolve) => setImmediate(resolve));
    const complete = viewer.completeReview();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(paths.length, 1);
    resolveRequest({ ok: false, status: 409 });
    await Promise.all([selection, complete]);
    assert.equal(paths.length, 1);
    assert.equal(viewer.selectionError, 'limit reached');
    assert.equal(viewer.selectionCompleting, false);
});


test('browser selection and completion send only the guest CSRF proof, never a session bearer', async () => {
    const requests = [];
    const viewer = viewerWithFetch(async (url, options) => {
        requests.push({url, options});
        return {ok: true};
    });
    viewer.$root = {dataset: {
        token: 'gallery-share', guestCsrfToken: 'csrf-proof', galleryUrl: '/mount/g/gallery-share',
        images: JSON.stringify([{id: 'one', selected: false, favorited: false}]),
    }};
    viewer.init();
    assert.equal('sessionId' in viewer, false);
    await viewer.toggleSelect('one');
    await viewer.completeReview();
    assert.deepEqual(requests.map((request) => request.url), [
        '/mount/g/gallery-share/selections', '/mount/g/gallery-share/complete',
    ]);
    for (const request of requests) {
        assert.equal(request.options.headers['X-Guest-CSRF-Token'], 'csrf-proof');
        assert.equal('session_id' in JSON.parse(request.options.body), false);
        assert.equal('X-Guest-Session' in request.options.headers, false);
    }
});
