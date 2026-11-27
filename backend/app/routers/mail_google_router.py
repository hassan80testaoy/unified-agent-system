"""Mailboxes (IMAP read-only + Fernet) & Owner Google Account + Verification-Code Ask endpoints (§2.3)."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.google_account import GoogleAccount, GoogleSession, VerificationRequest
from app.models.mailboxes import Mailbox
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.security.crypto import decrypt_secret
from app.services.google_account import (
    create_google_account,
    disconnect_google_account,
    get_google_account_activity,
    revoke_google_session,
    test_google_account,
    verification_coordinator,
)
from app.services.mailbox import add_or_update_mailbox, disconnect_mailbox, test_imap_connection

router = APIRouter(tags=["mailboxes_google"])


class MailboxConnectRequest(BaseModel):
    email: str = Field(min_length=4, max_length=255)
    app_password: str = Field(min_length=1, max_length=255)
    imap_host: str | None = None
    imap_port: int = 993
    test_on_connect: bool = True


class GoogleAccountCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=5, max_length=255)
    password: str | None = None
    oauth_refresh_token: str | None = None
    allow_gmail_send: bool = False


class GoogleAccountPatchRequest(BaseModel):
    label: str | None = None
    status: str | None = None  # active | disabled


class GoogleAccountTestRequest(BaseModel):
    app_password_override: str | None = None


class VerificationCodeSubmitRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    request_id: str | None = None


@router.get("/api/mailboxes")
def list_mailboxes(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(Mailbox).filter(Mailbox.workspace_id == auth.workspace.id).order_by(Mailbox.created_at.desc()).all()
    # Secrets are NEVER returned in responses
    return {
        "mailboxes": [
            {
                "id": m.id,
                "email": m.email,
                "provider": m.provider,
                "imap_host": m.imap_host,
                "imap_port": m.imap_port,
                "supports_plus": m.supports_plus,
                "status": m.status,
                "last_tested_at": m.last_tested_at.isoformat() if m.last_tested_at else None,
                "last_error": m.last_error,
            }
            for m in rows
        ]
    }


@router.post("/api/mailboxes")
def connect_mailbox(
    req: MailboxConnectRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        mb = add_or_update_mailbox(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            email_addr=req.email,
            app_password=req.app_password,
            imap_host=req.imap_host,
            imap_port=req.imap_port,
            run_connection_test=req.test_on_connect,
            ip=request.client.host if request.client else None,
        )
        return {
            "id": mb.id,
            "email": mb.email,
            "provider": mb.provider,
            "imap_host": mb.imap_host,
            "supports_plus": mb.supports_plus,
            "status": mb.status,
            "last_error": mb.last_error,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "mailbox_error", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.post("/api/mailboxes/{mailbox_id}/test")
def test_mailbox(
    mailbox_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    mb = db.get(Mailbox, mailbox_id)
    if not mb or mb.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الصندوق غير موجود", "message_en": "Mailbox not found"})
    if not mb.password_enc:
        return {"ok": False, "error": "Mailbox is disconnected"}
    pw = decrypt_secret(mb.password_enc)
    ok, err = test_imap_connection(mb.email, pw, mb.imap_host, mb.imap_port)
    del pw
    mb.status = "ok" if ok else "failed"
    mb.last_tested_at = datetime.now(timezone.utc)
    mb.last_error = err
    db.commit()
    return {"ok": ok, "status": mb.status, "error": err}


@router.delete("/api/mailboxes/{mailbox_id}")
def remove_mailbox(
    mailbox_id: str,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    mb = db.get(Mailbox, mailbox_id)
    if not mb or mb.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الصندوق غير موجود", "message_en": "Mailbox not found"})
    disconnect_mailbox(db, mailbox=mb, user_id=auth.user.id, ip=request.client.host if request.client else None)
    return {"ok": True, "status": mb.status}


@router.get("/api/google-accounts")
def list_google_accounts(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(GoogleAccount).filter(GoogleAccount.workspace_id == auth.workspace.id).order_by(GoogleAccount.created_at.desc()).all()
    sessions = {
        s.google_account_id: s
        for s in db.query(GoogleSession).filter(GoogleSession.workspace_id == auth.workspace.id, GoogleSession.status == "active").all()
    }
    return {
        "google_accounts": [
            {
                "id": g.id,
                "label": g.label,
                "email_masked": g.email_masked,
                "has_password": bool(g.password_enc),
                "has_oauth": bool(g.oauth_refresh_token_enc),
                "oauth_scopes": g.oauth_scopes,
                "imap_ok": g.imap_ok,
                "oauth_ok": g.oauth_ok,
                "supports_plus": g.supports_plus,
                "status": g.status,
                "consecutive_blocks": g.consecutive_blocks,
                "active_profile_id": sessions[g.id].browser_profile_id if g.id in sessions else None,
                "created_at": g.created_at.isoformat(),
                "last_used_at": g.last_used_at.isoformat() if g.last_used_at else None,
            }
            for g in rows
        ]
    }


@router.post("/api/google-accounts")
def add_google_account(
    req: GoogleAccountCreateRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    acct = create_google_account(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        label=req.label,
        email_addr=req.email,
        password=req.password,
        oauth_refresh_token=req.oauth_refresh_token,
        allow_gmail_send=req.allow_gmail_send,
        ip=request.client.host if request.client else None,
    )
    return {
        "id": acct.id,
        "label": acct.label,
        "email_masked": acct.email_masked,
        "status": acct.status,
        "oauth_scopes": acct.oauth_scopes,
    }


@router.patch("/api/google-accounts/{account_id}")
def patch_google_account(
    account_id: str,
    req: GoogleAccountPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    acct = db.get(GoogleAccount, account_id)
    if not acct or acct.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Google account not found"})
    if req.label:
        acct.label = req.label.strip()
    if req.status in {"active", "disabled"}:
        acct.status = req.status
    db.commit()
    return {"id": acct.id, "label": acct.label, "status": acct.status}


@router.post("/api/google-accounts/{account_id}/test")
def test_google_account_endpoint(
    account_id: str,
    req: GoogleAccountTestRequest | None = None,
    request: Request = None,  # type: ignore[assignment]
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    acct = db.get(GoogleAccount, account_id)
    if not acct or acct.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Google account not found"})
    return test_google_account(
        db,
        account=acct,
        user_id=auth.user.id,
        app_password_override=req.app_password_override if req else None,
        ip=request.client.host if request and request.client else None,
    )


@router.post("/api/google-accounts/{account_id}/disconnect")
def disconnect_google_account_endpoint(
    account_id: str,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    acct = db.get(GoogleAccount, account_id)
    if not acct or acct.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Google account not found"})
    disconnect_google_account(db, account=acct, user_id=auth.user.id, ip=request.client.host if request.client else None)
    return {"ok": True, "status": acct.status}


@router.post("/api/google-accounts/{account_id}/revoke-session")
def revoke_google_session_endpoint(
    account_id: str,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    acct = db.get(GoogleAccount, account_id)
    if not acct or acct.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Google account not found"})
    revoke_google_session(db, account=acct, user_id=auth.user.id, ip=request.client.host if request.client else None)
    return {"ok": True}


@router.get("/api/google-accounts/{account_id}/activity")
def get_google_activity_endpoint(account_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    acct = db.get(GoogleAccount, account_id)
    if not acct or acct.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Google account not found"})
    return {"activity": get_google_account_activity(db, auth.workspace.id, acct)}


@router.get("/api/google-accounts/oauth/url")
def google_oauth_consent_url(allow_send: bool = False, auth: AuthContext = Depends(require_roles("owner", "admin"))):
    settings = get_settings()
    if not settings.google_oauth_client_id:
        return {
            "configured": False,
            "message_ar": "أضف GOOGLE_OAUTH_CLIENT_ID في ملف .env على السيرفر لتفعيل ربط OAuth مباشر.",
            "message_en": "Set GOOGLE_OAUTH_CLIENT_ID in .env on the server to enable direct OAuth consent.",
        }
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    if allow_send:
        scopes.append("https://www.googleapis.com/auth/gmail.send")
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
    }
    return {
        "configured": True,
        "consent_url": f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}",
    }


@router.post("/api/verification-codes/{run_id}")
def submit_verification_code_endpoint(
    run_id: str,
    req: VerificationCodeSubmitRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    """Receive one-time verification code from user, relay to active browser/task waiter, NEVER store or log."""
    try:
        delivered = verification_coordinator.deliver_code(run_id, req.code)
        # Never put req.code in audit details or response
        record_audit(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            action="verification_code.submitted",
            target=run_id,
            ip=request.client.host if request.client else None,
            details={"delivered": delivered, "request_id": req.request_id},
        )
        db.commit()
        return {
            "delivered": delivered,
            "message_ar": "تم إرسال الكود للتشغيل واستخدامه مرة واحدة بدون تخزين." if delivered else "لا يوجد انتظار نشط لهذا التشغيل حالياً.",
            "message_en": "Code delivered for single-use and wiped from memory." if delivered else "No active waiter for this run.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_code_format", "message_ar": str(exc), "message_en": str(exc)}) from exc


@router.get("/api/verification-requests")
def list_verification_requests(
    run_id: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    q = db.query(VerificationRequest).filter(VerificationRequest.workspace_id == auth.workspace.id)
    if run_id:
        q = q.filter(VerificationRequest.run_id == run_id)
    rows = q.order_by(VerificationRequest.requested_at.desc()).limit(100).all()
    return {
        "requests": [
            {
                "id": r.id,
                "run_id": r.run_id,
                "site": r.site,
                "google_account_id": r.google_account_id,
                "code_type": r.code_type,
                "requested_at": r.requested_at.isoformat(),
                "entered_at": r.entered_at.isoformat() if r.entered_at else None,
                "result": r.result,
                "asked_count": r.asked_count,
            }
            for r in rows
        ]
    }
