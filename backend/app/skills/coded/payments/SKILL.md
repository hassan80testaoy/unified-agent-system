---
name: payments
category: finance
requires_approval: true
---

# الدفع الآمن ببوابة موافقة (`payments`)

Always create an explicit spend approval gate + Blind-Fill Vault card reference; never store raw card details.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "merchant",
    "amount_usd"
  ],
  "properties": {
    "merchant": {
      "type": "string"
    },
    "amount_usd": {
      "type": "number",
      "minimum": 0
    },
    "currency": {
      "type": "string"
    }
  }
}
```
