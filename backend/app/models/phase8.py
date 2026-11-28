"""Phase 8 SQLAlchemy models: Multi-Site Backlink Library, Unified Messaging Gateway, Agent Delegation, Platform Projects, Wide Research, Plugin Catalog, Self-Development, and Eval Harness."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid, utcnow


class TargetSiteProject(Base, TimestampMixin):
    """Owner target websites/projects organized independently from backlink library sites (§8B)."""

    __tablename__ = "target_site_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    maps_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_identity_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)  # active|paused|archived


class BacklinkLibrarySite(Base, TimestampMixin):
    """Reusable backlink site library + discovered signup flow & SITE.md playbook (§8B, §8L.1)."""

    __tablename__ = "backlink_library_sites"
    __table_args__ = (
        UniqueConstraint("workspace_id", "domain", name="uq_bl_library_ws_domain"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signup_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="profile", nullable=False)
    signup_flow: Mapped[str] = mapped_column(String(32), default="email", nullable=False)  # email|google_only|code_first|form
    discovered_fields_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    playbook_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    playbook_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    past_results_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", nullable=False)  # ready|needs_review|blocked


class BacklinkPlacement(Base, TimestampMixin):
    """Any target site × any library site × unique email mapping & result (§8B)."""

    __tablename__ = "backlink_placements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    target_project_id: Mapped[str] = mapped_column(String(36), ForeignKey("target_site_projects.id"), nullable=False, index=True)
    library_site_id: Mapped[str] = mapped_column(String(36), ForeignKey("backlink_library_sites.id"), nullable=False, index=True)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    email_used: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    link_type: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)  # dofollow|nofollow|unknown
    live: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="planned", nullable=False)  # planned|live|blocked|needs_manual|mock
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class MessagingGatewayConfig(Base, TimestampMixin):
    """Unified Messaging Gateway channels (whatsapp|telegram|email|voice) configured cleanly without YAML edits (§8C, §8L.5)."""

    __tablename__ = "messaging_gateway_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", name="uq_msg_gateway_ws_channel"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # whatsapp|telegram|email|voice
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    continuity_mode: Mapped[str] = mapped_column(String(32), default="shared_owner_thread", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="disconnected", nullable=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessagingConversationMap(Base, TimestampMixin):
    """Maps external platform peer IDs (WhatsApp/Telegram/Email) onto unified ChatThread (§8C)."""

    __tablename__ = "messaging_conversation_maps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "channel", "external_peer_id", name="uq_msg_conv_map_peer"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    external_peer_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    external_peer_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessagingEventLog(Base, TimestampMixin):
    """Inbound/outbound gateway audit & voice transcription record (no proactive unsolicited outbound)."""

    __tablename__ = "messaging_event_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # inbound|outbound
    external_peer_id: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    content_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    media_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="delivered", nullable=False)


class AgentDelegationTask(Base, TimestampMixin):
    """Inter-agent delegation ledger enforcing depth limits & i2i trust bundles (§8F, §8L.4)."""

    __tablename__ = "agent_delegation_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    parent_agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    child_agent_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trust_bundle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)  # running|done|canceled|failed
    result_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    permissions_snapshot_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformProject(Base, TimestampMixin):
    """Manus-style persistent project workspace with shared instructions & knowledge base (§8I.7)."""

    __tablename__ = "platform_projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_platform_project_ws_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    instructions_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    knowledge_files_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    sandbox_dir: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    autonomy_mode: Mapped[str] = mapped_column(String(24), default="checkpoint", nullable=False)  # autonomous|checkpoint|supervised
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class WideResearchJob(Base, TimestampMixin):
    """Parallel isolated sub-agent fan-out research job up to 20 items (§8I.2)."""

    __tablename__ = "wide_research_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    items_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    results_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="done", nullable=False)
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PluginCatalogInstall(Base, TimestampMixin):
    """Commit-pinned plugin installations inspired by Hermes plugin catalog (§8H.8)."""

    __tablename__ = "plugin_catalog_installs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "plugin_slug", name="uq_plugin_catalog_ws_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    plugin_slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    repo_url: Mapped[str] = mapped_column(String(512), nullable=False)
    pinned_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(48), default="general", nullable=False)
    installed_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class SelfDevProposal(Base, TimestampMixin):
    """Worktree-isolated self-development proposal with diff, tests & owner approval gate (§8E)."""

    __tablename__ = "self_dev_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    plan_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    branch_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    worktree_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    diff_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    test_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tests_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="planned", nullable=False)  # planned|awaiting_approval|merged|rejected|blocked


class EvalRun(Base, TimestampMixin):
    """Harness regression & capability evaluation score history (§8L.6)."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    suite_name: Mapped[str] = mapped_column(String(80), default="platform_core", nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    passed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_vs_prev: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    results_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
