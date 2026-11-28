"""Coded platform skill `messaging` (بوابة المراسلة الموحدة)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'messaging'
INPUT_SCHEMA = {'type': 'object', 'required': ['channel', 'message'], 'properties': {'channel': {'type': 'string', 'enum': ['whatsapp', 'telegram', 'email']}, 'peer_id': {'type': 'string'}, 'message': {'type': 'string'}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
