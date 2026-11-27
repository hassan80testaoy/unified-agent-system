"""Authentication, initial owner setup, 2FA TOTP, and user preferences endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.backlink.agent import seed_backlink_defaults
from app.agents.registry import discover_agents
from app.db import get_db
from app.models.core import User, Workspace, WorkspaceMember
from app.security.auth import (
    SESSION_COOKIE_NAME,
    AuthContext,
    create_access_token,
    encrypt_totp_secret,
    generate_totp_secret,
    get_current_auth,
    hash_password,
    record_audit,
    totp_provisioning_uri,
    verify_password,
    verify_totp_code,
)
from app.services.llm_router import seed_default_providers

router = APIRouter(tags=["auth"])


class SetupOwnerRequest(BaseModel):
    email: str = Field(min_length=4, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    workspace_name: str = Field(default="مكتبي للسيو", min_length=2, max_length=120)
    enable_2fa: bool = True


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class Verify2FARequest(BaseModel):
    totp_code: str = Field(min_length=6, max_length=8)


class UpdatePreferencesRequest(BaseModel):
    locale: str | None = None
    tz: str | None = None
    numeral_style: str | None = None
    calendar_type: str | None = None


@router.get("/api/auth/status")
def auth_status(db: Session = Depends(get_db)):
    has_owner = db.query(User).count() > 0
    return {"initialized": has_owner}


@router.post("/api/auth/setup")
def setup_owner(req: SetupOwnerRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "already_initialized",
                "message_ar": "تم إعداد حساب المالك مسبقاً",
                "message_en": "Owner account is already initialized",
            },
        )

    totp_secret = generate_totp_secret() if req.enable_2fa else None
    user = User(
        email=req.email.strip().lower(),
        password_hash=hash_password(req.password),
        totp_secret_enc=encrypt_totp_secret(totp_secret) if totp_secret else None,
        totp_enabled=False,  # User confirms first TOTP code on /auth/2fa/verify or can use immediately once verified
        role="owner",
        locale="ar",
        tz="Africa/Cairo",
    )
    db.add(user)
    db.flush()

    ws = Workspace(name=req.workspace_name.strip(), owner_id=user.id)
    db.add(ws)
    db.flush()

    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    seed_default_providers(db, ws.id)
    discover_agents(db, ws.id)
    seed_backlink_defaults(db, ws.id)

    record_audit(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        action="auth.setup_owner",
        target=user.email,
        ip=request.client.host if request.client else None,
    )
    db.commit()

    token = create_access_token(user.id, ws.id, "owner")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return {
        "user": {"id": user.id, "email": user.email, "role": user.role, "totp_enabled": user.totp_enabled},
        "workspace": {"id": ws.id, "name": ws.name},
        "access_token": token,
        "totp_setup": (
            {
                "secret": totp_secret,
                "provisioning_uri": totp_provisioning_uri(user.email, totp_secret),
            }
            if totp_secret
            else None
        ),
    }


@router.post("/api/auth/login")
def login(req: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email_norm = req.email.strip().lower()
    user = db.query(User).filter(User.email == email_norm).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_credentials",
                "message_ar": "بيانات الدخول غير صحيحة",
                "message_en": "Invalid credentials",
            },
        )

    now = datetime.now(timezone.utc)
    if user.locked_until:
        locked = user.locked_until if user.locked_until.tzinfo else user.locked_until.replace(tzinfo=timezone.utc)
        if locked > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "code": "account_locked",
                    "message_ar": "الحساب مقفل مؤقتاً بسبب محاولات خاطئة متكررة",
                    "message_en": "Account temporarily locked due to repeated failed attempts",
                },
            )

    if not verify_password(req.password, user.password_hash):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= 5:
            user.locked_until = now + timedelta(minutes=5)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_credentials",
                "message_ar": "بيانات الدخول غير صحيحة",
                "message_en": "Invalid credentials",
            },
        )

    if user.totp_enabled:
        if not req.totp_code or not verify_totp_code(user.totp_secret_enc, req.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "totp_required_or_invalid",
                    "message_ar": "مطلوب رمز التحقق الثنائي (2FA) الصحيح",
                    "message_en": "Valid 2FA TOTP code required",
                },
            )

    user.failed_login_attempts = 0
    user.locked_until = None
    member = db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).first()
    ws = db.get(Workspace, member.workspace_id) if member else db.query(Workspace).first()
    if not ws:
        raise HTTPException(status_code=500, detail={"code": "no_workspace", "message_ar": "لا توجد مساحة عمل", "message_en": "No workspace"})

    record_audit(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        action="auth.login",
        target=user.email,
        ip=request.client.host if request.client else None,
    )
    db.commit()

    token = create_access_token(user.id, ws.id, member.role if member else user.role)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return {
        "access_token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "totp_enabled": user.totp_enabled,
            "locale": user.locale,
            "tz": user.tz,
            "numeral_style": user.numeral_style,
            "calendar_type": user.calendar_type,
        },
        "workspace": {"id": ws.id, "name": ws.name},
    }


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@router.post("/api/auth/2fa/setup")
def setup_2fa(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    secret = generate_totp_secret()
    auth.user.totp_secret_enc = encrypt_totp_secret(secret)
    db.commit()
    return {
        "secret": secret,
        "provisioning_uri": totp_provisioning_uri(auth.user.email, secret),
    }


@router.post("/api/auth/2fa/verify")
def verify_2fa(req: Verify2FARequest, request: Request, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    if not verify_totp_code(auth.user.totp_secret_enc, req.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_totp",
                "message_ar": "رمز التحقق الثنائي غير صحيح",
                "message_en": "Invalid 2FA TOTP code",
            },
        )
    auth.user.totp_enabled = True
    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="auth.2fa_enabled",
        target=auth.user.email,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"totp_enabled": True}


@router.get("/api/me")
def get_me(auth: AuthContext = Depends(get_current_auth)):
    u = auth.user
    return {
        "user": {
            "id": u.id,
            "email": u.email,
            "role": auth.role,
            "totp_enabled": u.totp_enabled,
            "locale": u.locale,
            "tz": u.tz,
            "numeral_style": u.numeral_style,
            "calendar_type": u.calendar_type,
        },
        "workspace": {"id": auth.workspace.id, "name": auth.workspace.name},
    }


@router.patch("/api/me")
def update_me(req: UpdatePreferencesRequest, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    u = auth.user
    if req.locale in {"ar", "en"}:
        u.locale = req.locale
    if req.tz:
        u.tz = req.tz
    if req.numeral_style in {"western", "arabic_indic"}:
        u.numeral_style = req.numeral_style
    if req.calendar_type in {"gregorian", "hijri"}:
        u.calendar_type = req.calendar_type
    db.commit()
    return {"ok": True, "locale": u.locale, "tz": u.tz, "numeral_style": u.numeral_style, "calendar_type": u.calendar_type}
