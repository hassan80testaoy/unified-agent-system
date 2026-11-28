---
name: wake-subscriptions
category: proactive
requires_approval: false
---

# اشتراكات الاستيقاظ الفوري (`wake-subscriptions`)

Create event-driven (non-polling) wake subscriptions for Gmail/Calendar/Webhook events.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "source",
    "trigger_prompt"
  ],
  "properties": {
    "source": {
      "type": "string"
    },
    "trigger_prompt": {
      "type": "string"
    },
    "site_domain": {
      "type": "string"
    }
  }
}
```
