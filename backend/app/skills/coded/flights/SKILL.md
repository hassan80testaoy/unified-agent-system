---
name: flights
category: travel
requires_approval: true
---

# البحث وحجز الطيران (`flights`)

Search flight routes honestly via browser/API and always pause for explicit approval before payment/booking.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "origin",
    "destination"
  ],
  "properties": {
    "origin": {
      "type": "string"
    },
    "destination": {
      "type": "string"
    },
    "date": {
      "type": "string"
    },
    "passengers": {
      "type": "integer",
      "minimum": 1
    }
  }
}
```
