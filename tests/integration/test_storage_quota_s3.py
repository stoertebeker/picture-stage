"""Actual S3 adapter with an in-memory client; no external bucket or network."""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.main import app
from app.storage.dependencies import get_storage
from app.storage.s3 import S3Storage

from .test_gallery_storage_cleanup import _create_gallery, _png_bytes
from .test_storage_quota import _usage


class _S3Client:
    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.copies = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def upload_fileobj(self, data, bucket, key, **kwargs):
        self.uploads.append(key)
        self.objects[key] = data.read()

    async def copy_object(self, *, Bucket, CopySource, Key):
        self.copies.append(Key)
        self.objects[Key] = self.objects[CopySource["Key"]]

    async def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture
def s3_storage():
    client = _S3Client()
    storage = S3Storage()
    storage.session = SimpleNamespace(client=lambda **kwargs: client)
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_storage, None)


async def test_s3_upload_and_copy_cannot_exceed_same_owner_allowance(
    client, db, owner, auth_headers, monkeypatch, s3_storage
):
    gallery = await _create_gallery(db, owner, "S3 quota")
    url = f"/api/v1/galleries/{gallery.id}"
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 4)
    response = await client.post(
        f"{url}/images", headers=auth_headers(owner), files={"files": ("photo.png", _png_bytes(), "image/png")}
    )
    assert response.status_code == 201, response.text
    retained = dict(s3_storage.objects)
    assert len(retained) == 4
    response = await client.post(
        f"{url}/images", headers=auth_headers(owner), files={"files": ("photo.png", _png_bytes(), "image/png")}
    )
    assert response.status_code == 409
    assert s3_storage.objects == retained
    assert len(s3_storage.uploads) == 4
    monkeypatch.setattr(settings, "max_storage_objects_per_owner", 5)
    response = await client.post(f"{url}/duplicate", headers=auth_headers(owner), json={})
    assert response.status_code == 409
    assert len(s3_storage.copies) == 1
    assert s3_storage.objects == retained
    assert await _usage(owner.id) == (sum(map(len, retained.values())), 4, 0)
    response = await client.delete(url, headers=auth_headers(owner))
    assert response.status_code == 204
    assert s3_storage.objects == {}
    assert await _usage(owner.id) == (0, 0, 0)
