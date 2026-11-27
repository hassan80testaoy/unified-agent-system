"""SEO tables for GSC metrics, rank tracking, keyword research, content drafts, local listings, and outreach contact_log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, PortableJSON, TimestampMixin, new_uuid, utcnow


class GscMetric(Base, TimestampMixin):
    __tablename__ = "gsc_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    site_url: Mapped[str] = mapped_column(String(255), nullable=False)
    page: Mapped[str | None] = mapped_column(String(512), nullable=True)
    query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date: Mapped[str] = mapped_column(String(16), nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ctr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    position: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class RankSnapshot(Base, TimestampMixin):
    __tablename__ = "rank_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(8), default="SA", nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="ar", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="gsc", nullable=False)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_url: Mapped[str | None] = mapped_column(String(512), nullable=True)


class KeywordIdea(Base, TimestampMixin):
    __tablename__ = "keyword_ideas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    seed: Mapped[str] = mapped_column(String(255), nullable=False)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(8), default="SA", nullable=False)
    intent: Mapped[str] = mapped_column(String(32), default="informational", nullable=False)
    source: Mapped[str] = mapped_column(String(48), default="autocomplete", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ContentDraft(Base, TimestampMixin):
    __tablename__ = "content_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    dialect: Mapped[str] = mapped_column(String(24), default="msa", nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)  # draft|pending_review|approved|published
    target_url: Mapped[str | None] = mapped_column(String(512), nullable=True)


class LocalListing(Base, TimestampMixin):
    __tablename__ = "local_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str] = mapped_column(String(8), default="EG", nullable=False)
    directory: Mapped[str] = mapped_column(String(128), nullable=False)
    listing_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    nap_verified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    meta_json: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)


class ContactLog(Base):
    __tablename__ = "contact_log"
    __table_args__ = (Index("idx_contact_ws_key", "workspace_id", "contact_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    contact_key: Mapped[str] = mapped_column(String(255), nullable=False)  # email or handle hash/key
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
