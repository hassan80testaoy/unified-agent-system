"""Coded platform skill `payments` (الدفع الآمن ببوابة موافقة)."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'payments'
INPUT_SCHEMA = {'type': 'object', 'required': ['merchant', 'amount_usd'], 'properties': {'merchant': {'type': 'string'}, 'amount_usd': {'type': 'number', 'minimum': 0}, 'currency': {'type': 'string'}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
