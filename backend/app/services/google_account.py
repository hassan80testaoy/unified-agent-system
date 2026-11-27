"""Owner-controlled Google Account module & Verification-Code Ask Coordinator (§2.3).

Non-negotiable invariants:
- Google account credentials enter ONLY via secure Vault UI (never chat, logs, reports).
- Passwords & OAuth refresh tokens are stored Fernet-encrypted; UI shows only masked email.
- Verification codes asked from the user:
  - Agent pauses run on `awaiting_verification_code`
  - Asks user via Dashboard + Telegram
  - Accepts code once, submits to waiting browser/task Future, IMMEDIATELY wipes from memory
  - NEVER stores the verification code value in any DB column, event, log, or report
  - Max 2 asks on wrong/expired, then marks site `needs_manual` and moves on
  - Timeout marks `timeout` and advances run to next site
- Disconnect / Revoke Session wipes password_enc, oauth_refresh_token_enc, and persistent Chromium profile.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.agents import Approval, Run
from app.models.core import AuditLog
from app.models.google_account import GoogleAccount, GoogleSession, VerificationRequest
from app.security.auth import record_audit
from app.security.crypto import decrypt_secret, encrypt_secret, mask_email
from app.security.sanitizer import discard_ephemeral_secret, register_ephemeral_secret
from app.services.events import emit_and_broadcast_run_event
from app.services.mailbox import detect_plus_support, test_imap_connection
from app.services.notifications import create_notification, send_telegram_alert


class VerificationCodeCoordinator:
    """Coordinates one-time, non-persisted verification code exchange between user and active run."""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[str]] = {}

    def create_waiter(self, run_id: str) -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._futures[run_id] = fut
        return fut

    def deliver_code(self, run_id: str, code: str) -> bool:
        """Deliver one-time code to active waiter and register briefly in sanitizer so it is never logged."""
        cleaned = (code or "").strip()
        if not cleaned or len(cleaned) < 4 or len(cleaned) > 12:
            raise ValueError("Verification code must be 4 to 12 characters.")
        register_ephemeral_secret(cleaned)
        fut = self._futures.get(run_id)
        if fut is not None and not fut.done():
            fut.set_result(cleaned)
            return True
        discard_ephemeral_secret(cleaned)
        return False

    def clear_waiter(self, run_id: str) -> None:
        self._futures.pop(run_id, None)


verification_coordinator = VerificationCodeCoordinator()


def _profile_dir_for(account_id: str) -> Path:
    return get_settings().browser_profiles_dir / f"google_{account_id}"


def create_google_account(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    label: str,
    email_addr: str,
    password: str | None = None,
    oauth_refresh_token: str | None = None,
    allow_gmail_send: bool = False,
    ip: str | None = None,
) -> GoogleAccount:
    clean_email = email_addr.strip().lower()
    masked = mask_email(clean_email)
    scopes = "gmail.readonly,gmail.send" if allow_gmail_send else "gmail.readonly"

    acct = GoogleAccount(
        workspace_id=workspace_id,
        user_id=user_id,
        label=label.strip(),
        email_masked=masked,
        email_enc=encrypt_secret(clean_email),
        password_enc=encrypt_secret(password) if password else None,
        oauth_refresh_token_enc=encrypt_secret(oauth_refresh_token) if oauth_refresh_token else None,
        oauth_scopes=scopes,
        imap_ok=False,
        oauth_ok=bool(oauth_refresh_token),
        supports_plus=detect_plus_support(clean_email),
        status="active",
    )
    db.add(acct)
    db.flush()

    profile_dir = _profile_dir_for(acct.id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    session_row = GoogleSession(
        workspace_id=workspace_id,
        google_account_id=acct.id,
        browser_profile_id=f"google_{acct.id}",
        status="active",
    )
    db.add(session_row)

    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="google_account.add",
        target=masked,
        ip=ip,
        details={"label": label, "scopes": scopes},
    )
    db.commit()
    db.refresh(acct)
    return acct


def test_google_account(
    db: Session,
    *,
    account: GoogleAccount,
    user_id: str,
    app_password_override: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    email_plain = decrypt_secret(account.email_enc)
    pw_plain = app_password_override or (decrypt_secret(account.password_enc) if account.password_enc else "")
    imap_ok = False
    imap_err = None
    if pw_plain:
        imap_ok, imap_err = test_imap_connection(email_plain, pw_plain, "imap.gmail.com", 993)
    del email_plain
    del pw_plain

    account.imap_ok = imap_ok
    account.oauth_ok = bool(account.oauth_refresh_token_enc)
    record_audit(
        db,
        workspace_id=account.workspace_id,
        user_id=user_id,
        action="google_account.test",
        target=account.email_masked,
        ip=ip,
        details={"imap_ok": imap_ok, "oauth_ok": account.oauth_ok},
    )
    db.commit()
    return {
        "imap_ok": imap_ok,
        "oauth_ok": account.oauth_ok,
        "imap_error": imap_err,
        "supports_plus": account.supports_plus,
        "status": account.status,
    }


def disconnect_google_account(
    db: Session,
    *,
    account: GoogleAccount,
    user_id: str,
    ip: str | None = None,
) -> None:
    """Immediately wipe password, OAuth refresh token, and browser profile directory."""
    account.password_enc = None
    account.oauth_refresh_token_enc = None
    account.imap_ok = False
    account.oauth_ok = False
    account.status = "disconnected"

    revoke_google_session(db, account=account, user_id=user_id, ip=ip, audit_action="google_account.disconnect")


def revoke_google_session(
    db: Session,
    *,
    account: GoogleAccount,
    user_id: str,
    ip: str | None = None,
    audit_action: str = "google_account.revoke_session",
) -> None:
    now = datetime.now(timezone.utc)
    sessions = (
        db.query(GoogleSession)
        .filter(GoogleSession.google_account_id == account.id, GoogleSession.status == "active")
        .all()
    )
    for s in sessions:
        s.status = "revoked"
        s.revoked_at = now

    profile_dir = _profile_dir_for(account.id)
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)

    record_audit(
        db,
        workspace_id=account.workspace_id,
        user_id=user_id,
        action=audit_action,
        target=account.email_masked,
        ip=ip,
        details={"revoked_sessions": len(sessions)},
    )
    db.commit()


def record_google_block(db: Session, *, account: GoogleAccount, user_id: str, reason: str) -> None:
    """Stop using a Google account automatically after repeated security blocks (§2.3.5)."""
    account.consecutive_blocks += 1
    if account.consecutive_blocks >= 2:
        account.status = "blocked"
        create_notification(
            db,
            workspace_id=account.workspace_id,
            user_id=user_id,
            kind="google_blocked",
            title=f"تم إيقاف حساب Google مؤقتاً ({account.email_masked})",
            body="كرر جوجل تحديات الأمان على هذا الحساب. يُنصح باستخدام حساب جوجل مخصص للأتمتة أو التحويل للتسجيل بالإيميل والباسورد.",
            link="/settings",
        )
    record_audit(
        db,
        workspace_id=account.workspace_id,
        user_id=user_id,
        action="google_account.security_block",
        target=account.email_masked,
        details={"consecutive_blocks": account.consecutive_blocks, "reason": reason},
    )
    db.commit()


def get_google_account_activity(db: Session, workspace_id: str, account: GoogleAccount) -> list[dict[str, Any]]:
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.workspace_id == workspace_id, AuditLog.target == account.email_masked)
        .order_by(AuditLog.ts.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": l.id,
            "action": l.action,
            "target": l.target,
            "details": l.details,
            "ts": l.ts.isoformat(),
        }
        for l in logs
    ]


async def ask_user_for_verification_code(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    run: Run,
    site: str,
    google_account: GoogleAccount | None,
    code_type: str,  # google_2fa | site_email | site_sms | authenticator
    verifier_fn: Any = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Pause run, ask user for verification code (max 2 asks), enter once, wipe from memory immediately."""
    settings = get_settings()
    wait_sec = timeout_sec or settings.verification_code_timeout_sec
    masked = google_account.email_masked if google_account else "account"

    req = VerificationRequest(
        workspace_id=workspace_id,
        run_id=run.id,
        site=site,
        google_account_id=google_account.id if google_account else None,
        code_type=code_type,
        result="pending",
        asked_count=0,
    )
    db.add(req)
    db.flush()

    approval = Approval(
        workspace_id=workspace_id,
        run_id=run.id,
        kind="2fa",
        payload={
            "request_id": req.id,
            "site": site,
            "account_masked": masked,
            "code_type": code_type,
        },
        status="pending",
    )
    db.add(approval)
    run.status = "awaiting_verification_code"

    create_notification(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        kind="verification_code",
        title=f"مطلوب كود تحقق ({code_type}) — {site}",
        body=f"الحساب: {masked}. أدخل الكود في شاشة التشغيل ليُستخدم مرة واحدة ولا يُخزن.",
        link=f"/runs/{run.id}",
    )
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="verification_code.requested",
        target=masked,
        details={"run_id": run.id, "site": site, "code_type": code_type},
    )
    db.commit()

    max_asks = 2
    for attempt_no in range(1, max_asks + 1):
        req.asked_count = attempt_no
        db.commit()

        await emit_and_broadcast_run_event(
            db,
            workspace_id=workspace_id,
            run_id=run.id,
            level="needs_user",
            kind="awaiting_verification_code",
            message=(
                f"بانتظار كود التحقق ({code_type}) للموقع {site} للحساب {masked} "
                f"(محاولة {attempt_no} من {max_asks})."
            ),
            data={"request_id": req.id, "site": site, "account_masked": masked, "code_type": code_type, "attempt": attempt_no},
        )
        await send_telegram_alert(
            f"[Unified Agent] Verification code requested\nSite: {site}\nAccount: {masked}\nType: {code_type}\nRun: {run.id}"
        )

        fut = verification_coordinator.create_waiter(run.id)
        code_value: str | None = None
        try:
            code_value = await asyncio.wait_for(fut, timeout=wait_sec)
            req.entered_at = datetime.now(timezone.utc)
            is_valid = True
            if verifier_fn is not None:
                res = verifier_fn(code_value)
                is_valid = await res if asyncio.iscoroutine(res) else bool(res)

            if is_valid:
                req.result = "ok"
                approval.status = "approved"
                approval.decided_by = user_id
                approval.decided_at = datetime.now(timezone.utc)
                run.status = "running"
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    action="verification_code.entered",
                    target=masked,
                    details={"request_id": req.id, "result": "ok", "code_source": code_type},
                )
                db.commit()
                return {"status": "ok", "request_id": req.id, "asked_count": attempt_no}

            # Wrong or expired code — ask at most once more
            req.result = "wrong"
            record_audit(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                action="verification_code.entered",
                target=masked,
                details={"request_id": req.id, "result": "wrong", "attempt": attempt_no},
            )
            db.commit()
        except asyncio.TimeoutError:
            req.result = "timeout"
            approval.status = "expired"
            db.commit()
            return {"status": "timeout", "request_id": req.id, "asked_count": attempt_no}
        finally:
            # Wipe code value from memory and ephemeral sanitizer registry immediately
            if code_value is not None:
                discard_ephemeral_secret(code_value)
                code_value = None
            verification_coordinator.clear_waiter(run.id)

    # Exceeded 2 asks -> mark needs_manual
    req.result = "wrong"
    approval.status = "rejected"
    db.commit()
    return {"status": "needs_manual", "request_id": req.id, "asked_count": max_asks}
