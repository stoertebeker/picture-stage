'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
    path.join(projectRoot, 'frontend', 'static', 'js', 'components.js'),
    'utf8',
);

let currentGrid;
const documentListeners = new Map();
const context = {
    document: {
        addEventListener(name, handler) {
            documentListeners.set(name, handler);
        },
        removeEventListener(name, handler) {
            if (documentListeners.get(name) === handler) documentListeners.delete(name);
        },
        getElementById(id) {
            return id === 'image-grid' ? currentGrid : null;
        },
    },
};
vm.createContext(context);
vm.runInContext(source, context, { filename: 'components.js' });

function grid(statuses, id = 'image-grid') {
    return {
        id,
        querySelectorAll(selector) {
            assert.equal(selector, '[data-image-id][data-processing-status]');
            return Object.entries(statuses).map(([imageId, processingStatus]) => ({
                dataset: { imageId, processingStatus },
            }));
        },
    };
}

function uploadZone(fileCount = 1) {
    const component = context.uploadZoneComponent();
    let submitted = 0;
    const uploadForm = {
        requestSubmit() {
            submitted += 1;
        },
    };
    component.$refs = {
        uploadForm,
        fileInput: {
            files: Array.from({ length: fileCount }, (_unused, index) => ({
                name: `new-${index}.jpg`,
            })),
            value: 'selected',
        },
    };
    component.$root = {
        dataset: {
            uploadingLabel: 'Uploading',
            processingLabel: 'Processing {ready} of {total}',
            processingResumedLabel: 'Creating previews...',
            completeLabel: 'Complete',
            uploadFailedLabel: 'Upload failed',
            processingFailedLabel: 'Processing failed',
        },
    };
    return { component, uploadForm, submitted: () => submitted };
}

function uploadEvent(form, successful = true, loaded = 50) {
    return { target: form, detail: { successful, total: 100, loaded } };
}

function pollElement() {
    return {
        getAttribute(name) {
            return name === 'hx-get' ? '/galleries/gallery-1/images-grid' : null;
        },
    };
}

function gridRequestEvent(element, xhr, requestConfig, target = element) {
    return {
        target: element,
        detail: { elt: element, xhr, requestConfig, target, shouldSwap: true },
        defaultPrevented: false,
        preventDefault() {
            this.defaultPrevented = true;
        },
    };
}

function dispatchDocumentEvent(name, event) {
    const handler = documentListeners.get(name);
    assert.equal(typeof handler, 'function', `${name} listener must be registered`);
    handler(event);
}

// A successful HTTP response starts preview processing; it is not completion.
currentGrid = grid({ existing: 'ready' });
const success = uploadZone();
success.component.startUpload();
assert.equal(success.component.state, 'uploading');
assert.equal(success.submitted(), 1);

success.component.onProgress(uploadEvent({}));
assert.equal(success.component.uploadProgress, 0, 'foreign HTMX progress must be ignored');
success.component.onProgress(uploadEvent(success.uploadForm));
assert.equal(success.component.uploadProgress, 50);
assert.equal(success.component.progressValue(), 25, 'network progress occupies the first half');
success.component.onProgress(uploadEvent(success.uploadForm, true, 100));
assert.equal(success.component.uploadProgress, 100);
assert.equal(success.component.progressValue(), 50, 'XHR completion is only halfway overall');

currentGrid = grid({ existing: 'ready', fresh: 'pending' });
success.component.onGridSwap({ detail: { target: currentGrid } });
success.component.onUploadComplete(uploadEvent({}));
assert.equal(success.component.state, 'uploading', 'foreign afterRequest must be ignored');
success.component.onUploadComplete(uploadEvent(success.uploadForm));
assert.equal(success.component.state, 'processing');
assert.deepEqual({ ...success.component.trackedImages }, { fresh: 'pending' });
assert.equal(success.component.isStatusVisible(), true);
assert.equal(success.component.statusText(), 'Processing 0 of 1');
assert.equal(success.component.progressValue(), 50, 'entering preview processing must not reset the bar');

// Only swaps of #image-grid may advance the batch.
currentGrid = grid({ existing: 'ready', fresh: 'ready' });
success.component.onGridSwap({ detail: { target: grid({}, 'unrelated-target') } });
assert.equal(success.component.state, 'processing');
assert.equal(success.component.trackedImages.fresh, 'pending');
success.component.onGridSwap({ detail: { target: currentGrid } });
assert.equal(success.component.state, 'complete');
assert.equal(success.component.progressValue(), 100);
assert.equal(success.component.statusRole(), 'status');

