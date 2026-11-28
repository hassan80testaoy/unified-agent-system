---
name: files
category: output
requires_approval: false
---

# معالجة وتصدير الملفات (`files`)

Sandboxed artifact file creation & reading inside workspace artifacts/files only (no path traversal).

## Input Schema
```json
{
  "type": "object",
  "required": [
    "action",
    "filename"
  ],
  "properties": {
    "action": {
      "type": "string",
      "enum": [
        "write",
        "read",
        "list"
      ]
    },
    "filename": {
      "type": "string"
    },
    "content": {
      "type": "string"
    }
  }
}
```
