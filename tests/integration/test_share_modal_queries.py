"""Owner share fragments do no image work, regardless of gallery size."""

import html
import re
from datetime import UTC, datetime

from sqlalchemy import event

from app.auth.passwords import hash_token, verify_password
from app.auth.tokens import create_access_token
from app.config import settings
from app.db.models import Gallery, GalleryStatus, Image, ImagePreview, ImageProcessingStatus, PreviewVariant
from app.frontend import galleries as frontend
from app.security.middleware import _mint_csrf_token
from tests.integration.conftest import test_engine


def _authenticate(client, user):
    token = create_access_token(str(user.id))
    csrf = _mint_csrf_token(token)
    client.cookies.set("session", token)
    client.cookies.set("csrf_token", csrf)
    return {"X-CSRF-Token": csrf, "HX-Request": "true"}


def _share_url(response):
    match = re.search(r'data-share-url="([^"]*)"', response.text)
    return html.unescape(match[1]) if match else None


def _credentials(gallery):
    return gallery.share_token, gallery.share_token_hash, gallery.share_token_salt, gallery.share_token_lookup


async def test_share_mutations_have_constant_queries_and_zero_media_work(client, db, owner, monkeypatch):
    monkeypatch.setattr(settings, "app_url", "https://photos.example.test/studio")
    headers = _authenticate(client, owner)
    expires_at = datetime(2031, 1, 2, 12, 30, tzinfo=UTC)
    galleries = []
    for count in (1, 501):
        gallery = Gallery(owner_id=owner.id, name=f"Share {count}", expires_at=expires_at)
        db.add(gallery)
        await db.flush()
        images = [
            Image(
                gallery_id=gallery.id,
                filename=f"{index}.jpg",
                storage_key=f"{gallery.id}/{index}.jpg",
                processing_status=ImageProcessingStatus.ready,
            )
            for index in range(count)
        ]
        db.add_all(images)
        await db.flush()
        db.add_all(
            [
                ImagePreview(
                    image_id=image.id,
                    variant=variant,
                    storage_key=f"{image.id}/{variant.value}.webp",
                    width=100,
                    height=100,
                )
                for image in images
                for variant in PreviewVariant
            ]
        )
        galleries.append(gallery)
    await db.commit()

    queries = []
    signed = []
    real_sign = frontend.sign_url

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    def sign(path, expires_in):
        signed.append(path)
        return real_sign(path, expires_in)

    monkeypatch.setattr(frontend, "sign_url", sign)
    counts = []
    for gallery in galleries:
        gallery_counts = []
        credentials = None
        for epoch, (method, action, password) in enumerate(
            (
                ("POST", "share", "initial-password"),
                ("POST", "password", "changed-password"),
                ("POST", "password", ""),
                ("DELETE", "share", None),
            ),
            start=1,
        ):
            queries.clear()
            signed.clear()
            event.listen(test_engine.sync_engine, "before_cursor_execute", capture)
            try:
                response = await client.request(
                    method,
                    f"/galleries/{gallery.id}/{action}",
                    data={"password": password} if password is not None else None,
                    headers=headers,
                )
            finally:
                event.remove(test_engine.sync_engine, "before_cursor_execute", capture)
            assert response.status_code == 200, response.text
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["cache-control"] == "private, no-cache"
            assert "hx-reswap" not in response.headers
            assert f'name="csrf_token" value="{headers["X-CSRF-Token"]}"' in response.text
            assert queries, "SQL capture must include the request's real gallery reads/writes"
            assert not [sql for sql in queries if re.search(r"\b(?:images|image_previews)\b", sql, re.I)]
            assert not signed
            gallery_counts.append(len(queries))

            await db.refresh(gallery)
            assert gallery.authorization_epoch == epoch
            assert gallery.expires_at == expires_at
            if method == "DELETE":
                assert gallery.status == GalleryStatus.draft
                assert _credentials(gallery) == (None, None, None, None)
                assert gallery.password_hash is None
                assert _share_url(response) is None
            else:
                assert gallery.status == GalleryStatus.shared
                assert _share_url(response) == f"https://photos.example.test/studio/g/{gallery.share_token}"
                if credentials is None:
                    credentials = _credentials(gallery)
                    assert all(credentials)
                else:
                    assert _credentials(gallery) == credentials
                if password:
                    assert verify_password(password, gallery.password_hash)
                else:
                    assert gallery.password_hash is None
        counts.append(gallery_counts)
    assert counts[0] == counts[1], "Share query counts must be independent of image/preview cardinality"


async def test_hash_only_share_output_and_mutation_access_checks(client, db, owner, other_user):
    digest, salt = hash_token("legacy-hash-only-token")
    gallery = Gallery(
        owner_id=owner.id,
        name="Legacy share",
        status=GalleryStatus.completed,
        share_token_hash=digest,
        share_token_salt=salt,
    )
    db.add(gallery)
    await db.commit()
    original = _credentials(gallery)
    headers = _authenticate(client, owner)
    for epoch, password in enumerate(("new-password", ""), start=1):
        response = await client.post(f"/galleries/{gallery.id}/password", data={"password": password}, headers=headers)
        assert response.status_code == 200
        # An active legacy hash does not provide a recoverable plaintext URL.
        assert _share_url(response) == ""
        assert f'hx-delete="/galleries/{gallery.id}/share"' in response.text
        assert f'hx-post="/galleries/{gallery.id}/password"' in response.text
        await db.refresh(gallery)
        assert gallery.status == GalleryStatus.completed
        assert gallery.authorization_epoch == epoch
        assert _credentials(gallery) == original

    for method, action in (("POST", "share"), ("DELETE", "share"), ("POST", "password")):
        rejected = await client.request(method, f"/galleries/{gallery.id}/{action}")
        assert rejected.status_code == 403
    intruder_headers = _authenticate(client, other_user)
    for method, action in (("POST", "share"), ("DELETE", "share"), ("POST", "password")):
        rejected = await client.request(method, f"/galleries/{gallery.id}/{action}", headers=intruder_headers)
        assert rejected.status_code == 404
    await db.refresh(gallery)
    assert gallery.authorization_epoch == 2
    assert _credentials(gallery) == original
