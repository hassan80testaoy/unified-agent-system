---
name: hotels
category: travel
requires_approval: true
---

# البحث وحجز الفنادق (`hotels`)

Search real hotel availability and pause on checkpoint approval before committing reservation or card details.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "city"
  ],
  "properties": {
    "city": {
      "type": "string"
    },
    "checkin": {
      "type": "string"
    },
    "checkout": {
      "type": "string"
    },
    "guests": {
      "type": "integer",
      "minimum": 1
    }
  }
}
```
