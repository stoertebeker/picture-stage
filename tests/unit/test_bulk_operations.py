"""Tests for bulk operations.

Verifies that:
- Bulk-delete schema validates correctly
- Bulk-delete endpoint checks gallery ownership
- Gallery-duplicate creates fresh gallery (draft, no share token)
- Gallery-duplicate copies storage files (not shared keys)
- Storage backends implement copy method
- Duplicate endpoint checks gallery ownership
"""

import uuid


class TestBulkDeleteSchema:
    """Verify bulk-delete request/response schemas."""

    def test_valid_request(self) -> None:
        from app.images.schemas import BulkDeleteRequest
        req = BulkDeleteRequest(image_ids=[uuid.uuid4(), uuid.uuid4()])
        assert len(req.image_ids) == 2

    def test_empty_ids_accepted(self) -> None:
        from app.images.schemas import BulkDeleteRequest
        req = BulkDeleteRequest(image_ids=[])
        assert req.image_ids == []

    def test_response_has_deleted_field(self) -> None:
        from app.images.schemas import BulkDeleteResponse
        resp = BulkDeleteResponse(deleted=5)
        assert resp.deleted == 5


class TestBulkDeleteEndpoint:
    """Verify bulk-delete endpoint structure."""

    def test_endpoint_exists(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()
        assert "bulk-delete" in source

    def test_checks_gallery_ownership(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()
        assert "Gallery.owner_id == user.id" in source

    def test_filters_images_by_gallery(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()
        assert "Image.gallery_id == gallery_id" in source

    def test_deletes_storage_files(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()
        idx = source.index("bulk_delete")
        block = source[idx:idx + 1500]
        assert "storage.delete" in block

    def test_returns_deleted_count(self) -> None:
        with open("app/images/router.py") as f:
            source = f.read()
        assert "BulkDeleteResponse" in source


class TestGalleryDuplicateSchema:
    """Verify duplicate request schema."""

    def test_name_optional(self) -> None:
        from app.galleries.schemas import GalleryDuplicateRequest
        req = GalleryDuplicateRequest()
        assert req.name is None

    def test_custom_name(self) -> None:
        from app.galleries.schemas import GalleryDuplicateRequest
        req = GalleryDuplicateRequest(name="Meine Kopie")
        assert req.name == "Meine Kopie"


class TestGalleryDuplicateEndpoint:
    """Verify duplicate endpoint structure."""

    def test_endpoint_exists(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "duplicate" in source

    def test_checks_gallery_ownership(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "_get_owned_gallery" in source

    def test_creates_new_gallery_as_draft(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        idx = source.index("duplicate_gallery")
        block = source[idx:idx + 2000]
        assert "Gallery(" in block
        assert "share_token" not in block.split("Gallery(")[1].split(")")[0]

    def test_copies_storage_files(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        idx = source.index("duplicate_gallery")
        block = source[idx:idx + 2000]
        assert "storage.copy" in block

    def test_generates_new_storage_keys(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        idx = source.index("duplicate_gallery")
        block = source[idx:idx + 2000]
        assert "new_original_key" in block
        assert "new_preview_key" in block

    def test_default_name_has_kopie_suffix(self) -> None:
        with open("app/galleries/router.py") as f:
            source = f.read()
        assert "(Kopie)" in source


class TestStorageCopyMethod:
    """Verify storage backends implement copy."""

    def test_local_storage_has_copy(self) -> None:
        from app.storage.local import LocalStorage
        assert hasattr(LocalStorage, "copy")

    def test_s3_storage_has_copy(self) -> None:
        from app.storage.s3 import S3Storage
        assert hasattr(S3Storage, "copy")

    def test_base_storage_declares_copy(self) -> None:
        from app.storage.base import StorageBackend
        assert hasattr(StorageBackend, "copy")
