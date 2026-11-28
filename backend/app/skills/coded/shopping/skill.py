"""Coded platform skill `shopping` (مقارنة وشراء المنتجات)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'shopping'
INPUT_SCHEMA = {'type': 'object', 'required': ['query'], 'properties': {'query': {'type': 'string'}, 'max_price_usd': {'type': 'number', 'minimum': 0}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
