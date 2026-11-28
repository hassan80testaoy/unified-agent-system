"""Coded platform skill `presentations` (إنشاء العروض التقديمية)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'presentations'
INPUT_SCHEMA = {'type': 'object', 'required': ['title', 'topic'], 'properties': {'title': {'type': 'string'}, 'topic': {'type': 'string'}, 'slides': {'type': 'array'}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
