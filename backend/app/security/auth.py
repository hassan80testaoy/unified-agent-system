"""Authentication (scrypt + JWT HttpOnly/Bearer + TOTP 2FA), RBAC, rate limiting, and audit logging."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import jwt
import pyotp
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models.core import AuditLog, User, Workspace, WorkspaceMember
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.sanitizer import scrub_value

SESSION_COOKIE_NAME = "uas_session"
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    n, r, p = 2**14, 8, 1
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_hex, dk_hex = stored_hash.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Unified Agent System")


def verify_totp_code(secret_enc: str | None, code: str) -> bool:
    if not secret_enc or not code:
        return False
    secret = decrypt_secret(secret_enc)
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(code.strip(), valid_window=1))


def _jwt_key() -> str:
    settings = get_settings()
    if settings.jwt_secret_key and len(settings.jwt_secret_key) >= 16:
        return settings.jwt_secret_key
    if settings.is_production:
        raise RuntimeError("JWT_SECRET_KEY (min 16 chars) is required in production.")
    key_file = settings.data_dir / ".dev_jwt.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    generated = os.urandom(32).hex()
    key_file.write_text(generated, encoding="utf-8")
    return generated


def create_access_token(user_id: str, workspace_id: str, role: str, expires_hours: int = 8) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "ws": workspace_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_hours)).timestamp()),
    }
    return jwt.encode(payload, _jwt_key(), algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _jwt_key(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_session",
                "message_ar": "الجلسة منتهية أو غير صالحة",
                "message_en": "Invalid or expired session",
            },
        ) from exc


def check_rate_limit(key: str, limit: int = 120, window_seconds: int = 60) -> None:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message_ar": "تم تجاوز حد الطلبات المسموح. انتظر قليلاً.",
                "message_en": "Too many requests. Please slow down.",
            },
        )
    bucket.append(now)


def record_audit(
    db: Session,
    *,
    workspace_id: str,
    user_id: str | None,
    action: str,
    target: str,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    entry = AuditLog(
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
        target=target,
        ip=ip,
        details=scrub_value(details) if details else None,
        ts=utcnow(),
    )
    db.add(entry)
    db.flush()


class AuthContext:
    def __init__(self, user: User, workspace: Workspace, role: str):
        self.user = user
        self.workspace = workspace
        self.role = role


def get_current_auth(
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> AuthContext:
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(f"api:{client_ip}", limit=240, window_seconds=60)

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    elif session_cookie:
        token = session_cookie

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "auth_required",
                "message_ar": "يجب تسجيل الدخول للمتابعة",
                "message_en": "Authentication required",
            },
        )

    payload = decode_access_token(token)
    user = db.get(User, payload.get("sub"))
    workspace = db.get(Workspace, payload.get("ws"))
    if not user or not workspace:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "user_not_found",
                "message_ar": "المستخدم أو مساحة العمل غير موجودة",
                "message_en": "User or workspace not found",
            },
        )

    member = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == user.id)
        .first()
    )
    role = member.role if member else user.role
    return AuthContext(user=user, workspace=workspace, role=role)


def require_roles(*allowed: str) -> Callable[[AuthContext], AuthContext]:
    def _dep(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if auth.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "forbidden",
                    "message_ar": "لا تملك صلاحية تنفيذ هذا الإجراء",
                    "message_en": "Insufficient permissions",
                },
            )
        return auth

    return _dep


def encrypt_totp_secret(secret: str) -> str:
    return encrypt_secret(secret)
