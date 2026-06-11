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
    image_grid = _template("_image_grid.html")
    share = _template("_share_modal.html")
    delete = _template("_delete_modal.html")

    assert "| tojson" in image_grid
    # _share_modal now passes the share URL via a data-* attribute: Jinja
    # autoescapes attribute values and Alpine reads it through $root.dataset
    # (as data, never evaluated as code) — u3s CSP migration.
    assert 'data-share-url="{{ share_url' in share
    assert 'x-data="shareUrl"' in share
    assert "gallery.name | tojson" in delete


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
