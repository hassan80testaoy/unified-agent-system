"""Owner-controlled Google Account models (§2.3).

SECURITY INVARIANT:
- `verification_requests` NEVER has a column for the verification code value itself.
- Passwords & OAuth refresh tokens are stored Fernet-encrypted and wiped immediately on disconnect/revoke.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, new_uuid, utcnow


class GoogleAccount(Base, TimestampMixin):
    __tablename__ = "google_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    email_masked: Mapped[str] = mapped_column(String(255), nullable=False)
    email_enc: Mapped[str] = mapped_column(Text, nullable=False)  # encrypted full email for runtime sign-in
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_scopes: Mapped[str] = mapped_column(String(255), default="gmail.readonly", nullable=False)
    imap_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    oauth_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_plus: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)  # active|disabled|blocked|disconnected
    consecutive_blocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GoogleSession(Base, TimestampMixin):
    __tablename__ = "google_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    google_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("google_accounts.id"), nullable=False, index=True)
    browser_profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    proxy_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)  # active|revoked|blocked
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerificationRequest(Base):
    """Metadata-only log of 2FA / verification code asks. Never stores the code value."""
    __tablename__ = "verification_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(255), nullable=False)
    google_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("google_accounts.id"), nullable=True)
    code_type: Mapped[str] = mapped_column(String(32), nullable=False)  # google_2fa|site_email|site_sms|authenticator
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|ok|wrong|expired|timeout|skipped
    asked_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
