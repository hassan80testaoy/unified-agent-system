"""Coded platform skill `wake-subscriptions` (اشتراكات الاستيقاظ الفوري)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'wake-subscriptions'
INPUT_SCHEMA = {'type': 'object', 'required': ['source', 'trigger_prompt'], 'properties': {'source': {'type': 'string'}, 'trigger_prompt': {'type': 'string'}, 'site_domain': {'type': 'string'}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
