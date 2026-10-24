"""Structural tests for the owner gallery detail templates."""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = PROJECT_ROOT / "app" / "templates" / "galleries"


def _template(name: str) -> str:
    return (TEMPLATE_ROOT / name).read_text()


def _render_image_grid(*statuses: str) -> str:
    """Render the owner grid with lightweight image objects.

    This exercises the Jinja condition that controls polling instead of only
    asserting that the condition happens to be present in the template source.
    """
    environment = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "app" / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    template = environment.get_template("galleries/_image_grid.html")
    images = [
        SimpleNamespace(
            id=f"image-{index}",
            processing_status=status,
            previews={"thumb_md": f"/media/image-{index}.webp"},
            filename=f"photo-{index}.jpg",
            sort_order=index,
        )
        for index, status in enumerate(statuses)
    ]
    return template.render(
        gallery=SimpleNamespace(id="gallery-1"),
        images=images,
        t=lambda key, **_kwargs: key,
    )


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
    assert "onUploadComplete($event)" in upload
    assert "onUploadComplete(" in components_js


def test_upload_progress_handlers_are_scoped_and_csp_safe() -> None:
    """Only the grid swap is global, and every inline handler is a method call.

    Progress and request completion events originate on the upload form, so
    listening on ``window`` would let unrelated HTMX requests mutate this
    component.  Grid swaps happen outside its DOM subtree and therefore retain
    a filtered window listener.
    """
    upload = _template("_upload.html")

    assert '@htmx:xhr:progress="onProgress($event)"' in upload
    assert "@htmx:xhr:progress.window=" not in upload
    assert '@htmx:after-request="onUploadComplete($event)"' in upload
    assert "@htmx:after-request.window=" not in upload
    assert '@htmx:after-swap.window="onGridSwap($event)"' in upload

    # Keep @alpinejs/csp expressions as method calls: no assignment,
    # concatenation, optional chaining, globals, or chained statements.
    for expression in (
        "onProgress($event)",
        "onUploadComplete($event)",
        "onGridSwap($event)",
        "isStatusVisible()",
        "statusText()",
        "progressPercentText()",
        "progressValue()",
        "progressStyle()",
        "progressBarClass()",
        "statusRole()",
        "statusLive()",
    ):
        assert f'="{expression}"' in upload


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
    assert 'data-gallery-name="{{ gallery.name }}"' in delete
    assert 'x-data="galleryDelete"' in delete


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


def test_rendered_image_grid_exports_processing_state_and_only_polls_pending() -> None:
    """Issue #3: the upload UI consumes each image's id and terminal state.

    A failed preview is terminal just like a ready preview: neither may keep the
    grid polling forever.  A mixed grid still polls while any image is pending.
    """
    pending = _render_image_grid("ready", "pending", "failed")
    assert 'data-image-id="image-0"' in pending
    assert 'data-processing-status="ready"' in pending
    assert 'data-image-id="image-1"' in pending
    assert 'data-processing-status="pending"' in pending
    assert 'data-image-id="image-2"' in pending
    assert 'data-processing-status="failed"' in pending
    assert 'hx-trigger="every 2s"' in pending

    for terminal_status in ("ready", "failed"):
        terminal = _render_image_grid(terminal_status, terminal_status)
        assert 'hx-trigger="every 2s"' not in terminal
        assert 'hx-get="/galleries/gallery-1/images-grid"' not in terminal


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


def test_upload_preview_progress_i18n_keys_exist_in_de_and_en() -> None:
    """Every state-machine message is translated in both supported locales."""
    import json

    translations = {
        locale: json.loads((PROJECT_ROOT / "app" / "i18n" / f"{locale}.json").read_text())["gallery"]
        for locale in ("de", "en")
    }
    for key in (
        "upload_progress",
        "upload_processing",
        "upload_processing_resumed",
        "upload_complete",
        "upload_failed",
        "upload_processing_failed",
    ):
        for locale, gallery in translations.items():
            assert key in gallery, f"{locale.upper()} missing gallery.{key}"
            assert gallery[key].strip(), f"{locale.upper()} gallery.{key} is empty"

    for locale, gallery in translations.items():
        processing = gallery["upload_processing"]
        assert "{ready}" in processing, f"{locale.upper()} progress omits ready count"
        assert "{total}" in processing, f"{locale.upper()} progress omits total count"


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


