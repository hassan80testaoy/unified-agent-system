---
name: messaging
category: communication
requires_approval: false
---

# بوابة المراسلة الموحدة (`messaging`)

Route cross-platform messages (WhatsApp, Telegram, Email) to the unified thread; never send unsolicited.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "channel",
    "message"
  ],
  "properties": {
    "channel": {
      "type": "string",
      "enum": [
        "whatsapp",
        "telegram",
        "email"
      ]
    },
    "peer_id": {
      "type": "string"
    },
    "message": {
      "type": "string"
    }
  }
}
```
