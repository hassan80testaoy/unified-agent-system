"""Agent registry, goals, tasks, runs, events, approvals, artifacts, reports, and goal memory models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid, utcnow


class AgentModel(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name_ar: Mapped[str] = mapped_column(String(160), nullable=False)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    description_ar: Mapped[str] = mapped_column(Text, nullable=False)
    description_en: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_schema: Mapped[dict[str, Any]] = mapped_column(PortableJSON, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(PortableJSON, nullable=False, default=dict)
    tools_allowed: Mapped[list[str]] = mapped_column(PortableJSON, nullable=False, default=list)
    daily_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    daily_budget_usd: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    needs_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    category: Mapped[str] = mapped_column(String(48), default="seo", nullable=False)
    priority: Mapped[str] = mapped_column(String(8), default="P0", nullable=False)


class AgentSetting(Base, TimestampMixin):
    __tablename__ = "agent_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("agents.id"), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("threads.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_start: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metric_target: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    autopilot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)  # draft|active|paused|done|stopped|blocked
    plan: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False)
    goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"), nullable=True)
    request_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="once", nullable=False)  # once|repeat
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="created", nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (Index("idx_runs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    # queued | awaiting_approval | awaiting_captcha_manual | awaiting_verification_code | running | paused | done | failed | canceled
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("idx_run_events_run_ts", "run_id", "ts"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)  # info|warn|error|needs_user
    kind: Mapped[str] = mapped_column(String(48), default="step", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)


class RunItem(Base, TimestampMixin):
    __tablename__ = "run_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(48), nullable=False)
    item_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    review_state: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|accepted|redo|dropped
    result: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # spend|mailbox|bulk|captcha_manual|2fa|outbound
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|approved|rejected|expired
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("runs.id"), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("threads.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # csv|pdf|json|screenshot|plan|design|recording|task_list
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"), nullable=True)
    period: Mapped[str] = mapped_column(String(24), default="daily", nullable=False)  # daily|weekly
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    channels: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)


class AgentMemory(Base, TimestampMixin):
    __tablename__ = "agent_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # worked|failed|note
    text: Mapped[str] = mapped_column(Text, nullable=False)
