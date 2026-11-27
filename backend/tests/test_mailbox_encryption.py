"""Tests for mailbox Fernet encryption, plus-alias generation, read-only verification email matching, and disconnect wipe."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate

from sqlalchemy.orm import Session

from app.models.mailboxes import Mailbox
from app.security.crypto import decrypt_secret, encrypt_secret
from app.services.mailbox import alias_for, detect_plus_support, find_verification_from_messages, guess_server


def test_fernet_roundtrip_and_provider_detection():
    secret = "abcd-efgh-ijkl-mnop"
    enc = encrypt_secret(secret)
    assert secret not in enc
    assert decrypt_secret(enc) == secret

    assert guess_server("user@gmail.com") == ("imap.gmail.com", 993, "gmail")
    assert guess_server("user@outlook.com") == ("outlook.office365.com", 993, "outlook")
    assert guess_server("user@yahoo.com") == ("imap.mail.yahoo.com", 993, "yahoo")
    assert detect_plus_support("user@gmail.com") is True
    assert alias_for("user@gmail.com", "hashnode", True) == "user+hashnode@gmail.com"
    assert alias_for("user@yahoo.com", "hashnode", False) == "user@yahoo.com"


def test_verification_email_domain_guard_and_disconnect_wipe(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Add mailbox (skip live network IMAP call during offline unit test)
    add_res = client.post(
        "/api/mailboxes",
        headers=headers,
        json={
            "email": "seo.work@gmail.com",
            "app_password": "secret-app-password-1234",
            "test_on_connect": False,
        },
    )
    assert add_res.status_code == 200
    mb_id = add_res.json()["id"]

    # Password is encrypted in DB and NEVER exposed in GET /api/mailboxes
    mb_row = temp_db_session.get(Mailbox, mb_id)
    assert mb_row is not None
    assert mb_row.password_enc is not None
    assert "secret-app-password-1234" not in mb_row.password_enc
    list_res = client.get("/api/mailboxes", headers=headers)
    assert "secret-app-password-1234" not in list_res.text
    assert "password_enc" not in list_res.text

    # Build a malicious email with external phishing URL + valid email with same-domain link
    phish = EmailMessage()
    phish["Subject"] = "Please verify your account"
    phish["From"] = "noreply@hashnode.com"
    phish["To"] = "seo.work+hashnode@gmail.com"
    phish["Date"] = formatdate(usegmt=True)
    phish["Message-ID"] = "<phish-1@hashnode.com>"
    phish.set_content("Click here: https://evil-tracker.example.net/steal?token=123")

    valid = EmailMessage()
    valid["Subject"] = "Confirm your Hashnode registration"
    valid["From"] = "auth@mail.hashnode.com"
    valid["To"] = "seo.work+hashnode@gmail.com"
    valid["Date"] = formatdate(usegmt=True)
    valid["Message-ID"] = "<valid-1@hashnode.com>"
    valid.set_content("Activate: https://hashnode.com/verify?token=abc999")

    match = find_verification_from_messages(
        [phish.as_bytes(), valid.as_bytes()],
        site_domain="hashnode.com",
        target_address="seo.work+hashnode@gmail.com",
    )
    assert match is not None
    assert match["message_id"] == "<valid-1@hashnode.com>"
    assert match["activation_url"] == "https://hashnode.com/verify?token=abc999"

    # Disconnect must wipe password_enc immediately
    del_res = client.delete(f"/api/mailboxes/{mb_id}", headers=headers)
    assert del_res.status_code == 200
    temp_db_session.refresh(mb_row)
    assert mb_row.password_enc is None
    assert mb_row.status == "disconnected"
