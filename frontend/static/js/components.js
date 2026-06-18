/* Alpine.js component registry for picture-stage.
 *
 * Components are registered via Alpine.data() inside the `alpine:init` event,
 * so templates use the bare `x-data="componentName"` syntax (no parentheses).
 * This pattern is required by the @alpinejs/csp build (no eval of inline
 * expressions) and works identically under the standard build. Initial state
 * that depends on server-rendered values is read from data-* attributes on the
 * x-data root element during init(). Never put a `<script>` block inside a
 * Jinja template (CSP blocks it). See docs/design/build.md.
 */

// Upload zone for gallery detail. No server-rendered initial state.
function uploadZoneComponent() {
    return {
        dragOver: false,
        uploading: false,
        uploadProgress: 0,

        handleDrop(event) {
            this.dragOver = false;
            const files = event.dataTransfer.files;
            if (files.length > 0) {
                this.$refs.fileInput.files = files;
                this.startUpload();
            }
        },

        handleFiles(event) {
            if (event.target.files.length > 0) {
                this.startUpload();
            }
        },

        // Math.round lives here in JS — globals are forbidden in inline
        // expressions under the @alpinejs/csp build.
        onProgress(event) {
            this.uploadProgress = Math.round((event.detail.loaded / event.detail.total) * 100);
        },

        startUpload() {
            this.uploading = true;
            this.uploadProgress = 0;
            // requestSubmit() triggers a real SubmitEvent that HTMX can
            // intercept and preventDefault. htmx.trigger(form, 'submit')
            // dispatches a CustomEvent, which races against the browser's
            // native form submission and causes a page reload.
            this.$refs.uploadForm.requestSubmit();
        },

        // Reset after the HTMX upload request settles. Lives here because the
        // @alpinejs/csp build cannot parse multi-statement inline expressions
        // (uploadProgress = 0; uploading = false threw "Unexpected token").
        onUploadComplete() {
            this.uploadProgress = 0;
            this.uploading = false;
        },
    };
};