// A follow-up upload started while previews are still processing joins the
// existing batch. A stale response from the first request may omit the later
// image, but must neither forget it nor claim that the batch is complete.
currentGrid = grid({});
const merged = uploadZone();
merged.component.init();
merged.component.startUpload();
currentGrid = grid({ first: 'pending' });
merged.component.onGridSwap({ detail: { target: currentGrid } });
merged.component.onUploadComplete(uploadEvent(merged.uploadForm));
assert.equal(merged.component.state, 'processing');
assert.deepEqual({ ...merged.component.trackedImages }, { first: 'pending' });
const progressBeforeFollowUp = merged.component.progressValue();

const oldPollElement = pollElement();
const oldPollXhr = {};
const oldPollConfig = { elt: oldPollElement };
dispatchDocumentEvent(
    'htmx:beforeRequest',
    gridRequestEvent(oldPollElement, oldPollXhr, oldPollConfig),
);

merged.component.$refs.fileInput.files = [{ name: 'follow-up.jpg' }];
merged.component.$refs.fileInput.value = 'follow-up.jpg';
merged.component.startUpload();
assert.equal(merged.component.state, 'uploading');
assert.deepEqual({ ...merged.component.trackedImages }, { first: 'pending' });

const staleTerminalSwap = gridRequestEvent(
    oldPollElement,
    oldPollXhr,
    oldPollConfig,
    grid({ first: 'ready' }),
);
dispatchDocumentEvent('htmx:beforeSwap', staleTerminalSwap);
assert.equal(staleTerminalSwap.detail.shouldSwap, false);
assert.equal(staleTerminalSwap.defaultPrevented, true, 'stale poll must be rejected before DOM replacement');

const uploadSwap = gridRequestEvent(merged.uploadForm, {}, { elt: merged.uploadForm }, grid({ first: 'ready' }));
dispatchDocumentEvent('htmx:beforeSwap', uploadSwap);
assert.equal(uploadSwap.detail.shouldSwap, true, 'the active upload response must still be allowed to swap');
assert.equal(uploadSwap.defaultPrevented, false);

const duringUploadPollElement = pollElement();
const duringUploadPollXhr = {};
const duringUploadPollConfig = { elt: duringUploadPollElement };
dispatchDocumentEvent(
    'htmx:beforeRequest',
    gridRequestEvent(duringUploadPollElement, duringUploadPollXhr, duringUploadPollConfig),
);
const duringUploadPollSwap = gridRequestEvent(
    duringUploadPollElement,
    duringUploadPollXhr,
    duringUploadPollConfig,
    grid({ first: 'ready' }),
);
dispatchDocumentEvent('htmx:beforeSwap', duringUploadPollSwap);
assert.equal(duringUploadPollSwap.detail.shouldSwap, false);
assert.equal(duringUploadPollSwap.defaultPrevented, true, 'a poll launched during upload must not win the race');

currentGrid = grid({ first: 'pending', second: 'pending' });
merged.component.onGridSwap({ detail: { target: currentGrid } });
merged.component.onUploadComplete(uploadEvent(merged.uploadForm));
assert.equal(merged.component.state, 'processing');
assert.deepEqual(
    { ...merged.component.trackedImages },
    { first: 'pending', second: 'pending' },
    'the follow-up image must join the in-flight batch',
);
assert.ok(merged.component.progressValue() >= progressBeforeFollowUp);

currentGrid = grid({ first: 'ready' });
merged.component.onGridSwap({ detail: { target: currentGrid } });
assert.deepEqual(
    { ...merged.component.trackedImages },
    { first: 'ready', second: 'pending' },
    'an older response must not drop an image introduced by a later upload',
);
assert.equal(merged.component.state, 'processing');
assert.notEqual(merged.component.state, 'complete');

const currentPollElement = pollElement();
const currentPollXhr = {};
const currentPollConfig = { elt: currentPollElement };
dispatchDocumentEvent(
    'htmx:beforeRequest',
    gridRequestEvent(currentPollElement, currentPollXhr, currentPollConfig),
);
const currentPollSwap = gridRequestEvent(currentPollElement, currentPollXhr, currentPollConfig, currentGrid);
dispatchDocumentEvent('htmx:beforeSwap', currentPollSwap);
assert.equal(currentPollSwap.detail.shouldSwap, true, 'a current post-upload poll must be allowed to swap');
assert.equal(currentPollSwap.defaultPrevented, false);

const cleanupPollElement = pollElement();
const cleanupPollXhr = {};
const cleanupPollConfig = { elt: cleanupPollElement };
dispatchDocumentEvent(
    'htmx:beforeRequest',
    gridRequestEvent(cleanupPollElement, cleanupPollXhr, cleanupPollConfig),
);
assert.ok(cleanupPollXhr.__pictureStageUploadProgressContext);
dispatchDocumentEvent(
    'htmx:afterRequest',
    gridRequestEvent(cleanupPollElement, cleanupPollXhr, cleanupPollConfig),
);
assert.equal(
    cleanupPollXhr.__pictureStageUploadProgressContext,
    undefined,
    'completed poll context must be released',
);

