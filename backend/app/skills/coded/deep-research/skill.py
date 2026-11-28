"""Coded platform skill `deep-research` (البحث العميق والمتوازي)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'deep-research'
INPUT_SCHEMA = {'type': 'object', 'required': ['query'], 'properties': {'query': {'type': 'string'}, 'items': {'type': 'array', 'items': {'type': 'string'}}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
