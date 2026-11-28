"""Phase 3 Coding Agent models: CodingThread, ThreadFileSnapshot, ModelRace, HookRule."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid


class CodingThread(Base, TimestampMixin):
    """Coding Agent thread with plan, design options, live todo list, messages, worktree, and resume checkpoint."""

    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    parent_thread_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("threads.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="مهمة برمجة جديدة", nullable=False)
    goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    workspace_dir: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    mode_key: Mapped[str] = mapped_column(String(64), default="delegate", nullable=False)
    model_provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worktree_branch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    worktree_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    # idle | running | awaiting_plan_approval | blocked | interrupted | done | failed
    plan_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    plan_status: Mapped[str] = mapped_column(String(24), default="none", nullable=False)  # none|pending_review|approved|rejected
    design_options_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    selected_design_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    todo_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    messages_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    context_snapshot_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    system_prompt_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    subagent_role: Mapped[str | None] = mapped_column(String(48), nullable=True)


class ThreadFileSnapshot(Base, TimestampMixin):
    """Pre-edit snapshot of files on disk so 'Undo to here' rewinds files cleanly."""

    __tablename__ = "thread_file_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id"), nullable=False, index=True)
    message_seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rel_path: Mapped[str] = mapped_column(String(512), nullable=False)
    existed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class ModelRace(Base, TimestampMixin):
    """Multi-model race across parallel git worktrees scored by tests."""

    __tablename__ = "model_races"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id"), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    test_command: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    candidates_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    winner_branch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    winner_provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    merged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class HookRule(Base, TimestampMixin):
    """User-defined pre_tool, post_tool, and on_stop hooks for the Coding Agent."""

    __tablename__ = "coding_hooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(24), nullable=False)  # pre_tool | post_tool | on_stop
    tool_pattern: Mapped[str] = mapped_column(String(128), default="*", nullable=False)
    command: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    block_on_fail: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