// Guest viewer: image grid, lightbox, selection/favorite toggling, complete flow.
// Initial state read from data-* attributes on the root <div x-data="guestViewer">.
function guestViewerComponent() {
    return {
        token: '',
        sessionId: '',
        images: [],
        imageById: {},
        // Ordered id list the lightbox navigates (rfii). Mirrors the current
        // grid view (filter + sort); the server ships it via data-image-order
        // and refreshes it on every filter/sort swap (see setActiveOrder).
        activeOrder: [],
        totalImages: 0,
        selectedCount: 0,
        favoritedCount: 0,
        lightboxOpen: false,
        lightboxIndex: 0,
        showCompleteModal: false,
        completed: false,
        showSwipeHint: false,
        showShortcutHelp: false,
        _swipeHintTimer: null,

        init() {
            const root = this.$root;
            this.token = root.dataset.token || '';
            this.sessionId = root.dataset.sessionId || '';
            this.images = JSON.parse(root.dataset.images || '[]');
            // id -> image map for O(1) lookups from the (progressively loaded,
            // id-keyed) grid items (am9). The grid no longer uses array indices.
            this.imageById = {};
            this.images.forEach((img) => {
                this.imageById[img.id] = img;
            });
            // Initial navigation order = the unfiltered list in sort_order.
            // Falls back to the full image order if the attribute is missing.
            const order = JSON.parse(root.dataset.imageOrder || '[]');
            this.activeOrder = order.length ? order : this.images.map((img) => img.id);
            this.totalImages = parseInt(root.dataset.totalImages || '0', 10);
            this.selectedCount = parseInt(root.dataset.selectedCount || '0', 10);
            this.favoritedCount = parseInt(root.dataset.favoritedCount || '0', 10);
            // Once the review is completed the selection is read-only (gallery-wide).
            this.completed = root.dataset.sessionCompleted === 'true';
        },

        get currentImage() {
            return this.imageById[this.activeOrder[this.lightboxIndex]] || null;
        },

        // Replace the lightbox navigation order after a filter/sort grid refresh
        // (called from the htmx:afterSwap handler in app.js, rfii). If the
        // lightbox is open, keep showing the current image when it survives the
        // new filter, otherwise clamp the index into range.
        setActiveOrder(ids) {
            if (!Array.isArray(ids)) return;
            const currentId = this.activeOrder[this.lightboxIndex];
            this.activeOrder = ids;
            if (!this.lightboxOpen) return;
            const idx = ids.indexOf(currentId);
            if (idx >= 0) {
                this.lightboxIndex = idx;
            } else {
                this.lightboxIndex = Math.min(this.lightboxIndex, ids.length - 1);
                if (this.lightboxIndex < 0) this.closeLightbox();
            }
        },

        // Null-safe grid lookups by image id. The grid is loaded progressively
        // (am9), so array indices are not stable across pages — items reference
        // their image by id. Reading the mapped object's .selected keeps Alpine's
        // reactivity intact (same object instance as in this.images).
        isSelectedById(id) {
            const img = this.imageById[id];
            return !!(img && img.selected);
        },

        isFavoritedById(id) {
            const img = this.imageById[id];
            return !!(img && img.favorited);
        },

        openLightboxById(id) {
            const idx = this.activeOrder.indexOf(id);
            if (idx >= 0) this.openLightbox(idx);
        },

        openLightbox(index) {
            this.lightboxIndex = index;
            this.lightboxOpen = true;
            this._preloadAdjacent();
            this._showSwipeHintBriefly();
        },

        // Swipe hint (mobile) is a one-off nudge: show it when a photo opens,
        // then fade it out after a few seconds so it does not linger (jwc).
        _showSwipeHintBriefly() {
            if (this._swipeHintTimer) {
                clearTimeout(this._swipeHintTimer);
            }
            this.showSwipeHint = true;
            this._swipeHintTimer = setTimeout(() => {
                this.showSwipeHint = false;
                this._swipeHintTimer = null;
            }, 3500);
        },

        closeLightbox() {
            this.lightboxOpen = false;
        },

        nextImage() {
            if (this.lightboxIndex < this.activeOrder.length - 1) {
                this.lightboxIndex++;
                this._preloadAdjacent();
            }
        },

        prevImage() {
            if (this.lightboxIndex > 0) {
                this.lightboxIndex--;
                this._preloadAdjacent();
            }
        },

        // Preload the adjacent images so navigation feels instant.
        _preloadAdjacent() {
            const targets = [this.lightboxIndex + 1, this.lightboxIndex - 1];
            targets.forEach((idx) => {
                const img = this.imageById[this.activeOrder[idx]];
                if (!img) return;
                const url = img.preview_url || img.thumb_md_url;
                if (!url) return;
                const preload = new Image();
                preload.src = url;
            });
        },

        // Swipe gestures on mobile. Threshold = 50px horizontal,
        // vertical movement must be smaller (otherwise it's a scroll).
        _touchStartX: null,
        _touchStartY: null,

        handleTouchStart(e) {
            if (!e.touches || e.touches.length !== 1) return;
            this._touchStartX = e.touches[0].clientX;
            this._touchStartY = e.touches[0].clientY;
        },

        handleTouchEnd(e) {
            if (this._touchStartX === null) return;
            const t = e.changedTouches && e.changedTouches[0];
            if (!t) {
                this._touchStartX = null;
                return;
            }
            const dx = t.clientX - this._touchStartX;
            const dy = t.clientY - this._touchStartY;
            this._touchStartX = null;
            this._touchStartY = null;
            if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
            if (dx < 0) this.nextImage();
            else this.prevImage();
        },

        async toggleSelect(imageId) {
            if (this.completed) return;
            const img = this.images.find((i) => i.id === imageId);
            if (!img) return;
            const action = img.selected ? 'deselect' : 'select';
            img.selected = !img.selected;
            this.selectedCount += img.selected ? 1 : -1;
            await this._postSelection(imageId, action);
        },

        async toggleFavorite(imageId) {
            if (this.completed) return;
            const img = this.images.find((i) => i.id === imageId);
            if (!img) return;
            const action = img.favorited ? 'unfavorite' : 'favorite';
            img.favorited = !img.favorited;
            this.favoritedCount += img.favorited ? 1 : -1;
            await this._postSelection(imageId, action);
        },

        async submitComment(imageId, comment) {
            if (this.completed) return;
            await this._postSelection(imageId, 'comment', comment);
        },

        async _postSelection(imageId, action, comment) {
            const body = {
                image_id: imageId,
                action: action,
                session_id: this.sessionId,
            };
            if (comment !== undefined) body.comment = comment;
            await fetch(`/g/${this.token}/selections`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        },

        async completeReview() {
            await fetch(`/g/${this.token}/complete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: this.sessionId }),
            });
            this.showCompleteModal = false;
            window.location.reload();
        },

        // Desktop keyboard shortcuts (9q3.6). Active only while the lightbox is
        // open. Two guards keep this safe:
        //   1. When a text field (the comment input) is focused, the action
        //      shortcuts (j/f/c/?) must NOT fire — otherwise typing a "j" would
        //      both insert the character AND toggle the selection. Escape only
        //      releases focus in that case.
        //   2. The action shortcuts respect the read-only `completed` gate, so
        //      no write path slips past the lock via the keyboard.
        handleKeydown(e) {
            if (!this.lightboxOpen) return;

            // The help overlay toggles with "?" and is dismissed first by Escape.
            if (e.key === '?') {
                this.showShortcutHelp = !this.showShortcutHelp;
                return;
            }
            if (this.showShortcutHelp && e.key === 'Escape') {
                this.showShortcutHelp = false;
                return;
            }

            // Focus guard: while the comment field is focused, only Escape acts
            // (releasing focus); typing must not trigger action shortcuts.
            const t = e.target;
            const inField = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
            if (inField) {
                if (e.key === 'Escape') t.blur();
                return;
            }

            if (e.key === 'ArrowRight') this.nextImage();
            else if (e.key === 'ArrowLeft') this.prevImage();
            else if (e.key === 'Escape') this.closeLightbox();
            else if (e.key === 'j' || e.key === 'J') this._shortcutSelect();
            else if (e.key === 'f' || e.key === 'F') this._shortcutFavorite();
            else if (e.key === 'c' || e.key === 'C') this._shortcutComment(e);
        },

        _shortcutSelect() {
            if (this.completed || !this.currentImage) return;
            this.toggleSelect(this.currentImage.id);
        },

        _shortcutFavorite() {
            if (this.completed || !this.currentImage) return;
            this.toggleFavorite(this.currentImage.id);
        },

        // "c" focuses the comment field. preventDefault stops the "c" from being
        // typed into the field we are about to focus.
        _shortcutComment(e) {
            if (this.completed || !this.currentImage) return;
            e.preventDefault();
            if (this.$refs.commentInput) this.$refs.commentInput.focus();
        },
    };
};

// Gallery manager: rename, bulk selection, image preview modal.
// Initial state read from data-gallery-name on root.
function galleryManagerComponent() {
    return {
        editing: false,
        galleryName: '',
        selectedImages: [],

        // Owner-grid filter (3rl). Client-side: the grid loads all images at
        // once (no pagination), so filtering by the model's marks is instant
        // with no server round-trip. `selections` is a {id: {selected,
        // favorited}} map of marked images only (unmarked images are absent).
        activeFilter: 'all',
        selections: {},

        // Read-only lightbox state (x4o). Mirrors the guest viewer's navigation
        // (arrows / keyboard / swipe) but without select/favorite/comment.
        images: [],
        lightboxOpen: false,
        lightboxIndex: 0,
        showSwipeHint: false,
        _swipeHintTimer: null,
        _touchStartX: null,
        _touchStartY: null,

        init() {
            this.galleryName = this.$root.dataset.galleryName || '';
            // Only 'ready' images carry preview URLs; pending/failed are excluded
            // server-side. Fresh uploads appear after a full page reload (TODO).
            this.images = JSON.parse(this.$root.dataset.images || '[]');
            this.selections = JSON.parse(this.$root.dataset.selections || '{}');
        },

        // --- Owner-grid filter (3rl) ---

        setFilter(filter) {
            this.activeFilter = filter;
        },

        // Active/inactive chip styling. Computed in JS to keep the inline
        // expression a CSP-safe method call (no comparison operators in
        // templates under @alpinejs/csp).
        chipClass(filter) {
            const base = 'rounded-full border px-3 py-1 text-xs font-medium transition-colors';
            if (this.activeFilter === filter) {
                return base + ' border-accent bg-surface-raised text-accent';
            }
            return base + ' border-border-subtle text-text-secondary hover:border-border-strong hover:text-text-primary';
        },

        isVisible(imageId) {
            if (this.activeFilter === 'all') return true;
            const state = this.selections[imageId];
            if (!state) return false;
            if (this.activeFilter === 'favorited') return state.favorited;
            return state.selected;
        },

        startEditing() {
            this.editing = true;
            // $nextTick with an arrow lives here in JS — the @alpinejs/csp build
            // rejects arrow functions inside inline expressions.
            this.$nextTick(() => this.$refs.nameInput.focus());
        },

        cancelEditing() {
            this.editing = false;
            this.galleryName = this.$root.dataset.galleryName || '';
        },

        toggleImage(imageId) {
            const idx = this.selectedImages.indexOf(imageId);
            if (idx === -1) {
                this.selectedImages.push(imageId);
            } else {
                this.selectedImages.splice(idx, 1);
            }
        },

        isSelected(imageId) {
            return this.selectedImages.includes(imageId);
        },

        selectAll() {
            const checkboxes = document.querySelectorAll('[data-image-id]');
            this.selectedImages = Array.from(checkboxes).map((el) => el.dataset.imageId);
        },

        submitName() {
            this.$refs.renameForm.querySelector('[name=name]').value = this.galleryName;
            // requestSubmit() — see note in uploadZone.startUpload().
            this.$refs.renameForm.requestSubmit();
        },

        // --- Lightbox (read-only) ---

        // The lightbox navigates the *visible* subset, not all images: with a
        // filter active (selected/favorited), next/prev must skip hidden tiles
        // (ml0). lightboxIndex is therefore an index into visibleImages, kept
        // consistent across open/next/prev/preload. Recomputed on access; the
        // filter chips sit behind the overlay, so the subset is stable while
        // the lightbox is open.
        get visibleImages() {
            return this.images.filter((img) => this.isVisible(img.id));
        },

        get currentImage() {
            return this.visibleImages[this.lightboxIndex] || null;
        },

        openLightboxById(id) {
            const idx = this.visibleImages.findIndex((i) => i.id === id);
            if (idx >= 0) this.openLightbox(idx);
        },

        openLightbox(index) {
            this.lightboxIndex = index;
            this.lightboxOpen = true;
            this._preloadAdjacent();
            this._showSwipeHintBriefly();
        },

        closeLightbox() {
            this.lightboxOpen = false;
        },

        nextImage() {
            if (this.lightboxIndex < this.visibleImages.length - 1) {
                this.lightboxIndex++;
                this._preloadAdjacent();
            }
        },

        prevImage() {
            if (this.lightboxIndex > 0) {
                this.lightboxIndex--;
                this._preloadAdjacent();
            }
        },

        // Preload the adjacent images so navigation feels instant.
        _preloadAdjacent() {
            const visible = this.visibleImages;
            const targets = [this.lightboxIndex + 1, this.lightboxIndex - 1];
            targets.forEach((idx) => {
                const img = visible[idx];
                if (!img) return;
                const url = img.preview_url || img.thumb_md_url;
                if (!url) return;
                const preload = new Image();
                preload.src = url;
            });
        },

        // Swipe hint (mobile) is a one-off nudge: show on open, fade after a
        // few seconds so it does not linger (jwc pattern).
        _showSwipeHintBriefly() {
            if (this._swipeHintTimer) {
                clearTimeout(this._swipeHintTimer);
            }
            this.showSwipeHint = true;
            this._swipeHintTimer = setTimeout(() => {
                this.showSwipeHint = false;
                this._swipeHintTimer = null;
            }, 3500);
        },

        // Swipe gestures on mobile. Threshold = 50px horizontal; vertical
        // movement must be smaller (otherwise it's a scroll).
        handleTouchStart(e) {
            if (!e.touches || e.touches.length !== 1) return;
            this._touchStartX = e.touches[0].clientX;
            this._touchStartY = e.touches[0].clientY;
        },

        handleTouchEnd(e) {
            if (this._touchStartX === null) return;
            const t = e.changedTouches && e.changedTouches[0];
            if (!t) {
                this._touchStartX = null;
                return;
            }
            const dx = t.clientX - this._touchStartX;
            const dy = t.clientY - this._touchStartY;
            this._touchStartX = null;
            this._touchStartY = null;
            if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
            if (dx < 0) this.nextImage();
            else this.prevImage();
        },

        handleKeydown(e) {
            if (!this.lightboxOpen) return;
            if (e.key === 'ArrowRight') this.nextImage();
            else if (e.key === 'ArrowLeft') this.prevImage();
            else if (e.key === 'Escape') this.closeLightbox();
        },
    };
}

// Language switcher in the top nav. Reads the active language from the `lang`
// cookie. document.* access lives here in JS (not in an inline expression),
// which the @alpinejs/csp build forbids in templates.
function langSwitcherComponent() {
    return {
        currentLang: 'de',

        init() {
            const match = document.cookie.match(/lang=(\w+)/);
            this.currentLang = (match && match[1]) || 'de';
        },
    };
}

// Audit-log event-type filter. window.location navigation lives here in JS
// (not in an inline expression, which the @alpinejs/csp build forbids).
function auditFilterComponent() {
    return {
        navigate(value) {
            const base = this.$root.dataset.auditUrl || '';
            window.location.href = value ? `${base}?event_type=${encodeURIComponent(value)}` : base;
        },
    };
}

// Share-URL copy button. navigator.clipboard + setTimeout live here in JS.
function shareUrlComponent() {
    return {
        copied: false,
        shareUrl: '',

        init() {
            this.shareUrl = this.$root.dataset.shareUrl || '';
        },

        copy() {
            navigator.clipboard.writeText(this.shareUrl).then(() => {
                this.copied = true;
                setTimeout(() => {
                    this.copied = false;
                }, 2000);
            });
        },
    };
}

// Generic copy-to-clipboard button (r84: copy the marked filenames for
// Lightroom). Reads the payload from data-copy-text and shows a brief "copied"
// state. navigator.* / setTimeout live here in JS, not in inline expressions
// (the @alpinejs/csp build forbids them in templates).
function copyButtonComponent() {
    return {
        copied: false,
        text: '',

        init() {
            this.text = this.$root.dataset.copyText || '';
        },

        copy() {
            navigator.clipboard.writeText(this.text).then(() => {
                this.copied = true;
                setTimeout(() => {
                    this.copied = false;
                }, 2000);
            });
        },
    };
}

// "Einstellungen" dropdown in the top nav (theme toggle + language switcher).
// Logic lives in methods/getters because the @alpinejs/csp build forbids
// inline expressions. `expanded` returns a string so :aria-expanded renders
// "false" instead of dropping the attribute when closed.
function settingsMenuComponent() {
    return {
        open: false,

        toggle() {
            this.open = !this.open;
        },

        close() {
            this.open = false;
        },

        get expanded() {
            return this.open ? 'true' : 'false';
        },
    };
}

// Register components before Alpine boots. components.js is loaded before
// alpine.min.js (see base.html / guest_base.html), so the alpine:init event
// fires after this listener is attached.
document.addEventListener('alpine:init', () => {
    Alpine.data('uploadZone', uploadZoneComponent);
    Alpine.data('guestViewer', guestViewerComponent);
    Alpine.data('galleryManager', galleryManagerComponent);
    Alpine.data('langSwitcher', langSwitcherComponent);
    Alpine.data('auditFilter', auditFilterComponent);
    Alpine.data('shareUrl', shareUrlComponent);
    Alpine.data('copyButton', copyButtonComponent);
    Alpine.data('settingsMenu', settingsMenuComponent);
});
