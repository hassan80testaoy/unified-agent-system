"""phase7_goals_wakes_routing_hypnos

Revision ID: a7c9e2f4b108
Revises: 515fc7a1f2f9
Create Date: 2026-11-28 16:52:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision = "a7c9e2f4b108"
down_revision = "515fc7a1f2f9"
branch_labels = None
depends_on = None

JSON_COL = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "code_review_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("repo", sa.String(length=255), nullable=False),
        sa.Column("external_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("author", sa.String(length=128), nullable=False),
        sa.Column("source_branch", sa.String(length=128), nullable=False),
        sa.Column("target_branch", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("diff_text", sa.Text(), nullable=False),
        sa.Column("checks_json", JSON_COL, nullable=False),
        sa.Column("comments_json", JSON_COL, nullable=False),
        sa.Column("meta_json", JSON_COL, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("code_review_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_code_review_items_provider"), ["provider"], unique=False)
        batch_op.create_index(batch_op.f("ix_code_review_items_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "hypnos_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_hour", sa.Integer(), nullable=False),
        sa.Column("active_lora_version_id", sa.String(length=36), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_exam_score", sa.Float(), nullable=True),
        sa.Column("prompt_tokens_before", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens_after", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_hypnos_config_ws"),
    )
    with op.batch_alter_table("hypnos_configs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hypnos_configs_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "hypnos_feedback_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("context_summary", sa.Text(), nullable=False),
        sa.Column("correction_text", sa.Text(), nullable=False),
        sa.Column("command_name", sa.String(length=160), nullable=False),
        sa.Column("meta_json", JSON_COL, nullable=False),
        sa.Column("excluded_for_unlearn", sa.Boolean(), nullable=False),
        sa.Column("never_train", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("hypnos_feedback_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hypnos_feedback_events_excluded_for_unlearn"), ["excluded_for_unlearn"], unique=False)
        batch_op.create_index(batch_op.f("ix_hypnos_feedback_events_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "hypnos_lora_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("version_tag", sa.String(length=32), nullable=False),
        sa.Column("parent_version_id", sa.String(length=36), nullable=True),
        sa.Column("adapter_path", sa.String(length=512), nullable=False),
        sa.Column("training_event_ids_json", JSON_COL, nullable=False),
        sa.Column("habits_json", JSON_COL, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("exam_score", sa.Float(), nullable=False),
        sa.Column("baseline_score", sa.Float(), nullable=False),
        sa.Column("exam_passed", sa.Boolean(), nullable=False),
        sa.Column("exam_details_json", JSON_COL, nullable=False),
        sa.Column("prompt_delta_tokens", sa.Integer(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("hypnos_lora_versions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_hypnos_lora_versions_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "proactive_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("loop_name", sa.String(length=48), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("title_ar", sa.String(length=255), nullable=False),
        sa.Column("title_en", sa.String(length=255), nullable=False),
        sa.Column("detail_md", sa.Text(), nullable=False),
        sa.Column("action_payload_json", JSON_COL, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("decided_by", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_result_json", JSON_COL, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "loop_name", "dedup_key", name="uq_proactive_proposal_dedup"),
    )
    with op.batch_alter_table("proactive_proposals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_proactive_proposals_loop_name"), ["loop_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_proactive_proposals_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "routing_config_backups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("snapshot_json", JSON_COL, nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("routing_config_backups", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_routing_config_backups_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "routing_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("context_key", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=False),
        sa.Column("api_key_env", sa.String(length=128), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("rules_json", JSON_COL, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "context_key", name="uq_routing_profile_ws_ctx"),
    )
    with op.batch_alter_table("routing_profiles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_routing_profiles_context_key"), ["context_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_routing_profiles_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "site_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("target_backlink_url", sa.String(length=512), nullable=True),
        sa.Column("profile_url", sa.String(length=512), nullable=True),
        sa.Column("network_path", sa.String(length=32), nullable=False),
        sa.Column("screenshot_url", sa.String(length=512), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("summary_json", JSON_COL, nullable=False),
        sa.Column("findings_json", JSON_COL, nullable=False),
        sa.Column("report_md", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("site_audits", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_site_audits_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "wake_event_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_event_id", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("chat_thread_id", sa.String(length=36), nullable=True),
        sa.Column("chat_message_id", sa.String(length=36), nullable=True),
        sa.Column("payload_json", JSON_COL, nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["wake_subscriptions.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "subscription_id", "external_event_id", name="uq_wake_event_dedup"),
    )
    with op.batch_alter_table("wake_event_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wake_event_logs_external_event_id"), ["external_event_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wake_event_logs_subscription_id"), ["subscription_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wake_event_logs_workspace_id"), ["workspace_id"], unique=False)

    with op.batch_alter_table("mcp_servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("oauth_config_enc", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("oauth_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("oauth_auto_refresh", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("wake_subscriptions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("filter_json", JSON_COL, nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("action_type", sa.String(length=32), nullable=False, server_default="chat_notify"))
        batch_op.add_column(sa.Column("thread_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f("ix_wake_subscriptions_thread_id"), ["thread_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("wake_subscriptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wake_subscriptions_thread_id"))
        batch_op.drop_column("run_id")
        batch_op.drop_column("thread_id")
        batch_op.drop_column("action_type")
        batch_op.drop_column("filter_json")

    with op.batch_alter_table("mcp_servers", schema=None) as batch_op:
        batch_op.drop_column("oauth_auto_refresh")
        batch_op.drop_column("oauth_expires_at")
        batch_op.drop_column("oauth_config_enc")

    op.drop_table("wake_event_logs")
    op.drop_table("site_audits")
    op.drop_table("routing_profiles")
    op.drop_table("routing_config_backups")
    op.drop_table("proactive_proposals")
    op.drop_table("hypnos_lora_versions")
    op.drop_table("hypnos_feedback_events")
    op.drop_table("hypnos_configs")
    op.drop_table("code_review_items")
