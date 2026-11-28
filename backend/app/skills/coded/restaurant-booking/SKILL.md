---
name: restaurant-booking
category: personal
requires_approval: true
---

# حجز المطاعم (`restaurant-booking`)

Check table availability via browser and ask approval confirmation before finalizing reservation.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "restaurant",
    "party_size",
    "datetime_iso"
  ],
  "properties": {
    "restaurant": {
      "type": "string"
    },
    "party_size": {
      "type": "integer",
      "minimum": 1
    },
    "datetime_iso": {
      "type": "string"
    }
  }
}
```
