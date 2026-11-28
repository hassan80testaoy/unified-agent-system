"""Blind-Fill Vault service — one-time secure links, domain-pinned secrets, server-side password generation."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.instinct import VaultItem, VaultRequest
from app.security.auth import record_audit
from app.security.crypto import decrypt_secret, encrypt_secret, generate_site_password, mask_email
from app.security.sanitizer import register_vault_secret


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_vault_request(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    kind: str,
    label: str,
    allowed_sites: list[str],
    reason: str = "",
    ttl_minutes: int = 15,
) -> dict[str, Any]:
    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    req = VaultRequest(
        workspace_id=workspace_id,
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        kind=kind,
        label=label,
        allowed_sites=allowed_sites,
        reason=reason,
        status="pending",
        expires_at=expires_at,
    )
    db.add(req)
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="vault.request_created",
        target=label,
        details={"kind": kind, "allowed_sites": allowed_sites},
    )
    db.commit()
    return {
        "request_id": req.id,
        "token": raw_token,
        "vault_path": f"/vault/{raw_token}",
        "expires_at": expires_at.isoformat(),
    }


def get_vault_request_status(db: Session, raw_token: str) -> VaultRequest | None:
    req = db.query(VaultRequest).filter(VaultRequest.token_hash == _hash_token(raw_token)).first()
    if req is None:
        return None
    exp = req.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if req.status == "pending" and exp < datetime.now(timezone.utc):
        req.status = "expired"
        db.commit()
    return req


def submit_vault_request(
    db: Session,
    *,
    raw_token: str,
    username: str | None,
    secret_value: str,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> VaultItem:
    req = get_vault_request_status(db, raw_token)
    if req is None or req.status != "pending":
        raise ValueError("Invalid, used, or expired one-time vault link.")

    masked = mask_email(username) if (username and "@" in username) else (username[:2] + "***" if username else None)
    item = VaultItem(
        workspace_id=req.workspace_id,
        user_id=req.user_id,
        kind=req.kind,
        label=req.label,
        allowed_sites=req.allowed_sites,
        username_masked=masked,
        secret_enc=encrypt_secret(secret_value),
        meta_json=meta or {},
    )
    db.add(item)
    req.status = "completed"
    record_audit(
        db,
        workspace_id=req.workspace_id,
        user_id=req.user_id,
        action="vault.item_saved",
        target=req.label,
        ip=ip,
        details={"kind": req.kind, "allowed_sites": req.allowed_sites},
    )
    db.commit()
    db.refresh(item)
    register_vault_secret(secret_value)
    return item


def sync_vault_secrets_from_db(db: Session) -> int:
    """Load Vault secret fingerprints into the sanitizer scrubber at startup so secrets never leak to LLM payloads (§8L.2)."""
    loaded = 0
    for item in db.query(VaultItem).all():
        try:
            register_vault_secret(decrypt_secret(item.secret_enc))
            loaded += 1
        except Exception:
            continue
    return loaded


def get_domain_pinned_secret(
    db: Session,
    *,
    workspace_id: str,
    item_id: str,
    target_url: str,
) -> str:
    """Blind-fill access check: item is ONLY usable on domains in its `allowed_sites` allowlist."""
    item = db.get(VaultItem, item_id)
    if item is None or item.workspace_id != workspace_id:
        raise PermissionError("Vault item not found in workspace.")
    host = (urlparse(target_url).hostname or target_url).lower().lstrip("www.")
    allowed = [s.lower().lstrip("www.") for s in (item.allowed_sites or [])]
    if allowed and "*" not in allowed:
        if not any(host == dom or host.endswith("." + dom) for dom in allowed):
            raise PermissionError(f"Vault item '{item.label}' is not pinned for domain '{host}'.")
    secret = decrypt_secret(item.secret_enc)
    register_vault_secret(secret)
    return secret


def create_server_generated_site_password() -> tuple[str, str]:
    """Generate a strong unique per-site password server-side and return (plaintext, fernet_ciphertext)."""
    pw = generate_site_password(20)
    return pw, encrypt_secret(pw)
