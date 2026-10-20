"""Regression tests for password authorization on guest data routes."""

import re
import uuid

from app.auth.passwords import hash_password, hash_token
from app.db.models import (
    Gallery,
    GalleryStatus,
    Image,
    ImagePreview,
    PreviewVariant,
    SelectionAction,
    SelectionEvent,
    ShareSession,
)


async def _create_gallery(db, owner, token: str, *, protected: bool) -> tuple[Gallery, Image]:
    token_hash, token_salt = hash_token(token)
    gallery = Gallery(
        owner_id=owner.id,
        name="Private proofing",
        status=GalleryStatus.completed,
        share_token_hash=token_hash,
        share_token_salt=token_salt,
        password_hash=hash_password("correct horse battery staple") if protected else None,
    )
    db.add(gallery)
    await db.flush()

    image = Image(
        gallery_id=gallery.id,
        storage_key="proofs/secret-proof.jpg",
        filename="secret-proof.jpg",
        content_type="image/jpeg",
        sort_order=1,
    )
    db.add(image)
    await db.flush()
    db.add(
        ImagePreview(
            image_id=image.id,
            variant=PreviewVariant.preview,
            storage_key="proofs/previews/secret-proof.webp",
            width=1200,
            height=800,
        )
    )
    await db.commit()
    await db.refresh(gallery)
    await db.refresh(image)
    return gallery, image


async def test_protected_guest_data_routes_require_gallery_bound_session(client, db, owner) -> None:
    token = "protected-gallery-token"
    _gallery, image = await _create_gallery(db, owner, token, protected=True)

    unlock = await client.post(
        f"/g/{token}/verify-password",
        data={"password": "correct horse battery staple"},
    )
    assert unlock.status_code == 200
    session_match = re.search(r'data-session-id="([0-9a-f-]+)"', unlock.text)
    assert session_match is not None
    valid_session_id = session_match.group(1)

    db.add(
        SelectionEvent(
            image_id=image.id,
            share_session_id=uuid.UUID(valid_session_id),
            action=SelectionAction.comment,
            comment="private review note",
        )
    )
    await db.commit()

    other_gallery = Gallery(owner_id=owner.id, name="Other gallery", status=GalleryStatus.shared)
    db.add(other_gallery)
    await db.flush()
    foreign_session = ShareSession(gallery_id=other_gallery.id)
    db.add(foreign_session)
    await db.commit()
    await db.refresh(foreign_session)

    paths = (
        f"/g/{token}/gallery-images",
        f"/g/{token}/images",
        f"/g/{token}/selections",
    )
    for path in paths:
        missing = await client.get(path)
        assert missing.status_code == 403
        assert "secret-proof.jpg" not in missing.text
        assert "private review note" not in missing.text
        assert "/media/" not in missing.text

        foreign = await client.get(path, params={"session_id": str(foreign_session.id)})
        assert foreign.status_code == 403
        assert "secret-proof.jpg" not in foreign.text
        assert "private review note" not in foreign.text
        assert "/media/" not in foreign.text

        unlocked = await client.get(path, params={"session_id": valid_session_id})
        assert unlocked.status_code == 200


async def test_passwordless_guest_data_routes_still_allow_token_only_reads(client, db, owner) -> None:
    token = "passwordless-gallery-token"
    await _create_gallery(db, owner, token, protected=False)

    for path in (
        f"/g/{token}/gallery-images",
        f"/g/{token}/images",
        f"/g/{token}/selections",
    ):
        response = await client.get(path)
        assert response.status_code == 200
