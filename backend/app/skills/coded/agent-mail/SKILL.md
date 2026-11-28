---
name: agent-mail
category: communication
requires_approval: false
---

# إدارة البريد والتحقق (`agent-mail`)

Manage read-only IMAP mailboxes, generate safe +aliases, and extract verification links transiently without storage.

## Input Schema
```json
{
  "type": "object",
  "properties": {
    "email": {
      "type": "string"
    },
    "site_slug": {
      "type": "string"
    },
    "action": {
      "type": "string",
      "enum": [
        "status",
        "alias"
      ]
    }
  }
}
```
