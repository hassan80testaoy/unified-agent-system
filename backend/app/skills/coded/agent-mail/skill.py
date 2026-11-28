"""Coded platform skill `agent-mail` (إدارة البريد والتحقق)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'agent-mail'
INPUT_SCHEMA = {'type': 'object', 'properties': {'email': {'type': 'string'}, 'site_slug': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['status', 'alias']}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
