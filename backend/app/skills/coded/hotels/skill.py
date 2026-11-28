"""Coded platform skill `hotels` (البحث وحجز الفنادق)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'hotels'
INPUT_SCHEMA = {'type': 'object', 'required': ['city'], 'properties': {'city': {'type': 'string'}, 'checkin': {'type': 'string'}, 'checkout': {'type': 'string'}, 'guests': {'type': 'integer', 'minimum': 1}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
