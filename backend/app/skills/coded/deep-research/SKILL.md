---
name: deep-research
category: research
requires_approval: false
---

# البحث العميق والمتوازي (`deep-research`)

Fan out isolated parallel sub-agent research items (Wide Research) and export structured Markdown/CSV deliverable.

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
    "items": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  }
}
```