def test_selection_page_offers_lightroom_copy_and_downloads() -> None:
    """The selection result page (r84) exposes a copy-for-Lightroom button plus
    txt/csv downloads, all scoped to the 'marked' filter (selected OR favorite)."""
    selection = _template("selection.html")

    # Copy-for-Lightroom button: payload via data-* (autoescaped), copied via x-text/x-show.
    assert 'x-data="copyButton"' in selection
    assert 'data-copy-text="{{ filename_list }}"' in selection
    assert '@click="copy()"' in selection
    assert 'x-show="copied"' in selection
    assert "gallery.selection_copy_lightroom" in selection
    assert "gallery.selection_copied" in selection

    # Downloads use the marked filter so untouched images are excluded.
    assert "/export?format=txt&filter=marked" in selection
    assert "/export?format=csv&filter=marked" in selection

    # Both badges (selected + favorite) and the filename are present.
    assert "gallery.selection_selected" in selection
    assert "gallery.selection_favorited" in selection
    assert "{{ item.filename }}" in selection


def test_detail_links_to_selection_when_shared_or_completed() -> None:
    """The 'view selection' link appears only for shared/completed galleries."""
    detail = _template("detail.html")

    assert "st in ['shared', 'completed']" in detail
    assert 'href="/galleries/{{ gallery.id }}/selection"' in detail
    assert "gallery.selection_view" in detail


def test_selection_page_i18n_keys_exist() -> None:
    """All i18n keys referenced by the selection page exist in DE and EN."""
    import json

    de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())["gallery"]
    en = json.loads((PROJECT_ROOT / "app" / "i18n" / "en.json").read_text())["gallery"]
    for key in (
        "selection_title",
        "selection_view",
        "back_to_gallery",
        "selection_marked",
        "selection_selected",
        "selection_favorited",
        "selection_copy_lightroom",
        "selection_copied",
        "selection_download_txt",
        "selection_lightroom_hint",
        "selection_empty_title",
        "selection_empty_text",
    ):
        assert key in de, f"DE missing gallery.{key}"
        assert key in en, f"EN missing gallery.{key}"


def test_owner_grid_filter_chips_are_wired() -> None:
    """3rl: the owner filter chips (Alle/Ausgewählt/Favoriten) are functional —
    client-side Alpine, not decorative <span>s. Each chip sets the active filter
    and reflects it via chipClass(); the root carries the selection map and each
    tile is gated by isVisible()."""
    detail = _template("detail.html")
    image_grid = _template("_image_grid.html")
    components_js = (PROJECT_ROOT / "frontend" / "static" / "js" / "components.js").read_text()

    # Chips are buttons with click + reactive class, reusing the guest i18n keys.
    assert "@click=\"setFilter('all')\"" in detail
    assert "@click=\"setFilter('selected')\"" in detail
    assert "@click=\"setFilter('favorited')\"" in detail
    assert "chipClass('all')" in detail
    assert "chipClass('selected')" in detail
    assert "chipClass('favorited')" in detail
    assert "guest.filter_all" in detail
    assert "guest.filter_selected" in detail
    assert "guest.filter_favorited" in detail

    # Selection data rides a data-* attribute as JSON (autoescaped, never code).
    assert "data-selections='{{ selection_map | tojson }}'" in detail

    # Each tile is gated by the client filter.
    assert "x-show=\"isVisible('{{ img.id }}')\"" in image_grid

    # The component implements the filter API used by the templates.
    assert "activeFilter" in components_js
    assert "setFilter(" in components_js
    assert "isVisible(" in components_js
    assert "chipClass(" in components_js
    assert "dataset.selections" in components_js


def test_owner_grid_filter_backend_provides_selection_map() -> None:
    """3rl: the owner detail/grid routes must thread a selection_map into the
    context so the client filter has data; the helper is owner-scoped via the
    already owner-checked gallery."""
    galleries_py = (PROJECT_ROOT / "app" / "frontend" / "galleries.py").read_text()

    assert "async def _load_selection_map(" in galleries_py
    assert "selection_map" in galleries_py
    # All three routes that render the grid/detail pass the map.
    assert galleries_py.count("_load_selection_map(gallery_id, db)") >= 3
