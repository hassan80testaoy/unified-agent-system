"""Coded platform skill `scheduling` (جدولة المهام والتقويم)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'scheduling'
INPUT_SCHEMA = {'type': 'object', 'required': ['name', 'cron_expr', 'action_prompt'], 'properties': {'name': {'type': 'string'}, 'cron_expr': {'type': 'string'}, 'action_prompt': {'type': 'string'}, 'channels': {'type': 'array', 'items': {'type': 'string'}}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
