"""Coded platform skill `restaurant-booking` (حجز المطاعم)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'restaurant-booking'
INPUT_SCHEMA = {'type': 'object', 'required': ['restaurant', 'party_size', 'datetime_iso'], 'properties': {'restaurant': {'type': 'string'}, 'party_size': {'type': 'integer', 'minimum': 1}, 'datetime_iso': {'type': 'string'}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
