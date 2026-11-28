"""User-defined pre_tool / post_tool / on_stop hooks for the Coding Agent (§1G)."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.coding import HookRule


def run_hooks(
    db: Session,
    *,
    workspace_id: str,
    event: str,
    tool_name: str,
    workspace_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run all enabled HookRules matching `(event, tool_name)` in `workspace_dir`."""
    rules = (
        db.query(HookRule)
        .filter(HookRule.workspace_id == workspace_id, HookRule.event == event, HookRule.enabled.is_(True))
        .all()
    )
    results: list[dict[str, Any]] = []
    for rule in rules:
        pattern = (rule.tool_pattern or "*").strip()
        if not fnmatch.fnmatch(tool_name, pattern):
            continue
        env = os.environ.copy()
        env["UAS_HOOK_EVENT"] = event
        env["UAS_HOOK_TOOL"] = tool_name
        env["UAS_WORKSPACE_DIR"] = str(workspace_dir)
        if extra_env:
            for k, v in extra_env.items():
                env[k] = v
        try:
            proc = subprocess.run(
                rule.command,
                shell=True,
                cwd=str(workspace_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            ok = proc.returncode == 0
            results.append(
                {
                    "hook_id": rule.id,
                    "name": rule.name,
                    "event": event,
                    "ok": ok,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout[-500:],
                    "stderr": proc.stderr[-500:],
                }
            )
            if not ok and rule.block_on_fail:
                return {"allowed": False, "blocked_by": rule.name, "results": results}
        except Exception as exc:
            results.append({"hook_id": rule.id, "name": rule.name, "event": event, "ok": False, "error": type(exc).__name__})
            if rule.block_on_fail:
                return {"allowed": False, "blocked_by": rule.name, "results": results}

    return {"allowed": True, "blocked_by": None, "results": results}
