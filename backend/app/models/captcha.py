"""Captcha attempts statistics model (manual human-in-the-loop tracking & plugin results)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, new_uuid, utcnow


class CaptchaAttempt(Base):
    __tablename__ = "captcha_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("runs.id"), nullable=True, index=True)
    site: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    challenge_type: Mapped[str] = mapped_column(String(48), nullable=False)  # recaptcha_v2|turnstile|hcaptcha|cloudflare|image_captcha|phone_verify
    method: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)  # manual | plugin:<id>
    proxy_type: Mapped[str] = mapped_column(String(24), default="direct", nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    result: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)  # pending|passed|skipped|timeout|rejected_by_site
    blocked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    solved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
