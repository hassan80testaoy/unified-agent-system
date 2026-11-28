"""Phase 5 models: MCP Servers & Session Permissions, Installed Skills Store, Bots, Channels, Team Packages & Triggers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid


class McpServer(Base, TimestampMixin):
    """Registered local/remote MCP servers (added via command, link, or pasted JSON)."""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "namespace_prefix", name="uq_mcp_servers_ws_prefix"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), default="command", nullable=False)  # command|link|json
    command: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    env_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet encrypted JSON env vars
    env_keys_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    scope: Mapped[str] = mapped_column(String(24), default="all_sessions", nullable=False)  # all_sessions|project
    project_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    namespace_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    working_dir: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)  # active|unavailable|disabled
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    launch_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    oauth_config_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oauth_auto_refresh: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class McpSessionPermission(Base, TimestampMixin):
    """Per-session MCP tool permissions bound to tool definition + launch + account + scope."""

    __tablename__ = "mcp_session_permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    server_id: Mapped[str] = mapped_column(String(36), ForeignKey("mcp_servers.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    scope: Mapped[str] = mapped_column(String(64), default="all_sessions", nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    launch_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    grant_mode: Mapped[str] = mapped_column(String(24), default="remembered", nullable=False)  # remembered|allow_once
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstalledSkill(Base, TimestampMixin):
    """Installed skills from ~/.agent/skills, project folder, or one-click skills.sh store."""

    __tablename__ = "installed_skills"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_installed_skills_ws_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="skills_sh", nullable=False)  # local|project|skills_sh
    install_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    contract_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    has_script: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tools_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    references_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BotContact(Base, TimestampMixin):
    """Chat-app bot roster contact with own personality, model, computer mode, memory, and thread ID."""

    __tablename__ = "bot_contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    handle: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    avatar_emoji: Mapped[str] = mapped_column(String(16), default="🤖", nullable=False)
    role_title: Mapped[str] = mapped_column(String(128), default="Assistant", nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), default="auto:fast", nullable=False)
    computer_mode: Mapped[str] = mapped_column(String(32), default="workspace_sandbox", nullable=False)
    connected_apps_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    memory_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(36), default=new_uuid, nullable=False, index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    skills_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)


class TeamChannel(Base, TimestampMixin):
    """Work/Personal/Project contexts sharing bots without cloning them."""

    __tablename__ = "team_channels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_type: Mapped[str] = mapped_column(String(24), default="work", nullable=False)  # work|personal|project
    shared_instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    working_dir: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    lead_bot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    bot_ids_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    responder_rules_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    transcript_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)


class TeamPackageImport(Base, TimestampMixin):
    """Audit record of reviewed and installed portable Markdown team packages."""

    __tablename__ = "team_package_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class AutomationTrigger(Base, TimestampMixin):
    """Schedule (cron) or event-driven automation triggers with explicit run logs & notifications."""

    __tablename__ = "automation_triggers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(24), default="schedule", nullable=False)  # schedule|event
    cron_expr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_bot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_channel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fire_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TriggerRunLog(Base, TimestampMixin):
    """Execution history log for AutomationTrigger runs."""

    __tablename__ = "trigger_run_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    trigger_id: Mapped[str] = mapped_column(String(36), ForeignKey("automation_triggers.id"), nullable=False, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), default="schedule", nullable=False)
    action_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="success", nullable=False)  # success|failed
    output_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
