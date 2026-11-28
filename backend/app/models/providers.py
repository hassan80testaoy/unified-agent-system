"""Multi-provider registry, per-agent delegation assignments, model routes, LLM usage log, API keys, and connections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid, utcnow


class Provider(Base, TimestampMixin):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="llm", nullable=False)  # llm|llm-local|llm-mock|captcha
    connect_type: Mapped[str] = mapped_column(String(24), default="api_key", nullable=False)  # signin|api_key|custom_endpoint
    protocol: Mapped[str] = mapped_column(String(32), default="openai-completions", nullable=False)  # openai-completions|openai-responses|anthropic|gemini|ollama|custom
    path_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_base: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_ref: Mapped[str] = mapped_column(String(128), default="none", nullable=False)
    models_json: Mapped[dict[str, str]] = mapped_column(PortableJSON, default=dict, nullable=False)
    budget_usd_daily: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    budget_usd_monthly: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class AgentProviderAssignment(Base, TimestampMixin):
    """Per-agent delegation mapping: orchestrator + workers by task_type + different-provider reviewer."""
    __tablename__ = "agent_provider_assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agents.id"), nullable=False, index=True)
    orchestrator_json: Mapped[dict[str, str]] = mapped_column(PortableJSON, default=dict, nullable=False)
    workers_json: Mapped[dict[str, dict[str, str]]] = mapped_column(PortableJSON, default=dict, nullable=False)
    reviewer_json: Mapped[dict[str, str]] = mapped_column(PortableJSON, default=dict, nullable=False)


class ModelRoute(Base, TimestampMixin):
    __tablename__ = "model_routes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # cheap|mid|strong
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey("providers.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    fallback_route_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    max_cost_per_call: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("runs.id"), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    route_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    last4: Mapped[str] = mapped_column(String(8), default="", nullable=False)


class Connection(Base, TimestampMixin):
    __tablename__ = "connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # gsc|wordpress|ga4|sheets|telegram
    config_enc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)


class ProviderAccount(Base, TimestampMixin):
    """Multiple accounts/keys per provider supporting automatic priority failover."""
    __tablename__ = "provider_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey("providers.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(24), default="api_key", nullable=False)  # api_key|oauth_signin|cli_login
    key_ref_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    last4: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)  # active|resting|disabled
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quota_used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quota_limit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_failover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failover_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProviderModel(Base):
    """Full fetched or manually configured model catalogue entries per provider & workspace."""

    __tablename__ = "provider_models"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider_id", "model_id", name="uq_provider_models_ws_prov_mid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey("providers.id"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    family: Mapped[str] = mapped_column(String(128), default="", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), default="fetched", nullable=False)  # fetched|manual
    metadata_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RoleModelAssignment(Base, TimestampMixin):
    """Workspace-wide provider + model assignment per role (orchestrator/worker/reviewer/coding_*)."""

    __tablename__ = "role_model_assignments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "role_key", name="uq_role_model_ws_role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    role_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("providers.id"), nullable=True)
    tier: Mapped[str] = mapped_column(String(16), default="mid", nullable=False)
    model: Mapped[str] = mapped_column(String(255), default="auto", nullable=False)
