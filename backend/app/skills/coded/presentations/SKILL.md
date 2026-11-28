---
name: presentations
category: output
requires_approval: false
---

# إنشاء العروض التقديمية (`presentations`)

Generate interactive multi-slide RTL HTML presentation decks saved as previewable Artifacts.

## Input Schema
```json
{
  "type": "object",
  "required": [
    "title",
    "topic"
  ],
  "properties": {
    "title": {
      "type": "string"
    },
    "topic": {
      "type": "string"
    },
    "slides": {
      "type": "array"
    }
  }
}
```
