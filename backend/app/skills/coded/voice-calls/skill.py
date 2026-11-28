"""Coded platform skill `voice-calls` (المكالمات والصوتيات (STT/TTS))."""

from app.skills.coded.catalog import execute_coded_skill

SKILL_SLUG = 'voice-calls'
INPUT_SCHEMA = {'type': 'object', 'required': ['text'], 'properties': {'text': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['transcribe', 'tts_plan']}}}

def execute(db, workspace_id: str, user_id: str, params: dict):
    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)
