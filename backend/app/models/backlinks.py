"""Backlink Profiles tables — keeping exact bl_* table & column names from SEO spec §4.2 / §8."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, new_uuid


class BlSite(Base, TimestampMixin):
    __tablename__ = "bl_sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signup_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    noindex_checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_noindex: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    # queued | in_progress | live | pending_review | partial | excluded_noindex | dropped_by_user | blocked | needs_captcha_manual | needs_manual | failed
    blocked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_attempt_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BlTarget(Base, TimestampMixin):
    __tablename__ = "bl_targets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # homepage | maps
    url: Mapped[str] = mapped_column(String(512), nullable=False)


class BlIdentity(Base, TimestampMixin):
    __tablename__ = "bl_identity"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extended_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)


class BlBio(Base, TimestampMixin):
    __tablename__ = "bl_bios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    angle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BlAnchor(Base, TimestampMixin):
    __tablename__ = "bl_anchors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    target_kind: Mapped[str] = mapped_column(String(24), nullable=False)  # homepage | maps
    text: Mapped[str] = mapped_column(String(255), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class BlAccount(Base, TimestampMixin):
    __tablename__ = "bl_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(String(36), ForeignKey("bl_sites.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("runs.id"), nullable=True)
    mailbox_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("mailboxes.id"), nullable=True)
    google_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("google_accounts.id"), nullable=True)
    email_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted; UI reveal/copy only, NEVER exported
    profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verified_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class BlLink(Base, TimestampMixin):
    __tablename__ = "bl_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("bl_accounts.id"), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(36), ForeignKey("bl_targets.id"), nullable=False)
    anchor_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bl_anchors.id"), nullable=True)
    link_type: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)  # dofollow | nofollow | unknown
    live: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_checked_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class BlStep(Base):
    __tablename__ = "bl_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("bl_accounts.id"), nullable=False, index=True)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # goto | click | type | select | upload | submit | wait
    selector_css: Mapped[str | None] = mapped_column(String(512), nullable=True)
    el_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    el_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    el_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_key: Mapped[str | None] = mapped_column(String(128), nullable=True)  # NEVER raw secret
    ok: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ts: Mapped[str] = mapped_column(String(64), nullable=False)
