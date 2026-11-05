const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

function viewerWithFetch(fetch, extra = {}) {
    const context = vm.createContext({
        document: { addEventListener() {} },
        window: { location: { reload() {} } },
        fetch,
        URLSearchParams,
        ...extra,
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

function pagedViewer(fetch) {
    const viewer = viewerWithFetch(fetch);
    viewer.activeOrder = ['one'];
    viewer.nextCursor = 'page-two';
    viewer.filteredCount = 3;
    viewer.galleryUrl = '/mount/g/share';
    viewer.$root = { querySelector() { return { querySelector() { return null; } }; }, contains() { return true; } };
    return viewer;
}

function pageData(ids, cursor = null) {
    return { images: ids.map((id) => ({id, selected: false, favorited: false, comment: null})),
        next_cursor: cursor, query: {sort_by: 'sort_order', sort_dir: 'asc', filter: 'all'},
        total_images: 3, filtered_count: 3 };
}

test('page merge extends navigation, deduplicates IDs and preserves acknowledged edits', async () => {
    const viewer = pagedViewer(async () => ({ok: true}));
    await viewer.toggleSelect('one');
    await viewer.submitComment('one', 'acknowledged');
    viewer.acceptPage(pageData(['one', 'two'], 'page-three'), true);
    assert.deepEqual(Array.from(viewer.activeOrder), ['one', 'two']);
    assert.equal(viewer.images.length, 2);
    assert.equal(viewer.imageById.one.selected, true);
    assert.equal(viewer.imageById.one.comment, 'acknowledged');
    assert.equal(viewer.selectedCount, 1);
    viewer.acceptPage(pageData(['three']), false);
    assert.deepEqual(Array.from(viewer.activeOrder), ['three']);
    assert.equal(viewer.imageById.one.comment, 'acknowledged');
});

test('lightbox loads only at a boundary and previous navigation reuses loaded pages', async () => {
    const viewer = pagedViewer(async () => ({ok: true}));
    let loads = 0;
    viewer.loadNextPage = async () => { loads++; viewer.acceptPage(pageData(['two', 'three']), true); };
    assert.equal(viewer.hasNextImage, true);
    await viewer.nextImage();
    assert.equal(viewer.currentImage.id, 'two');
    assert.equal(loads, 1);
    viewer.prevImage();
    assert.equal(viewer.currentImage.id, 'one');
    await viewer.nextImage();
    await viewer.nextImage();
    assert.equal(viewer.currentImage.id, 'three');
    assert.equal(viewer.hasNextImage, false);
    assert.equal(loads, 1);
});

test('a filter change during a boundary load cannot advance the new view', async () => {
    const viewer = pagedViewer(async () => ({ok: true}));
    let release;
    viewer.loadNextPage = () => new Promise((resolve) => { release = resolve; });
    const advance = viewer.nextImage();
    viewer.pageGeneration++;
    viewer.acceptPage(pageData(['three']), false);
    release();
    await advance;
    assert.equal(viewer.currentImage.id, 'three');
    assert.equal(viewer.lightboxIndex, 0);
});

test('concurrent scroll and boundary requests share one fetch; stale pages never merge', async () => {
    const resolvers = [];
    let calls = 0;
    const viewer = viewerWithFetch(() => {
        calls++;
        return new Promise((resolve) => resolvers.push(resolve));
    }, { document: {
        addEventListener() {},
        createElement() {
            const template = {innerHTML: ''};
            template.content = {querySelector() {
                return {dataset: {guestPage: template.innerHTML}, remove() {}};
            }};
            return template;
        },
    }});
    viewer.activeOrder = ['one'];
    viewer.nextCursor = 'page-two';
    viewer.galleryUrl = '/mount/g/share';
    viewer.$root = {querySelector() { return {querySelector() { return null; }}; }};
    const scroll = viewer.loadNextPage();
    const boundary = viewer.nextImage();
    assert.equal(calls, 1);
    resolvers.shift()({ok: true, text: async () => JSON.stringify(pageData(['two'], 'page-three'))});
    await Promise.all([scroll, boundary]);
    assert.equal(viewer.currentImage.id, 'two');
    assert.equal(viewer.nextCursor, 'page-three');
    const stale = viewer.loadNextPage();
    assert.equal(calls, 2);
    viewer.pageGeneration++;
    viewer.acceptPage(pageData(['fresh']), false);
    resolvers.shift()({ok: true, text: async () => JSON.stringify(pageData(['stale']))});
    await stale;
    assert.deepEqual(Array.from(viewer.activeOrder), ['fresh']);
    assert.equal(viewer.imageById.stale, undefined);
});

function refreshRequest(viewer, filter, status, marked = false) {
    viewer.setFilterChoice(filter);
    const xhr = {status, getResponseHeader() { return marked ? 'before-effects' : null; }};
    const target = {id: 'image-grid'};
    viewer.beforePageRequest({detail: {xhr, target, elt: {hasAttribute() { return false; }}}});
    return {xhr, target, failed: status === 0 || status >= 400};
}

for (const status of [503, 500, 0]) {
    test(`terminal refresh failure ${status} restores accepted context and releases navigation`, async () => {
        const viewer = pagedViewer(async () => ({ok: true}));
        const controls = {filter: {value: 'all'}, sort_by: {value: 'sort_order'}, sort_dir: {value: 'asc'}};
        viewer.$root.querySelector = (selector) => controls[selector.match(/name="([^"]+)"/)?.[1]] || null;
        const request = refreshRequest(viewer, 'selected', status);
        controls.sort_dir.value = 'desc';
        viewer.afterPageRequest({detail: request});
        assert.equal(viewer._refreshing, false);
        assert.equal(viewer.pageQuery.filter, 'all');
        assert.equal(controls.filter.value, 'all');
        assert.equal(controls.sort_dir.value, 'asc');
        assert.equal(viewer.nextCursor, 'page-two');
        let loaded = false;
        viewer.loadNextPage = async function () {
            assert.equal(this._refreshing, false);
            assert.equal(this.pageQuery.filter, 'all');
            loaded = true;
            this.acceptPage(pageData(['two']), true);
        };
        await viewer.nextImage();
        assert.equal(loaded, true);
        assert.equal(viewer.currentImage.id, 'two');
    });
}

test('stale refresh failure cannot release or restore a newer request or accepted view', () => {
    const viewer = pagedViewer(async () => ({ok: true}));
    const first = refreshRequest(viewer, 'selected', 500);
    const second = refreshRequest(viewer, 'unrated', 200);
    viewer.afterPageRequest({detail: first});
    assert.equal(viewer._refreshing, true);
    assert.equal(viewer.pageQuery.filter, 'unrated');
    const page = pageData(['two']);
    page.query.filter = 'unrated';
    viewer.acceptPage(page, false);
    viewer._refreshing = false;
    viewer.afterPageRequest({detail: first});
    assert.equal(viewer._refreshing, false);
    assert.equal(viewer.pageQuery.filter, 'unrated');
    assert.equal(viewer.currentImage.id, 'two');
    viewer.afterPageRequest({detail: {...second, failed: false}});
    assert.equal(viewer.pageQuery.filter, 'unrated');
});

test('marked lookup response keeps refresh pending until its ordered retry', () => {
    const viewer = pagedViewer(async () => ({ok: true}));
    const request = refreshRequest(viewer, 'selected', 503, true);
    viewer.afterPageRequest({detail: {...request, failed: false}});
    assert.equal(viewer._refreshing, true);
    assert.equal(viewer.pageQuery.filter, 'selected');
    assert.equal(viewer._acceptedQuery.filter, 'all');
});

test('scheduled marked gallery lookup retry preserves context and drops superseded generations', () => {
    const callbacks = [];
    const handlers = {};
    const requests = [];
    const viewer = pagedViewer(async () => ({ok: true}));
    const root = {};
    const context = vm.createContext({
        document: {querySelector() { return null; }, addEventListener(name, handler) { handlers[name] = handler; }},
        window: {Alpine: {$data() { return viewer; }}},
        setTimeout(callback) { callbacks.push(callback); },
        htmx: {ajax(...args) { requests.push(args); }},
    });
    vm.runInContext(fs.readFileSync('frontend/static/js/lookup-retry.js', 'utf8'), context);
    const source = {isConnected: true, getAttribute() { return 'innerHTML'; }};
    const target = {isConnected: true, closest() { return root; }};
    const parameters = {filter: 'selected', sort_by: 'filename', sort_dir: 'desc'};
    const enqueue = () => handlers['htmx:beforeSwap']({detail: {
        xhr: {status: 503, __guestPageGeneration: viewer.pageGeneration,
            getResponseHeader() { return 'before-effects'; }},
        target, requestConfig: {verb: 'get', path: '/mount/g/share/gallery-images', elt: source,
            parameters, headers: {'HX-Request': 'true'}},
    }});
    enqueue();
    callbacks.shift()();
    assert.equal(requests.length, 1);
    assert.equal(requests[0][2].values, parameters);
    enqueue();
    viewer.pageGeneration++;
    callbacks.shift()();
    assert.equal(requests.length, 1, 'a stale scheduled retry must not replace the newer view');
});

test('filter chips consume class literals supplied by the scanned template', () => {
    const viewer = pagedViewer(async () => ({ok: true}));
    viewer.$root.dataset = {filterActiveClass: 'active classes', filterInactiveClass: 'inactive classes'};
    assert.equal(viewer.filterChipClass('all'), 'active classes');
    assert.equal(viewer.filterChipClass('selected'), 'inactive classes');
    viewer.setFilterChoice('selected');
    assert.equal(viewer.filterChipClass('selected'), 'active classes');
});
