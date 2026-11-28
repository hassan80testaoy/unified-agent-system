"""Coded platform skill `flights` (البحث وحجز الطيران)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'flights'
INPUT_SCHEMA = {'type': 'object', 'required': ['origin', 'destination'], 'properties': {'origin': {'type': 'string'}, 'destination': {'type': 'string'}, 'date': {'type': 'string'}, 'passengers': {'type': 'integer', 'minimum': 1}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
