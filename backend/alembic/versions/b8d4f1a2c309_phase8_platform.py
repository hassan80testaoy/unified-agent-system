"""phase8_platform

Revision ID: b8d4f1a2c309
Revises: a7c9e2f4b108
Create Date: 2026-11-28 18:20:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

revision = "b8d4f1a2c309"
down_revision = "a7c9e2f4b108"
branch_labels = None
depends_on = None

JSON_COL = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


def _add_missing_columns(insp: sa.Inspector, table: str, columns: list[sa.Column]) -> None:
    existing = {c["name"] for c in insp.get_columns(table)}
    missing = [col for col in columns if col.name not in existing]
    if missing:
        with op.batch_alter_table(table, schema=None) as batch_op:
            for col in missing:
                batch_op.add_column(col)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing_tables = set(insp.get_table_names())

    if "target_site_projects" not in existing_tables:
        op.create_table(
            "target_site_projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("domain", sa.String(length=255), nullable=False),
            sa.Column("url", sa.String(length=512), nullable=False),
            sa.Column("maps_url", sa.String(length=512), nullable=True),
            sa.Column("default_identity_json", JSON_COL, nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("target_site_projects", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_target_site_projects_domain"), ["domain"], unique=False)
            batch_op.create_index(batch_op.f("ix_target_site_projects_workspace_id"), ["workspace_id"], unique=False)

    if "backlink_library_sites" not in existing_tables:
        op.create_table(
            "backlink_library_sites",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("domain", sa.String(length=255), nullable=False),
            sa.Column("signup_url", sa.String(length=512), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("signup_flow", sa.String(length=32), nullable=False),
            sa.Column("discovered_fields_json", JSON_COL, nullable=False),
            sa.Column("playbook_md", sa.Text(), nullable=False),
            sa.Column("playbook_path", sa.String(length=512), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("past_results_json", JSON_COL, nullable=False),
            sa.Column("success_count", sa.Integer(), nullable=False),
            sa.Column("fail_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "domain", name="uq_bl_library_ws_domain"),
        )
        with op.batch_alter_table("backlink_library_sites", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_backlink_library_sites_domain"), ["domain"], unique=False)
            batch_op.create_index(batch_op.f("ix_backlink_library_sites_workspace_id"), ["workspace_id"], unique=False)

    if "backlink_placements" not in existing_tables:
        op.create_table(
            "backlink_placements",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("target_project_id", sa.String(length=36), nullable=False),
            sa.Column("library_site_id", sa.String(length=36), nullable=False),
            sa.Column("account_id", sa.String(length=36), nullable=True),
            sa.Column("email_used", sa.String(length=255), nullable=False),
            sa.Column("profile_url", sa.String(length=512), nullable=True),
            sa.Column("link_type", sa.String(length=24), nullable=False),
            sa.Column("live", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["library_site_id"], ["backlink_library_sites.id"]),
            sa.ForeignKeyConstraint(["target_project_id"], ["target_site_projects.id"]),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("backlink_placements", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_backlink_placements_library_site_id"), ["library_site_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_backlink_placements_target_project_id"), ["target_project_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_backlink_placements_workspace_id"), ["workspace_id"], unique=False)

    if "messaging_gateway_configs" not in existing_tables:
        op.create_table(
            "messaging_gateway_configs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("channel", sa.String(length=24), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("config_enc", sa.Text(), nullable=True),
            sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
            sa.Column("continuity_mode", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "channel", name="uq_msg_gateway_ws_channel"),
        )
        with op.batch_alter_table("messaging_gateway_configs", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_messaging_gateway_configs_channel"), ["channel"], unique=False)
            batch_op.create_index(batch_op.f("ix_messaging_gateway_configs_workspace_id"), ["workspace_id"], unique=False)

    if "messaging_conversation_maps" not in existing_tables:
        op.create_table(
            "messaging_conversation_maps",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("channel", sa.String(length=24), nullable=False),
            sa.Column("external_peer_id", sa.String(length=160), nullable=False),
            sa.Column("external_peer_name", sa.String(length=160), nullable=False),
            sa.Column("thread_id", sa.String(length=36), nullable=False),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "channel", "external_peer_id", name="uq_msg_conv_map_peer"),
        )
        with op.batch_alter_table("messaging_conversation_maps", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_messaging_conversation_maps_channel"), ["channel"], unique=False)
            batch_op.create_index(batch_op.f("ix_messaging_conversation_maps_external_peer_id"), ["external_peer_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_messaging_conversation_maps_thread_id"), ["thread_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_messaging_conversation_maps_workspace_id"), ["workspace_id"], unique=False)

    if "messaging_event_logs" not in existing_tables:
        op.create_table(
            "messaging_event_logs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("channel", sa.String(length=24), nullable=False),
            sa.Column("direction", sa.String(length=16), nullable=False),
            sa.Column("external_peer_id", sa.String(length=160), nullable=False),
            sa.Column("thread_id", sa.String(length=36), nullable=True),
            sa.Column("content_preview", sa.Text(), nullable=False),
            sa.Column("media_kind", sa.String(length=24), nullable=True),
            sa.Column("transcript", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("messaging_event_logs", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_messaging_event_logs_channel"), ["channel"], unique=False)
            batch_op.create_index(batch_op.f("ix_messaging_event_logs_thread_id"), ["thread_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_messaging_event_logs_workspace_id"), ["workspace_id"], unique=False)

    if "agent_delegation_tasks" not in existing_tables:
        op.create_table(
            "agent_delegation_tasks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("parent_agent_id", sa.String(length=64), nullable=False),
            sa.Column("child_agent_id", sa.String(length=64), nullable=False),
            sa.Column("trust_bundle_id", sa.String(length=36), nullable=True),
            sa.Column("depth", sa.Integer(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("result_summary", sa.Text(), nullable=False),
            sa.Column("permissions_snapshot_json", JSON_COL, nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("agent_delegation_tasks", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_agent_delegation_tasks_child_agent_id"), ["child_agent_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_agent_delegation_tasks_parent_agent_id"), ["parent_agent_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_agent_delegation_tasks_workspace_id"), ["workspace_id"], unique=False)

    if "platform_projects" not in existing_tables:
        op.create_table(
            "platform_projects",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column("instructions_md", sa.Text(), nullable=False),
            sa.Column("knowledge_files_json", JSON_COL, nullable=False),
            sa.Column("sandbox_dir", sa.String(length=512), nullable=False),
            sa.Column("autonomy_mode", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "slug", name="uq_platform_project_ws_slug"),
        )
        with op.batch_alter_table("platform_projects", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_platform_projects_slug"), ["slug"], unique=False)
            batch_op.create_index(batch_op.f("ix_platform_projects_workspace_id"), ["workspace_id"], unique=False)

    if "wide_research_jobs" not in existing_tables:
        op.create_table(
            "wide_research_jobs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("thread_id", sa.String(length=36), nullable=True),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("items_json", JSON_COL, nullable=False),
            sa.Column("results_json", JSON_COL, nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("progress_done", sa.Integer(), nullable=False),
            sa.Column("progress_total", sa.Integer(), nullable=False),
            sa.Column("artifact_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("wide_research_jobs", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_wide_research_jobs_thread_id"), ["thread_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_wide_research_jobs_workspace_id"), ["workspace_id"], unique=False)

    if "plugin_catalog_installs" not in existing_tables:
        op.create_table(
            "plugin_catalog_installs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("plugin_slug", sa.String(length=80), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("repo_url", sa.String(length=512), nullable=False),
            sa.Column("pinned_commit", sa.String(length=64), nullable=False),
            sa.Column("category", sa.String(length=48), nullable=False),
            sa.Column("installed_path", sa.String(length=512), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("manifest_json", JSON_COL, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "plugin_slug", name="uq_plugin_catalog_ws_slug"),
        )
        with op.batch_alter_table("plugin_catalog_installs", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_plugin_catalog_installs_plugin_slug"), ["plugin_slug"], unique=False)
            batch_op.create_index(batch_op.f("ix_plugin_catalog_installs_workspace_id"), ["workspace_id"], unique=False)

    if "self_dev_proposals" not in existing_tables:
        op.create_table(
            "self_dev_proposals",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("thread_id", sa.String(length=36), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("plan_md", sa.Text(), nullable=False),
            sa.Column("branch_name", sa.String(length=128), nullable=False),
            sa.Column("worktree_path", sa.String(length=512), nullable=False),
            sa.Column("diff_text", sa.Text(), nullable=False),
            sa.Column("test_output", sa.Text(), nullable=False),
            sa.Column("tests_passed", sa.Boolean(), nullable=False),
            sa.Column("review_item_id", sa.String(length=36), nullable=True),
            sa.Column("approval_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("self_dev_proposals", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_self_dev_proposals_thread_id"), ["thread_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_self_dev_proposals_workspace_id"), ["workspace_id"], unique=False)

    if "eval_runs" not in existing_tables:
        op.create_table(
            "eval_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("workspace_id", sa.String(length=36), nullable=False),
            sa.Column("suite_name", sa.String(length=80), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("passed_count", sa.Integer(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=False),
            sa.Column("delta_vs_prev", sa.Float(), nullable=False),
            sa.Column("results_json", JSON_COL, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("eval_runs", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_eval_runs_workspace_id"), ["workspace_id"], unique=False)

    _add_missing_columns(
        insp,
        "chat_threads",
        [
            sa.Column("autonomy_mode", sa.String(length=24), nullable=False, server_default="checkpoint"),
            sa.Column("agent_id", sa.String(length=64), nullable=True),
            sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("pending_steering_json", JSON_COL, nullable=False, server_default="[]"),
            sa.Column("loaded_memory_ids_json", JSON_COL, nullable=False, server_default="[]"),
        ],
    )

    _add_missing_columns(
        insp,
        "agents",
        [
            sa.Column("provider_id", sa.String(length=64), nullable=True),
            sa.Column("model_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("mode_key", sa.String(length=64), nullable=False, server_default="delegate"),
            sa.Column("skills_json", JSON_COL, nullable=False, server_default="[]"),
            sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
            sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table("agents", schema=None) as batch_op:
        batch_op.drop_column("status")
        batch_op.drop_column("is_custom")
        batch_op.drop_column("system_prompt")
        batch_op.drop_column("skills_json")
        batch_op.drop_column("mode_key")
        batch_op.drop_column("model_name")
        batch_op.drop_column("provider_id")

    with op.batch_alter_table("chat_threads", schema=None) as batch_op:
        batch_op.drop_column("loaded_memory_ids_json")
        batch_op.drop_column("pending_steering_json")
        batch_op.drop_column("paused")
        batch_op.drop_column("agent_id")
        batch_op.drop_column("autonomy_mode")

    op.drop_table("eval_runs")
    op.drop_table("self_dev_proposals")
    op.drop_table("plugin_catalog_installs")
    op.drop_table("wide_research_jobs")
    op.drop_table("platform_projects")
    op.drop_table("agent_delegation_tasks")
    op.drop_table("messaging_event_logs")
    op.drop_table("messaging_conversation_maps")
    op.drop_table("messaging_gateway_configs")
    op.drop_table("backlink_placements")
    op.drop_table("backlink_library_sites")
    op.drop_table("target_site_projects")
