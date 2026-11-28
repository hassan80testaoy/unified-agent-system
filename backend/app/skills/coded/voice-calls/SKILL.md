---
name: voice-calls
category: voice
requires_approval: false
---

# المكالمات والصوتيات (STT/TTS) (`voice-calls`)

Transcribe incoming voice memos cleanly and prepare TTS audio synthesis payloads for chat read-aloud.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "text"
  ],
  "properties": {
    "text": {
      "type": "string"
    },
    "mode": {
      "type": "string",
      "enum": [
        "transcribe",
        "tts_plan"
      ]
    }
  }
}
```
