"""Tests for owner setup, TOTP 2FA verification, login gating, and audit logging."""

from __future__ import annotations

import pyotp
from sqlalchemy.orm import Session

from app.models.core import AuditLog


def test_owner_setup_2fa_login_and_audit(client, temp_db_session: Session):
    status_before = client.get("/api/auth/status")
    assert status_before.status_code == 200
    assert status_before.json()["initialized"] is False

    setup_res = client.post(
        "/api/auth/setup",
        json={
            "email": "moaaz@example.com",
            "password": "MySuperStrongPassword!9",
            "workspace_name": "وكالة سيو",
            "enable_2fa": True,
        },
    )
    assert setup_res.status_code == 200
    body = setup_res.json()
    token = body["access_token"]
    secret = body["totp_setup"]["secret"]
    assert len(secret) >= 16

    # Duplicate setup must be rejected
    dup = client.post(
        "/api/auth/setup",
        json={"email": "other@example.com", "password": "Password1234!"},
    )
    assert dup.status_code == 409

    # Verify & activate 2FA using real pyotp code
    totp = pyotp.TOTP(secret)
    verify_res = client.post(
        "/api/auth/2fa/verify",
        json={"totp_code": totp.now()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["totp_enabled"] is True

    # Login WITHOUT 2FA code must fail once totp_enabled is True
    bad_login = client.post(
        "/api/auth/login",
        json={"email": "moaaz@example.com", "password": "MySuperStrongPassword!9"},
    )
    assert bad_login.status_code == 401

    # Login WITH valid TOTP code succeeds
    good_login = client.post(
        "/api/auth/login",
        json={"email": "moaaz@example.com", "password": "MySuperStrongPassword!9", "totp_code": totp.now()},
    )
    assert good_login.status_code == 200
    assert "access_token" in good_login.json()

    me_res = client.get("/api/me", headers={"Authorization": f"Bearer {good_login.json()['access_token']}"})
    assert me_res.status_code == 200
    assert me_res.json()["user"]["email"] == "moaaz@example.com"

    # Confirm audit log recorded setup, 2fa_enabled, and login
    actions = {a.action for a in temp_db_session.query(AuditLog).all()}
    assert {"auth.setup_owner", "auth.2fa_enabled", "auth.login"}.issubset(actions)
