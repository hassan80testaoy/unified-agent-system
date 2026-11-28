"""V3 Harness models: Modes presets, Project Mode defaults, Backlink Session Replay, and Chat Threads/Messages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid


class ModePreset(Base, TimestampMixin):
    """Built-in and custom agent modes (delegate, standard, minimal, build, plan, red-team, custom)."""

    __tablename__ = "modes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name_ar: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str] = mapped_column(String(128), nullable=False)
    description_ar: Mapped[str] = mapped_column(Text, default="", nullable=False)
    description_en: Mapped[str] = mapped_column(Text, default="", nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tool_allowlist: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    sandbox_mode: Mapped[str] = mapped_column(String(32), default="workspace-write", nullable=False)
    approval_policy: Mapped[str] = mapped_column(String(24), default="ask_first", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProjectMode(Base, TimestampMixin):
    """Per-project mode, default model, and delegation lane overrides (Zadloop-style)."""

    __tablename__ = "project_modes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mode_key: Mapped[str] = mapped_column(String(64), default="delegate", nullable=False)
    model_provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delegation_lanes_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class BlSession(Base, TimestampMixin):
    """Versioned browser Session Replay archive per site (strictly zero secrets stored)."""

    __tablename__ = "bl_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    site_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("bl_sites.id"), nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    steps_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    playbook_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    replay_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_steps_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saved_llm_calls_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_replay_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class ChatThread(Base, TimestampMixin):
    """Chat-first General Agent thread backed by append-only NDJSON session log."""

    __tablename__ = "chat_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), default="محادثة جديدة", nullable=False)
    mode_key: Mapped[str] = mapped_column(String(64), default="delegate", nullable=False)
    project_key: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(24), default="ask_first", nullable=False)
    ndjson_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    compacted_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="idle", nullable=False)


class ChatMessage(Base, TimestampMixin):
    """Messages & inline action cards (CAPTCHA, verification code, approvals) inside a ChatThread."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_threads.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    card_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    card_payload: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rewound: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
