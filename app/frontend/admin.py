from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_user_from_cookie
from app.db.models import PendingSignup, User, UserStatus
from app.db.session import get_db
from app.frontend.deps import templates

router = APIRouter(tags=["frontend-admin"])


async def require_admin_page(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    user = await get_user_from_cookie(request, db)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if user.status != UserStatus.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/admin/signups", response_class=HTMLResponse)
async def admin_pending_signups(
    request: Request,
    user: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(PendingSignup).order_by(PendingSignup.requested_at.desc())
    )
    signups = list(result.scalars().all())
    csrf_token = request.cookies.get("csrf_token", "")
    return templates.TemplateResponse(
        "admin/pending.html",
        {
            "request": request,
            "user": user,
            "signups": signups,
            "csrf_token": csrf_token,
        },
    )


@router.post("/admin/approve/{signup_id}", response_class=HTMLResponse)
async def admin_approve_signup(
    signup_id: int,
    request: Request,
    _admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(PendingSignup).where(PendingSignup.id == signup_id)
    )
    signup = result.scalar_one_or_none()
    if signup is None:
        raise HTTPException(status_code=404, detail="Pending signup not found")

    existing = await db.execute(select(User).where(User.email == signup.email))
    if existing.scalar_one_or_none() is not None:
        await db.delete(signup)
        await db.commit()
        raise HTTPException(
            status_code=409, detail="User with this email already exists"
        )

    user = User(
        email=signup.email,
        password_hash=signup.password_hash,
        status=UserStatus.active,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.delete(signup)
    await db.commit()

    return HTMLResponse("")


@router.post("/admin/reject/{signup_id}", response_class=HTMLResponse)
async def admin_reject_signup(
    signup_id: int,
    request: Request,
    _admin: User = Depends(require_admin_page),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(PendingSignup).where(PendingSignup.id == signup_id)
    )
    signup = result.scalar_one_or_none()
    if signup is None:
        raise HTTPException(status_code=404, detail="Pending signup not found")

    await db.delete(signup)
    await db.commit()

    return HTMLResponse("")
