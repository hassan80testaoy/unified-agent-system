"""Execution LoopGuard & Runaway Tool-Call Guard (adapted from agent-flow-harness/afh/engine/loopguard.py, §8.0)."""

from __future__ import annotations

from collections import deque
import json
import re
from typing import Any

WINDOW = 8
NOTE_AT = 3
STOP_AT = 3
MIN_TEXT = 24
WAITING_TOOLS = {"jobs", "todo", "goal"}
CHANGE_TOOLS = {"write", "edit", "patch", "write_file", "edit_file"}
SCREEN_TOOLS = {"browser", "browser_click", "browser_scroll", "browser_screenshot", "computer"}
MOVE_ACTIONS = {"press", "key", "scroll", "wait", "screenshot", "zoom", "hover", "back", "forward", "reload"}

LOOP_NOTE_TEMPLATE = (
    "[تنبيه منع الدوران: تم تكرار نفس الإجراء {n} مرات مؤخراً ({what}). "
    "توقف عن تكراره واتجه لمسار مختلف أو توقف بصدق واشرح العائق للمالك.]"
)


def _norm_words(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip(" .:!")


def _call_sig(name: str, args: Any) -> str:
    try:
        return f"{name} {json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    except (TypeError, ValueError):
        return f"{name} {args}"


class LoopGuard:
    """Detect repetitive assistant messages or repetitive tool-call arguments and stop runaway loops."""

    def __init__(self, *, window: int = WINDOW, note_at: int = NOTE_AT, stop_at: int = STOP_AT) -> None:
        self.window = window
        self.note_at = note_at
        self.stop_at = stop_at
        self.recent: deque[set[str]] = deque(maxlen=window)
        self.noted: str | None = None
        self.after_note: int = 0

    def record_step(self, *, text: str = "", tool_calls: list[dict[str, Any]] | None = None) -> tuple[str, str | None]:
        """Record one agent step and return (decision: 'ok'|'note'|'stop', reason_or_note)."""
        calls = tool_calls or []
        names = {str(c.get("name") or c.get("tool") or "").strip().lower() for c in calls}
        if names & CHANGE_TOOLS:
            self.recent.clear()
            self.noted = None
            self.after_note = 0

        keys: set[str] = set()
        for c in calls:
            cname = str(c.get("name") or c.get("tool") or "").strip().lower()
            if not cname:
                continue
            if cname in WAITING_TOOLS:
                continue
            args = c.get("arguments") or c.get("args") or {}
            act = str((args if isinstance(args, dict) else {}).get("action") or "").lower()
            if cname in SCREEN_TOOLS and (not act or act in MOVE_ACTIONS):
                continue
            keys.add(_call_sig(cname, args))

        norm_txt = _norm_words(text)
        if len(norm_txt) >= MIN_TEXT and not calls:
            keys.add(f"text:{norm_txt[:180]}")

        self.recent.append(keys)
        for k in keys:
            cnt = sum(1 for bucket in self.recent if k in bucket)
            if self.noted == k:
                self.after_note += 1
                if self.after_note >= self.stop_at:
                    return "stop", f"runaway_loop:{k[:100]}"
            elif cnt >= self.note_at:
                self.noted = k
                self.after_note = 0
                return "note", LOOP_NOTE_TEMPLATE.format(n=cnt, what=k[:100])
        return "ok", None


class RunawayToolCallGuard:
    """Stops models that repeatedly emit raw <tool_call> text instead of real tool executions (§8.0.4)."""

    def __init__(self, *, max_malformed: int = 3) -> None:
        self.max_malformed = max_malformed
        self.malformed_count = 0

    def observe(self, raw_text: str, extracted_calls: int = 0, native_calls: int = 0) -> tuple[bool, str | None]:
        has_raw_tags = "<tool_call" in (raw_text or "").lower() or "</tool_call>" in (raw_text or "").lower()
        if native_calls > 0 or extracted_calls > 0:
            self.malformed_count = 0
            return False, None
        if has_raw_tags:
            self.malformed_count += 1
            if self.malformed_count >= self.max_malformed:
                return True, "النموذج لم ينفّذ نداءات الأدوات الفعلية وأصدر وسوم <tool_call> نصية متكررة — تم إيقاف التشغيل لحماية الجلسة."
        return False, None
