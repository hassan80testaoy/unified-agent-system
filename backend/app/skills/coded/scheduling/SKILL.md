---
name: scheduling
category: calendar
requires_approval: false
---

# جدولة المهام والتقويم (`scheduling`)

Create recurring cron automations wired to the triggers scheduler with visible workspace notifications.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "name",
    "cron_expr",
    "action_prompt"
  ],
  "properties": {
    "name": {
      "type": "string"
    },
    "cron_expr": {
      "type": "string"
    },
    "action_prompt": {
      "type": "string"
    },
    "channels": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  }
}
```
