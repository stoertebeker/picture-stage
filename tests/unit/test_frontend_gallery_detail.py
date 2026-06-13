"""Structural tests for the owner gallery detail templates."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = PROJECT_ROOT / "app" / "templates" / "galleries"


def _template(name: str) -> str:
    return (TEMPLATE_ROOT / name).read_text()


def test_gallery_detail_preserves_htmx_contracts() -> None:
    detail = _template("detail.html")

    assert 'hx-post="/galleries/{{ gallery.id }}/rename"' in detail
    assert 'hx-post="/galleries/{{ gallery.id }}/status"' in detail
    assert 'hx-post="/galleries/{{ gallery.id }}/expiry"' in detail
    assert 'hx-post="/galleries/{{ gallery.id }}/bulk-delete"' in detail
    assert 'hx-target="#image-grid"' in detail
    assert 'name="csrf_token"' in detail


def test_gallery_partials_preserve_share_upload_delete_contracts() -> None:
    upload = _template("_upload.html")
    share = _template("_share_modal.html")
    delete = _template("_delete_modal.html")

    assert 'hx-post="/galleries/{{ gallery.id }}/upload"' in upload
    assert 'hx-target="#image-grid"' in upload
    assert 'hx-delete="/galleries/{{ gallery.id }}/share"' in share
    assert 'hx-target="#share-section"' in share
    assert 'action="/galleries/{{ gallery.id }}/delete"' in delete
    assert 'name="csrf_token"' in upload
    assert 'name="csrf_token"' in share
    assert 'name="csrf_token"' in delete


def test_upload_handler_avoids_multi_statement_inline_expression() -> None:
    """Upload reset must call a method, not inline multi-statements (picture-stage-3uh).

    The @alpinejs/csp build cannot parse multi-statement inline expressions
    (uploadProgress = 0; uploading = false threw "CSP Parser Error: Unexpected
    token: uploading"), silently breaking the upload-progress reset. The reset
    must go through uploadZone.onUploadComplete() instead.
    """
    upload = _template("_upload.html")
    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()

    assert "uploadProgress = 0; uploading = false" not in upload
    assert "onUploadComplete()" in upload
    assert "onUploadComplete(" in components_js


def test_gallery_templates_have_no_multi_statement_alpine_handlers() -> None:
    """No @-handler in the owner templates may chain statements with ';'.

    Multi-statement inline expressions are rejected by the @alpinejs/csp parser
    (see picture-stage-3uh). Single assignments like ``open = !open`` are fine;
    only ';'-separated chains break. Guards the whole owner template tree.
    """
    import re

    offenders = []
    for path in TEMPLATE_ROOT.glob("*.html"):
        for m in re.finditer(r'@[\w:.@-]+="([^"]*)"', path.read_text()):
            if ";" in m.group(1):
                offenders.append(f"{path.name}: {m.group(1)}")
    assert not offenders, f"multi-statement Alpine handlers (CSP-incompatible): {offenders}"


def test_gallery_detail_uses_editorial_dark_tokens() -> None:
    detail = _template("detail.html")
    share = _template("_share_modal.html")
    image_grid = _template("_image_grid.html")

    assert "bg-surface-base" in detail
    assert "font-display" in detail
    assert "border-border-subtle" in detail
    assert "text-text-primary" in share
    assert "bg-surface-raised" in image_grid


def test_gallery_detail_uses_i18n_for_new_copy() -> None:
    detail = _template("detail.html")
    share = _template("_share_modal.html")

    assert "gallery.upload_heading" in detail
    assert "gallery.image_grid_heading" in detail
    assert "gallery.expiry_hint" in detail
    assert "gallery.watermark_heading" in detail
    assert "gallery.share_visible_text" in share
    assert "gallery.share_password_hint" in share


def test_gallery_detail_escapes_alpine_embedded_values() -> None:
    detail = _template("detail.html")
    image_grid = _template("_image_grid.html")
    share = _template("_share_modal.html")
    delete = _template("_delete_modal.html")

    # Owner lightbox (x4o): the grid opens images by UUID id only — no
    # user-controlled value is embedded in an inline expression. Image data,
    # including user-controlled filenames, rides a data-* attribute as JSON
    # (Jinja-autoescaped) and is rendered via Alpine x-text, never as code.
    assert "openLightboxById('{{ img.id }}')" in image_grid
    assert "data-images='{{ lightbox_images | tojson }}'" in detail
    # _share_modal now passes the share URL via a data-* attribute: Jinja
    # autoescapes attribute values and Alpine reads it through $root.dataset
    # (as data, never evaluated as code) — u3s CSP migration.
    assert 'data-share-url="{{ share_url' in share
    assert 'x-data="shareUrl"' in share
    assert "gallery.name | tojson" in delete


def test_owner_lightbox_has_readonly_navigation() -> None:
    """The owner lightbox (x4o) mirrors the guest viewer's navigation but is
    read-only: no select/favorite/comment controls, only browse + close."""
    detail = _template("detail.html")
    lightbox = _template("_owner_lightbox.html")

    # Wired into the gallery page within the galleryManager scope.
    assert '{% include "galleries/_owner_lightbox.html" %}' in detail

    # Navigation: arrows, keyboard, swipe.
    assert 'x-show="lightboxOpen"' in lightbox
    assert '@click="prevImage()"' in lightbox
    assert '@click="nextImage()"' in lightbox
    assert '@click="closeLightbox()"' in lightbox
    assert "handleKeydown($event)" in lightbox
    assert "handleTouchStart($event)" in lightbox
    assert "handleTouchEnd($event)" in lightbox

    # Filename via x-text (never evaluated as code).
    assert 'x-text="currentImage.filename"' in lightbox

    # Read-only: none of the guest selection controls leak in.
    assert "toggleSelect" not in lightbox
    assert "toggleFavorite" not in lightbox
    assert "submitComment" not in lightbox


def test_image_grid_polls_only_while_previews_pending() -> None:
    """The grid self-polls /images-grid every 2s, gated on a pending image.

    The trigger lives inside a `{% if pending_count %}` block so it disappears
    once all images settle — that is what stops the polling loop (picture-stage-o4d).
    """
    image_grid = _template("_image_grid.html")

    assert "selectattr('processing_status', 'equalto', 'pending')" in image_grid
    assert "if pending_count" in image_grid
    assert 'hx-get="/galleries/{{ gallery.id }}/images-grid"' in image_grid
    assert 'hx-trigger="every 2s"' in image_grid
    assert 'hx-target="#image-grid"' in image_grid


def test_image_grid_branches_on_processing_status() -> None:
    """Each tile renders by status: ready -> thumbnail, pending -> spinner, failed -> error."""
    image_grid = _template("_image_grid.html")

    assert "img.processing_status == 'ready'" in image_grid
    assert "img.processing_status == 'failed'" in image_grid
    # Spinner + error copy come from i18n, not hardcoded strings.
    assert "gallery.processing" in image_grid
    assert "gallery.processing_failed" in image_grid
    # Status semantics for assistive tech.
    assert 'role="status"' in image_grid
    assert 'role="alert"' in image_grid


def test_share_modal_can_update_password_without_rotating_token() -> None:
    """1y5: with an active share link the modal offers set/change/remove of the
    gallery password via its own endpoint — NOT via re-share, which would
    rotate the token and kill the magic link already sent to the model."""
    share = _template("_share_modal.html")
    galleries_py = (PROJECT_ROOT / "app" / "frontend" / "galleries.py").read_text()

    # Update + remove forms post to the dedicated password endpoint.
    assert 'hx-post="/galleries/{{ gallery.id }}/password"' in share
    assert "gallery.password_remove_confirm" in share
    assert "gallery.password_set_badge" in share
    assert "gallery.password_not_set_badge" in share
    assert "gallery.password_update_hint" in share

    # Endpoint exists, is owner-scoped and never touches the share token.
    assert '@router.post("/galleries/{gallery_id}/password"' in galleries_py
    endpoint_start = galleries_py.index("async def set_gallery_password")
    endpoint_end = galleries_py.index("@router.post", endpoint_start)
    endpoint_block = galleries_py[endpoint_start:endpoint_end]
    assert "_get_owned_gallery" in endpoint_block
    assert "share_token" not in endpoint_block
    assert "hash_password(password) if password else None" in endpoint_block


def test_share_modal_password_i18n_keys_exist() -> None:
    """All i18n keys referenced by the password section exist in DE and EN."""
    import json

    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())["gallery"]
    en = json.loads((PROJECT_ROOT / "app" / "i18n" / "en.json").read_text())["gallery"]
    for key in (
        "password_set_badge",
        "password_not_set_badge",
        "password_update_hint",
        "password_save",
        "password_remove",
        "password_remove_confirm",
    ):
        assert key in de, f"DE missing gallery.{key}"
        assert key in en, f"EN missing gallery.{key}"
