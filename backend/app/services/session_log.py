"""Append-only NDJSON session log (DeepSeek Harness pattern: single source of truth surviving restarts)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import utcnow
from app.security.sanitizer import scrub_value


def get_session_log_path(thread_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", (thread_id or "thread").strip())
    sessions_dir = get_settings().data_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir / f"{safe_id}.ndjson"


def append_turn(thread_id: str, entry: dict[str, Any]) -> Path:
    """Append a sanitized JSON entry as one line in `data/sessions/<thread_id>.ndjson`."""
    path = get_session_log_path(thread_id)
    payload = dict(entry)
    payload.setdefault("ts", utcnow().isoformat())
    clean = scrub_value(payload)
    line = json.dumps(clean, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def read_log(thread_id: str, *, include_rewound: bool = False) -> list[dict[str, Any]]:
    path = get_session_log_path(thread_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not include_rewound and item.get("rewound"):
            continue
        out.append(item)
    return out


def rewind_log(thread_id: str, to_seq: int) -> Path:
    """Mark entries with `seq > to_seq` as `rewound=True` and record a rewind marker."""
    path = get_session_log_path(thread_id)
    if not path.exists():
        return path
    updated: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        seq = item.get("seq")
        if isinstance(seq, int) and seq > to_seq:
            item["rewound"] = True
        updated.append(json.dumps(item, ensure_ascii=False, default=str))

    updated.append(
        json.dumps(
            {"type": "undo", "to_seq": to_seq, "ts": utcnow().isoformat()},
            ensure_ascii=False,
        )
    )
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return path