currentGrid = grid({ first: 'ready', second: 'ready' });
merged.component.onGridSwap({ detail: { target: currentGrid } });
assert.equal(merged.component.state, 'complete');
merged.component.destroy();
assert.equal(documentListeners.has('htmx:beforeRequest'), false);
assert.equal(documentListeners.has('htmx:beforeSwap'), false);
assert.equal(documentListeners.has('htmx:afterRequest'), false);

// A failed preview is terminal for progress, but another pending preview must
// keep polling. Failure becomes final only after no tracked image is pending.
currentGrid = grid({ existing: 'ready' });
const failure = uploadZone(2);
failure.component.startUpload();
failure.component.onProgress(uploadEvent(failure.uploadForm, true, 100));
assert.equal(failure.component.progressValue(), 50);

currentGrid = grid({ existing: 'ready', broken: 'failed', slow: 'pending' });
failure.component.onGridSwap({ detail: { target: currentGrid } });
failure.component.onUploadComplete(uploadEvent(failure.uploadForm));
assert.equal(failure.component.state, 'processing');
assert.notEqual(failure.component.state, 'complete');
assert.equal(failure.component.progressValue(), 75, 'one of two terminal previews advances the second half');
assert.equal(failure.component.statusRole(), 'status');

currentGrid = grid({ existing: 'ready', broken: 'pending', slow: 'pending' });
failure.component.onGridSwap({ detail: { target: currentGrid } });
assert.equal(
    failure.component.trackedImages.broken,
    'failed',
    'a stale pending response must not regress an observed terminal failure',
);
assert.equal(failure.component.state, 'processing');
assert.equal(failure.component.progressValue(), 75);

currentGrid = grid({ existing: 'ready', broken: 'failed', slow: 'ready' });
failure.component.onGridSwap({ detail: { target: currentGrid } });
assert.equal(failure.component.state, 'failed');
assert.notEqual(failure.component.state, 'complete');
assert.equal(failure.component.progressValue(), 99, 'terminal failure must never present 100% success');
assert.equal(failure.component.statusText(), 'Processing failed');
assert.equal(failure.component.statusRole(), 'alert');

// A failed upload request is distinct from failed background processing and
// must never transition to processing or successful completion.
currentGrid = grid({ existing: 'ready' });
const uploadFailure = uploadZone();
uploadFailure.component.startUpload();
uploadFailure.component.onUploadComplete(uploadEvent(uploadFailure.uploadForm, false));
assert.equal(uploadFailure.component.state, 'failed');
assert.equal(uploadFailure.component._failureStage, 'upload');
assert.equal(uploadFailure.component._awaitingUploadGrid, false);
assert.equal(uploadFailure.component.statusText(), 'Upload failed');
assert.equal(uploadFailure.component.statusRole(), 'alert');
assert.notEqual(uploadFailure.component.progressValue(), 100);

// Reloading loses the identity of the active batch. Show generic processing
// while any gallery image is pending, without inventing exact counts or
// carrying an unrelated historical failure into the resumed result.
currentGrid = grid({ unfinished: 'pending', old: 'ready', historicalFailure: 'failed' });
const resumed = uploadZone();
resumed.component.init();
assert.equal(resumed.component.state, 'processing');
assert.equal(resumed.component._resumedProcessing, true);
assert.deepEqual({ ...resumed.component.trackedImages }, {});
assert.equal(resumed.component.progressValue(), null);
assert.equal(resumed.component.progressPercentText(), '');
assert.equal(resumed.component.progressStyle(), 'width: 100%');
assert.equal(resumed.component.progressBarClass(), 'bg-accent animate-pulse');
assert.equal(resumed.component.statusText(), 'Creating previews...');
assert.doesNotMatch(resumed.component.statusText(), /\d|\{ready\}|\{total\}/);
assert.notEqual(resumed.component.statusText(), 'Processing failed');

currentGrid = grid({ unfinished: 'ready', old: 'ready', historicalFailure: 'failed' });
resumed.component.onGridSwap({ detail: { target: currentGrid } });
assert.equal(resumed.component.state, 'idle');
assert.equal(resumed.component._resumedProcessing, false);
assert.equal(resumed.component.isStatusVisible(), false);
assert.notEqual(resumed.component.state, 'complete');
assert.notEqual(resumed.component.state, 'failed');

currentGrid = grid({ old: 'ready', historicalFailure: 'failed' });
const settledReload = uploadZone();
settledReload.component.init();
assert.equal(settledReload.component.state, 'idle');
assert.equal(settledReload.component._resumedProcessing, false);
