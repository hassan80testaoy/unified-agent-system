"""Strict Chat Output Guard — prevents raw JSON plans and <tool_call> markup from leaking to chat UI (§8.0)."""

from __future__ import annotations

import json
import re
from typing import Any

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>.*?(?:</tool_call>|$)", re.IGNORECASE | re.DOTALL)
_TOOL_CALL_CLOSE_RE = re.compile(r"</?tool_call>", re.IGNORECASE)
_FUNCTION_TAG_RE = re.compile(r"<function=[^>]*>.*?(?:</function>|$)", re.IGNORECASE | re.DOTALL)
_THOUGHT_TAG_RE = re.compile(r"<(?:thought|thinking)>.*?(?:</(?:thought|thinking)>|$)", re.IGNORECASE | re.DOTALL)

_INTERNAL_JSON_KEYS = (
    {"origin", "task"},
    {"origin", "skills"},
    {"thought", "tool", "args"},
    {"tool", "args"},
    {"tool_calls"},
)


def extract_fallback_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse textual <tool_call>{...}</tool_call> blocks for backend execution/guard inspection without exposing raw text."""
    out: list[dict[str, Any]] = []
    if not text:
        return out
    for m in re.finditer(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", text, re.IGNORECASE | re.DOTALL):
        body = m.group(1).strip()
        if not body:
            continue
        try:
            obj = json.loads(body)
            if isinstance(obj, dict) and (obj.get("name") or obj.get("tool")):
                out.append(
                    {
                        "name": str(obj.get("name") or obj.get("tool")),
                        "arguments": obj.get("arguments") or obj.get("args") or {},
                    }
                )
        except Exception:
            out.append({"raw": body[:200], "malformed": True})
    return out


def is_internal_plan_json(raw: str) -> dict[str, Any] | None:
    """Return parsed dict if `raw` (or stripped code fence) is a raw internal plan/tool JSON payload."""
    txt = (raw or "").strip()
    if not txt:
        return None
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*", "", txt, flags=re.I)
        txt = re.sub(r"\s*```$", "", txt).strip()
    if not (txt.startswith("{") and txt.endswith("}")):
        return None
    try:
        obj = json.loads(txt)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    keys = set(obj.keys())
    if any(req <= keys for req in _INTERNAL_JSON_KEYS):
        return obj
    if "skills" in keys and ("task" in keys or "steps" in keys):
        return obj
    return None


def sanitize_chat_output(text: str, *, strip_whitespace: bool = True) -> tuple[str, dict[str, Any]]:
    """Strip all <tool_call>, <thought>, <function>, and internal plan JSON from user-visible chat text."""
    raw = text or ""
    meta: dict[str, Any] = {
        "had_tool_markup": bool("<tool_call" in raw.lower() or "</tool_call>" in raw.lower()),
        "had_internal_plan_json": False,
        "extracted_calls": extract_fallback_tool_calls(raw),
    }

    cleaned = _TOOL_CALL_BLOCK_RE.sub("", raw)
    cleaned = _TOOL_CALL_CLOSE_RE.sub("", cleaned)
    cleaned = _FUNCTION_TAG_RE.sub("", cleaned)
    cleaned = _THOUGHT_TAG_RE.sub("", cleaned)

    # Strip inline fenced internal JSON blocks if mixed with normal Arabic text
    def _strip_fenced_plan(match: re.Match[str]) -> str:
        inner = match.group(0)
        if is_internal_plan_json(inner):
            meta["had_internal_plan_json"] = True
            return ""
        return inner

    cleaned = re.sub(r"```(?:json)?\s*\{[\s\S]*?\}\s*```", _strip_fenced_plan, cleaned, flags=re.IGNORECASE)
    plan_obj = is_internal_plan_json(cleaned)
    if plan_obj is not None:
        meta["had_internal_plan_json"] = True
        task_desc = str(plan_obj.get("task") or plan_obj.get("goal") or "").strip()
        cleaned = (
            f"جارٍ تنفيذ المهمة مباشرة بدون عرض خطط داخلية: {task_desc}"
            if task_desc
            else ""
        )

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if strip_whitespace:
        cleaned = cleaned.strip()
    return cleaned, meta


class StreamingOutputFilter:
    """Line/token buffer preventing partial <tool_call> tags or raw JSON plan prefixes from flashing in SSE streams."""

    def __init__(self) -> None:
        self._buf = ""
        self._suppress_until_tag_close = False

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._buf += chunk
        out_parts: list[str] = []

        while self._buf:
            low = self._buf.lower()
            if self._suppress_until_tag_close:
                end_idx = low.find("</tool_call>")
                if end_idx == -1:
                    # Wait for closing tag, keep tail trimmed
                    if len(self._buf) > 4096:
                        self._buf = self._buf[-512:]
                    break
                self._buf = self._buf[end_idx + len("</tool_call>") :]
                self._suppress_until_tag_close = False
                continue

            open_idx = low.find("<tool_call")
            if open_idx != -1:
                if open_idx > 0:
                    safe, _ = sanitize_chat_output(self._buf[:open_idx], strip_whitespace=False)
                    if safe:
                        out_parts.append(safe)
                self._buf = self._buf[open_idx:]
                self._suppress_until_tag_close = True
                continue

            # Hold trailing '<' so split '<tool_call' across token boundaries never leaks
            last_lt = self._buf.rfind("<")
            if last_lt != -1 and len(self._buf) - last_lt <= 16:
                prefix = self._buf[:last_lt]
                if prefix:
                    safe, _ = sanitize_chat_output(prefix, strip_whitespace=False)
                    if safe:
                        out_parts.append(safe)
                self._buf = self._buf[last_lt:]
                break

            safe, _ = sanitize_chat_output(self._buf, strip_whitespace=False)
            self._buf = ""
            if safe:
                out_parts.append(safe)

        return "".join(out_parts)

    def flush(self) -> str:
        if self._suppress_until_tag_close:
            self._buf = ""
            self._suppress_until_tag_close = False
            return ""
        safe, _ = sanitize_chat_output(self._buf, strip_whitespace=False)
        self._buf = ""
        return safe
