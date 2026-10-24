"""Gallery deletion against real LocalStorage files and PostgreSQL.

Unlike recording stubs, these tests upload real image bytes through the API,
check all three preview variants, and inspect the filesystem after deletion.
Failure and concurrency gaps are tracked separately in picture-stage-khfp,
picture-stage-qeqc and picture-stage-imux; these tests cover healthy storage.
"""

import io

import pytest
from PIL import Image as PillowImage
from sqlalchemy import select

from app.auth.tokens import create_access_token
from app.db.models import Gallery, GalleryStatus, Image, ImagePreview
from app.main import app
from app.security.middleware import _mint_csrf_token
from app.storage.dependencies import get_storage
from app.storage.local import LocalStorage


@pytest.fixture
def local_storage(tmp_path):
    storage = LocalStorage(str(tmp_path))
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        yield storage
    finally:
        app.dependency_overrides.pop(get_storage, None)


def _png_bytes():
    buf = io.BytesIO()
    PillowImage.new("RGB", (128, 96), "cornflowerblue").save(buf, format="PNG")
    return buf.getvalue()


async def _create_gallery(db, owner, name):
    gallery = Gallery(owner_id=owner.id, name=name, status=GalleryStatus.draft)
    db.add(gallery)
    await db.commit()
    return gallery


async def _upload_image(client, gallery, owner, auth_headers):
    response = await client.post(
        f"/api/v1/galleries/{gallery.id}/images",
        headers=auth_headers(owner),
        files={"files": ("original.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["uploaded"] == 1


async def _delete_gallery(client, gallery, owner, auth_headers, entrypoint):
    if entrypoint == "api":
        return await client.delete(f"/api/v1/galleries/{gallery.id}", headers=auth_headers(owner))
    session = create_access_token(str(owner.id))
    csrf = _mint_csrf_token(session)
    client.cookies.set("session", session)
    client.cookies.set("csrf_token", csrf)
    return await client.post(
        f"/galleries/{gallery.id}/delete",
        data={"confirm_delete": "yes", "csrf_token": csrf},
    )


def _files(storage, gallery):
    root = storage.base_path / str(gallery.id)
    return {str(path.relative_to(storage.base_path)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("entrypoint", ["frontend", "api"])
async def test_delete_removes_real_original_and_all_previews(
    client, db, owner, auth_headers, verify_db, local_storage, entrypoint
):
    gallery = await _create_gallery(db, owner, "Delete physical files")
    control = await _create_gallery(db, owner, "Keep physical files")
    await _upload_image(client, gallery, owner, auth_headers)
    await _upload_image(client, control, owner, auth_headers)
    before = _files(local_storage, gallery)
    retained = _files(local_storage, control)
    assert len(before) == 4
    assert len(retained) == 4
    assert sum("/originals/" in key for key in before) == 1
    assert sum("/previews/" in key for key in before) == 3

    response = await _delete_gallery(client, gallery, owner, auth_headers, entrypoint)
    assert response.status_code == (204 if entrypoint == "api" else 303)
    assert _files(local_storage, gallery) == {}
    assert _files(local_storage, control) == retained
    assert await verify_db.get(Gallery, gallery.id) is None
    assert await verify_db.get(Gallery, control.id) is not None
    assert (await verify_db.execute(select(Image).where(Image.gallery_id == gallery.id))).scalars().all() == []
    assert (
        (await verify_db.execute(select(ImagePreview).join(Image).where(Image.gallery_id == control.id)))
        .scalars()
        .all()
    )
