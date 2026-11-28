"""Instinct personal agent tables: memory, observations, vault_items (domain-pinned), wakes, proactive_reports, i2i_bundles."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid


class MemoryFact(Base, TimestampMixin):
    __tablename__ = "memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(48), default="preference", nullable=False)
    content_enc: Mapped[str] = mapped_column(Text, nullable=False)
    allow_training: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Observation(Base, TimestampMixin):
    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # gmail|calendar|docs|location
    summary_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # Observations are NEVER used for training (enforced in policy & schema)
    never_train: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class VaultItem(Base, TimestampMixin):
    __tablename__ = "vault_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # login|google_account|card|totp|api_secret
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    allowed_sites: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    username_masked: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class VaultRequest(Base, TimestampMixin):
    __tablename__ = "vault_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    allowed_sites: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|completed|expired
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WakeSubscription(Base, TimestampMixin):
    __tablename__ = "wake_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # gmail|calendar|imessage|location
    trigger_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    safety_checkin_hours: Mapped[int] = mapped_column(default=24, nullable=False)
    filter_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), default="chat_notify", nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProactiveReport(Base, TimestampMixin):
    __tablename__ = "proactive_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    loop_name: Mapped[str] = mapped_column(String(48), nullable=False)
    earned_score: Mapped[int] = mapped_column(default=0, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, nullable=False)


class I2IBundle(Base, TimestampMixin):
    __tablename__ = "i2i_bundles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    peer_label: Mapped[str] = mapped_column(String(128), nullable=False)
    permissions_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|active|revoked
