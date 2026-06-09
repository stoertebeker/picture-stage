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

        startUpload() {
            this.uploading = true;
            this.uploadProgress = 0;
            // requestSubmit() triggers a real SubmitEvent that HTMX can
            // intercept and preventDefault. htmx.trigger(form, 'submit')
            // dispatches a CustomEvent, which races against the browser's
            // native form submission and causes a page reload.
            this.$refs.uploadForm.requestSubmit();
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
        totalImages: 0,
        selectedCount: 0,
        favoritedCount: 0,
        lightboxOpen: false,
        lightboxIndex: 0,
        showCompleteModal: false,
        completed: false,

        init() {
            const root = this.$root;
            this.token = root.dataset.token || '';
            this.sessionId = root.dataset.sessionId || '';
            this.images = JSON.parse(root.dataset.images || '[]');
            this.totalImages = parseInt(root.dataset.totalImages || '0', 10);
            this.selectedCount = parseInt(root.dataset.selectedCount || '0', 10);
            this.favoritedCount = parseInt(root.dataset.favoritedCount || '0', 10);
            // Once the review is completed the selection is read-only (gallery-wide).
            this.completed = root.dataset.sessionCompleted === 'true';
        },

        get currentImage() {
            return this.images[this.lightboxIndex] || null;
        },

        openLightbox(index) {
            this.lightboxIndex = index;
            this.lightboxOpen = true;
            this._preloadAdjacent();
        },

        closeLightbox() {
            this.lightboxOpen = false;
        },

        nextImage() {
            if (this.lightboxIndex < this.images.length - 1) {
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
                const img = this.images[idx];
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

        handleKeydown(e) {
            if (!this.lightboxOpen) return;
            if (e.key === 'ArrowRight') this.nextImage();
            else if (e.key === 'ArrowLeft') this.prevImage();
            else if (e.key === 'Escape') this.closeLightbox();
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
        previewSrc: '',
        previewFilename: '',

        init() {
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

        openPreview(src, filename) {
            this.previewSrc = src;
            this.previewFilename = filename;
            // The modal macro no longer carries x-ref (u3s); open by id.
            document.getElementById('previewModal').showModal();
        },

        submitName() {
            this.$refs.renameForm.querySelector('[name=name]').value = this.galleryName;
            // requestSubmit() — see note in uploadZone.startUpload().
            this.$refs.renameForm.requestSubmit();
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

// Cookie-consent banner. localStorage access lives here in JS for the same
// CSP reason as langSwitcher.
function cookieBannerComponent() {
    return {
        show: false,

        init() {
            this.show = !localStorage.getItem('cookie_consent');
        },

        accept() {
            localStorage.setItem('cookie_consent', 'accepted');
            this.show = false;
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
    Alpine.data('cookieBanner', cookieBannerComponent);
});
