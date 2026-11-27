"""Mailbox module — IMAP read-only verification, Fernet encryption, plus-aliasing, and verification email extraction."""

from __future__ import annotations

import email
import imaplib
import re
import time
from datetime import datetime, timezone
from email.header import decode_header
from typing import Any

from sqlalchemy.orm import Session

from app.models.mailboxes import Mailbox
from app.security.auth import record_audit
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.sanitizer import is_same_domain_or_subdomain

PROVIDERS: dict[str, tuple[str, int, str]] = {
    "gmail.com": ("imap.gmail.com", 993, "gmail"),
    "googlemail.com": ("imap.gmail.com", 993, "gmail"),
    "outlook.com": ("outlook.office365.com", 993, "outlook"),
    "hotmail.com": ("outlook.office365.com", 993, "outlook"),
    "live.com": ("outlook.office365.com", 993, "outlook"),
    "yahoo.com": ("imap.mail.yahoo.com", 993, "yahoo"),
}

SUBJECT_RE = re.compile(r"(verify|activate|confirm|code|تفعيل|تأكيد|تحقق)", re.I)
CODE_RE = re.compile(r"\b(\d{6,8})\b")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
MAX_AGE_SEC = 15 * 60


def guess_server(email_addr: str, host: str | None = None, port: int = 993) -> tuple[str | None, int, str]:
    domain = email_addr.split("@")[-1].lower().strip()
    if host:
        provider_name = PROVIDERS.get(domain, ("", 993, "custom"))[2]
        return host, port, provider_name
    matched = PROVIDERS.get(domain)
    if matched:
        return matched
    return None, port, "custom"


def detect_plus_support(email_addr: str) -> bool:
    domain = email_addr.split("@")[-1].lower().strip()
    return domain in {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com"}


def alias_for(email_addr: str, site_slug: str, supports_plus: bool) -> str:
    if not supports_plus or "@" not in email_addr:
        return email_addr
    clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "", site_slug)[:24] or "site"
    local, domain = email_addr.split("@", 1)
    return f"{local}+{clean_slug}@{domain}"


def test_imap_connection(email_addr: str, app_password: str, host: str, port: int = 993) -> tuple[bool, str | None]:
    """Test IMAP by opening INBOX strictly read-only. Never logs passwords."""
    if not host:
        return False, "IMAP host is required"
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=15)
        try:
            client.login(email_addr, app_password)
            typ, _ = client.select("INBOX", readonly=True)
            if typ != "OK":
                return False, "Cannot open INBOX read-only"
            return True, None
        finally:
            try:
                client.logout()
            except Exception:
                pass
    except imaplib.IMAP4.error:
        return False, "Login rejected (verify App Password / 2-Step Verification / IMAP enabled)"
    except Exception as exc:
        return False, f"Connection error: {type(exc).__name__}"


