"""Phase 7 ORM models: Site Auditor, Event Wakes Log, Proactive Proposals, Hermes Routing, Hypnos, and Unified Reviews."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid, utcnow


class SiteAudit(Base, TimestampMixin):
    """Real browser + HTTP technical SEO & backlink audit report with line/screenshot evidence."""

    __tablename__ = "site_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    target_backlink_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    network_path: Mapped[str] = mapped_column(String(32), default="direct", nullable=False)
    screenshot_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    findings_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    report_md: Mapped[str] = mapped_column(Text, default="", nullable=False)


class WakeEventLog(Base, TimestampMixin):
    """Idempotent audit ledger for real event-driven Wake firings."""

    __tablename__ = "wake_event_logs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "subscription_id", "external_event_id", name="uq_wake_event_dedup"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(String(36), ForeignKey("wake_subscriptions.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="fired", nullable=False)  # fired|deduplicated|failed
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    chat_thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chat_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProactiveProposal(Base, TimestampMixin):
    """Proactive Instinct value loop proposals that ALWAYS require explicit owner approval before action."""

    __tablename__ = "proactive_proposals"
    __table_args__ = (
        UniqueConstraint("workspace_id", "loop_name", "dedup_key", name="uq_proactive_proposal_dedup"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    loop_name: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title_ar: Mapped[str] = mapped_column(String(255), nullable=False)
    title_en: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    detail_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action_payload_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)  # pending|approved|rejected|executed
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result_json: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON, nullable=True)


class RoutingProfile(Base, TimestampMixin):
    """Per-context Hermes routing overrides (chat / coding / planning / research)."""

    __tablename__ = "routing_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "context_key", name="uq_routing_profile_ws_ctx"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    context_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # chat|coding|planning|research
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(255), default="auto", nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    api_key_env: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="mid", nullable=False)
    rules_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RoutingConfigBackup(Base, TimestampMixin):
    """Config snapshot saved before every wizard/routing profile edit so rollback is always possible."""

    __tablename__ = "routing_config_backups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(160), default="wizard", nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class HypnosConfig(Base, TimestampMixin):
    """Workspace Opt-In settings for Hypnos sleep-learning (STRICTLY OFF by default)."""

    __tablename__ = "hypnos_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_hypnos_config_ws"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    schedule_hour: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    active_lora_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_exam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens_before: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class HypnosFeedbackEvent(Base, TimestampMixin):
    """Daytime explicit feedback / command pass-fail ledger (excluded from training unless Hypnos opted-in)."""

    __tablename__ = "hypnos_feedback_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # approved|denied|corrected|cmd_passed|cmd_failed
    context_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    correction_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    command_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    excluded_for_unlearn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    never_train: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class HypnosLoraVersion(Base, TimestampMixin):
    """Versioned personal adapter candidates & held-out exam results produced only when Hypnos is enabled."""

    __tablename__ = "hypnos_lora_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    version_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    adapter_path: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    training_event_ids_json: Mapped[list[str]] = mapped_column(PortableJSON, default=list, nullable=False)
    habits_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="candidate", nullable=False)  # candidate|exam_passed|exam_failed|promoted|unlearned
    exam_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    baseline_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    exam_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exam_details_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    prompt_delta_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CodeReviewItem(Base, TimestampMixin):
    """Unified review entity for GitHub Pull Requests, GitLab Merge Requests, and local diffs."""

    __tablename__ = "code_review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(24), default="github", nullable=False, index=True)  # github|gitlab|local
    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    external_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(128), default="owner", nullable=False)
    source_branch: Mapped[str] = mapped_column(String(128), default="feat/update", nullable=False)
    target_branch: Mapped[str] = mapped_column(String(128), default="main", nullable=False)
    url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)  # open|approved|changes_requested|merged|closed
    diff_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    checks_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    comments_json: Mapped[list[dict[str, Any]]] = mapped_column(PortableJSON, default=list, nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
