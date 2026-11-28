"""Coded platform skill `files` (معالجة وتصدير الملفات)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'files'
INPUT_SCHEMA = {'type': 'object', 'required': ['action', 'filename'], 'properties': {'action': {'type': 'string', 'enum': ['write', 'read', 'list']}, 'filename': {'type': 'string'}, 'content': {'type': 'string'}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