def _extract_body_text(msg: email.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in {"text/plain", "text/html"}:
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode(msg.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(parts)


def find_verification_from_messages(
    raw_messages: list[bytes],
    *,
    site_domain: str,
    target_address: str,
    seen_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Inspect candidate emails for CURRENT task only.

    Strictly checks:
    - To header matches target_address (or its base address)
    - Sender domain matches site_domain (or subdomain)
    - Subject matches verify/activate/confirm/code
    - Received within MAX_AGE_SEC (15 min)
    - Extracted link stays on site_domain / subdomain (no external trackers)
    Returns metadata + transient link/code in memory ONLY — caller MUST NOT persist or log them.
    """
    seen = seen_ids or set()
    now = time.time()
    norm_target = target_address.lower().strip()
    norm_domain = site_domain.lower().strip().lstrip("www.")

    for raw in raw_messages:
        msg = email.message_from_bytes(raw)
        mid = (msg.get("Message-ID") or "").strip()
        if mid and mid in seen:
            continue

        raw_subj = decode_header(msg.get("Subject", ""))[0][0]
        subject = raw_subj.decode("utf-8", errors="replace") if isinstance(raw_subj, bytes) else str(raw_subj)
        sender_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        to_addr = email.utils.parseaddr(msg.get("To", ""))[1].lower()

        if norm_target and to_addr and norm_target != to_addr:
            # Allow matching base address if plus-alias is used
            base_target = re.sub(r"\+[^@]+@", "@", norm_target)
            base_to = re.sub(r"\+[^@]+@", "@", to_addr)
            if base_target != base_to:
                continue

        sender_domain = sender_addr.split("@")[-1] if "@" in sender_addr else ""
        if not (sender_domain == norm_domain or sender_domain.endswith("." + norm_domain)):
            continue

        if not SUBJECT_RE.search(subject):
            continue

        try:
            msg_dt = email.utils.parsedate_to_datetime(msg.get("Date", ""))
            if msg_dt.tzinfo is None:
                msg_dt = msg_dt.replace(tzinfo=timezone.utc)
            if (now - msg_dt.timestamp()) > MAX_AGE_SEC:
                continue
        except Exception:
            pass

        body = _extract_body_text(msg)
        activation_url = None
        for url in URL_RE.findall(body):
            clean_url = url.rstrip(".,);]")
            if is_same_domain_or_subdomain(clean_url, norm_domain):
                activation_url = clean_url
                break

        code_match = CODE_RE.search(body)
        email_code = code_match.group(1) if code_match else None

        if activation_url or email_code:
            return {
                "message_id": mid,
                "activation_url": activation_url,
                "email_code": email_code,
            }
    return None


def fetch_and_find_verification_imap(
    db: Session,
    *,
    mailbox_id: str,
    site_domain: str,
    target_address: str,
    seen_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    mb = db.get(Mailbox, mailbox_id)
    if not mb or not mb.password_enc or mb.status == "disconnected":
        return None
    password = decrypt_secret(mb.password_enc)
    client = imaplib.IMAP4_SSL(mb.imap_host, mb.imap_port, timeout=15)
    try:
        client.login(mb.email, password)
        client.select("INBOX", readonly=True)
        _, data = client.search(None, "UNSEEN")
        msg_nums = (data[0] or b"").split()[-15:]
        raw_list: list[bytes] = []
        for num in reversed(msg_nums):
            typ, msg_data = client.fetch(num, "(RFC822)")
            if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                raw_list.append(msg_data[0][1])
        return find_verification_from_messages(
            raw_list,
            site_domain=site_domain,
            target_address=target_address,
            seen_ids=seen_ids,
        )
    finally:
        del password
        try:
            client.logout()
        except Exception:
            pass


def add_or_update_mailbox(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    email_addr: str,
    app_password: str,
    imap_host: str | None = None,
    imap_port: int = 993,
    run_connection_test: bool = True,
    ip: str | None = None,
) -> Mailbox:
    host, port, provider_name = guess_server(email_addr, imap_host, imap_port)
    if not host:
        raise ValueError("Unable to detect IMAP host for this domain; please enter imap_host manually.")

    ok, err = (True, None)
    if run_connection_test:
        ok, err = test_imap_connection(email_addr, app_password, host, port)

    mb = Mailbox(
        workspace_id=workspace_id,
        owner_user_id=user_id,
        email=email_addr.strip().lower(),
        provider=provider_name,
        imap_host=host,
        imap_port=port,
        password_enc=encrypt_secret(app_password),
        supports_plus=detect_plus_support(email_addr),
        status="ok" if ok else "failed",
        last_tested_at=datetime.now(timezone.utc) if run_connection_test else None,
        last_error=err,
    )
    db.add(mb)
    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="mailbox.connect",
        target=mb.email,
        ip=ip,
        details={"provider": provider_name, "status": mb.status},
    )
    db.commit()
    db.refresh(mb)
    return mb


def disconnect_mailbox(
    db: Session,
    *,
    mailbox: Mailbox,
    user_id: str,
    ip: str | None = None,
) -> None:
    """Immediately wipe encrypted App Password and mark mailbox disconnected."""
    mailbox.password_enc = None
    mailbox.status = "disconnected"
    mailbox.last_error = None
    record_audit(
        db,
        workspace_id=mailbox.workspace_id,
        user_id=user_id,
        action="mailbox.disconnect",
        target=mailbox.email,
        ip=ip,
    )
    db.commit()
