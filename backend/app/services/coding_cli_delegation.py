"""Local-CLI delegation for Coding Agent subagents (Codex / Claude CLI in local mode, API fallback otherwise — §7E.2)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.coding import CodingThread
from app.security.sanitizer import scrub_text
from app.services.coding_agent import SUBAGENT_ROLES, _append_message, create_coding_thread, serialize_coding_thread
from app.services.llm_router import LLMRouter
from app.services.mcp_service import build_sanitized_worker_env


def _probe_single_cli(tool_name: str) -> dict[str, Any]:
    """Detect whether local CLI (`codex` or `claude`) is installed and logged in."""
    if get_settings().is_production:
        return {"installed": False, "logged_in": False, "path": None, "reason": "disabled_in_production"}

    exe = shutil.which(tool_name)
    if not exe:
        return {"installed": False, "logged_in": False, "path": None, "reason": "not_in_path"}

    logged_in = False
    if tool_name == "codex":
        auth_file = Path.home() / ".codex" / "auth.json"
        if auth_file.exists() and auth_file.stat().st_size > 10:
            logged_in = True
        elif os.environ.get("OPENAI_API_KEY"):
            logged_in = True
    elif tool_name == "claude":
        claude_cfg = Path.home() / ".claude.json"
        claude_creds = Path.home() / ".claude" / ".credentials.json"
        if (claude_cfg.exists() and claude_cfg.stat().st_size > 10) or (claude_creds.exists() and claude_creds.stat().st_size > 10):
            logged_in = True
        elif os.environ.get("ANTHROPIC_API_KEY"):
            logged_in = True

    if not logged_in:
        # Fallback lightweight version/whoami probe
        try:
            cmd = [exe, "login", "status"] if tool_name == "codex" else [exe, "auth", "status"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=False)
            out = (proc.stdout + proc.stderr).lower()
            if proc.returncode == 0 and any(tok in out for tok in ("logged in", "authenticated", "active", "@")):
                logged_in = True
        except Exception:
            logged_in = False

    return {
        "installed": True,
        "logged_in": logged_in,
        "path": exe,
        "reason": "ready" if logged_in else "not_logged_in",
    }


def detect_local_coding_clis() -> dict[str, Any]:
    """Return local detection state for Codex & Claude CLIs (always disabled in ENV=production)."""
    is_prod = get_settings().is_production
    codex = _probe_single_cli("codex")
    claude = _probe_single_cli("claude")
    available = [name for name, st in (("codex", codex), ("claude", claude)) if st["installed"] and st["logged_in"]]
    return {
        "local_mode": not is_prod,
        "codex": codex,
        "claude": claude,
        "available_clis": available,
        "any_ready": bool(available),
    }


def _run_local_cli_task(tool_name: str, exe_path: str, *, cwd: str, prompt: str) -> str:
    env = build_sanitized_worker_env()
    if tool_name == "codex":
        cmd = [exe_path, "exec", "--skip-git-repo-check", prompt]
    else:
        cmd = [exe_path, "-p", prompt]
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=90, check=False)
    if proc.returncode != 0:
        raise RuntimeError(scrub_text((proc.stderr or proc.stdout or f"exit_{proc.returncode}")[:240]))
    return scrub_text((proc.stdout or "").strip())


def delegate_coding_subagent(
    db: Session,
    parent_thread: CodingThread,
    *,
    role: str,
    prompt: str,
    delegate_cli: bool = False,
    preferred_cli: str = "auto",
    use_worktree: bool = False,
) -> dict[str, Any]:
    """Delegate a coding subagent to local Codex/Claude CLI when opted-in & logged in; fall back to API provider cleanly."""
    clean_role = role.strip().lower()
    if clean_role not in SUBAGENT_ROLES:
        raise ValueError(f"Unsupported subagent role '{role}'. Allowed: {', '.join(SUBAGENT_ROLES)}")

    child = create_coding_thread(
        db,
        workspace_id=parent_thread.workspace_id,
        user_id=parent_thread.user_id,
        title=f"[{clean_role}] {prompt[:60]}",
        goal=prompt,
        workspace_dir=parent_thread.workspace_dir,
        mode_key=parent_thread.mode_key,
        model_provider_id=parent_thread.model_provider_id,
        model_name=parent_thread.model_name,
        parent_thread_id=parent_thread.id,
        subagent_role=clean_role,
        use_worktree=use_worktree,
    )

    cli_status = detect_local_coding_clis() if delegate_cli else {"any_ready": False, "available_clis": []}
    chosen_cli: str | None = None
    if delegate_cli and not get_settings().is_production:
        pref = (preferred_cli or "auto").strip().lower()
        if pref in {"codex", "claude"} and pref in cli_status["available_clis"]:
            chosen_cli = pref
        elif cli_status["available_clis"]:
            chosen_cli = cli_status["available_clis"][0]

    delegation_mode = "api_fallback"
    fallback_reason: str | None = None
    cli_tool: str | None = None
    output_text = ""

    ws_target = child.worktree_path or child.workspace_dir
    if chosen_cli:
        exe = cli_status[chosen_cli]["path"]
        try:
            output_text = _run_local_cli_task(chosen_cli, exe, cwd=ws_target, prompt=prompt)
            delegation_mode = "local_cli"
            cli_tool = chosen_cli
        except Exception as exc:
            fallback_reason = f"cli_exec_failed:{exc}"

    if delegation_mode != "local_cli":
        if delegate_cli and not fallback_reason:
            fallback_reason = "local_cli_not_logged_in_or_disabled"
        try:
            res = LLMRouter(db, parent_thread.workspace_id).route(
                task_type=f"coding_{clean_role}",
                tier="mid",
                toolsets=["terminal", "file"],
                prompt=f"Subagent role={clean_role}. Task:\n{prompt}",
                system_prompt="You are a Coding Subagent. Return a focused engineering response in Arabic.",
                use_cache=False,
            )
            output_text = str(res.get("raw_text") or "")
        except Exception as exc:
            output_text = (
                f"نتيجة الوكيل الفرعي ({clean_role}): مسار `{ws_target}` — المهمة: {prompt} ({type(exc).__name__})"
            )

    child.status = "done"
    summary = (
        f"🖥️ **تنفيذ عبر `{cli_tool}` CLI المحلي ({clean_role}):**\n{output_text}"
        if delegation_mode == "local_cli"
        else f"🤖 **تنفيذ عبر مزود الـ API الاحتياطي ({clean_role}):**\n{output_text}"
    )
    _append_message(child, role="user", content=prompt, msg_type="subagent_prompt")
    _append_message(
        child,
        role="assistant",
        content=summary,
        msg_type="subagent_result",
        metadata={
            "role": clean_role,
            "delegation_mode": delegation_mode,
            "cli_tool": cli_tool,
            "fallback_reason": fallback_reason,
        },
    )
    _append_message(
        parent_thread,
        role="assistant",
        content=f"أكمل الوكيل الفرعي `{clean_role}` ({delegation_mode}{':' + cli_tool if cli_tool else ''}).",
        msg_type="subagent_report",
        metadata={
            "subagent_thread_id": child.id,
            "role": clean_role,
            "delegation_mode": delegation_mode,
            "cli_tool": cli_tool,
            "fallback_reason": fallback_reason,
        },
    )
    db.flush()
    return {
        "delegation_mode": delegation_mode,
        "cli_tool": cli_tool,
        "fallback_reason": fallback_reason,
        "subagent": serialize_coding_thread(child),
        "parent": serialize_coding_thread(parent_thread),
    }
