---
name: shopping
category: commerce
requires_approval: true
---

# مقارنة وشراء المنتجات (`shopping`)

Compare real product prices via live browser and require approval + Blind-Fill Vault before checkout.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "query"
  ],
  "properties": {
    "query": {
      "type": "string"
    },
    "max_price_usd": {
      "type": "number",
      "minimum": 0
    }
  }
}
```
