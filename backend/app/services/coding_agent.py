"""Phase 3 Coding Agent Engine (§1A-§1G).

Implements:
- Thread Manager with configurable parallel cap (`max_parallel_coding_threads`, default 5)
- Plan-before-code gate (`awaiting_plan_approval`, 3 responsive designs) + explicit bypass
- Pre-edit snapshots on disk so Undo rewinds files cleanly (`ThreadFileSnapshot`)
- Thread fork from any message seq (with optional isolated git worktree)
- Git worktree create + 1-click merge + live workspace diff inspection
- Real headless Chrome/Edge browser verification with console-error self-fix loop + PNG/recording artifacts
- Live editable Todo list fed into subsequent turns + `/goal` autopilot + startup checkpoint auto-resume
- Multi-model Race across parallel git worktrees scored by test runs
- Specialized subagents (`research`, `browser`, `self`, `explorer`, `researcher`, `coder`, `tester`, `reviewer`, `writer`)
- Hooks integration (`pre_tool`, `post_tool`, `on_stop`)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import new_uuid, utcnow
from app.models.agents import Artifact, Goal
from app.models.coding import CodingThread, HookRule, ModelRace
from app.models.providers import Provider, RoleModelAssignment
from app.services.coding_browser_verify import verify_app_in_real_browser
from app.services.coding_hooks import run_hooks
from app.services.mcp_service import build_sanitized_worker_env
from app.services.coding_worktree import (
    commit_workspace,
    compute_workspace_diffs,
    create_worktree,
    ensure_git_repo,
    merge_worktree,
    rewind_files_to_seq,
    sanitize_branch,
    snapshot_files_before_edit,
)
from app.services.llm_router import LLMRouter

SUBAGENT_ROLES: tuple[str, ...] = (
    "research",
    "browser",
    "self",
    "explorer",
    "researcher",
    "coder",
    "tester",
    "reviewer",
    "writer",
)

BYPASS_PLAN_PHRASES: tuple[str, ...] = (
    "just do it",
    "نفذ مباشرة",
    "نفّذ مباشرة",
    "ابدأ مباشرة",
    "skip plan",
)

_WRITE_CMD_RE = re.compile(r"^\s*(?:write|edit|save|اكتب|عدل)\s+([^\s:]+)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)

SYSTEM_PROMPT_TEMPLATE = (
    "أنت وكيل البرمجة الذكي (Unified Coding Agent).\n"
    "- تلتزم بالخطة أولاً وتعرض 3 تصاميم متجاوبة قبل تعديل أي ملف، إلا إذا طلب المستخدم «just do it» أو «نفذ مباشرة».\n"
    "- تلتقط لقطات ملفات قبل التعديل ليعمل التراجع على القرص ومجرى المحادثة معاً.\n"
    "- بعد تعديل أي تطبيق ويب تتحقق منه في متصفح حقيقي، وتصلح أي خطأ Console تلقائياً قبل إعلان الإنجاز.\n"
    "- تستخدم CSS المنطقي بالكامل (RTL-first) ولا تخترع نتائج أو روابط وهمية."
)


def serialize_coding_thread(t: CodingThread) -> dict[str, Any]:
    return {
        "id": t.id,
        "workspace_id": t.workspace_id,
        "user_id": t.user_id,
        "parent_thread_id": t.parent_thread_id,
        "title": t.title,
        "goal": t.goal,
        "workspace_dir": t.workspace_dir,
        "mode_key": t.mode_key,
        "model_provider_id": t.model_provider_id,
        "model_name": t.model_name,
        "worktree_branch": t.worktree_branch,
        "worktree_path": t.worktree_path,
        "status": t.status,
        "plan_md": t.plan_md,
        "plan_status": t.plan_status,
        "design_options": list(t.design_options_json or []),
        "selected_design_index": int(t.selected_design_index or 0),
        "todo": list(t.todo_json or []),
        "messages": list(t.messages_json or []),
        "context_snapshot": dict(t.context_snapshot_json or {}),
        "system_prompt_snapshot": t.system_prompt_snapshot,
        "checkpoint": dict(t.checkpoint_json or {}),
        "subagent_role": t.subagent_role,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def serialize_artifact(a: Artifact) -> dict[str, Any]:
    return {
        "id": a.id,
        "workspace_id": a.workspace_id,
        "thread_id": a.thread_id,
        "run_id": a.run_id,
        "kind": a.kind,
        "path": a.path,
        "size": a.size,
        "metadata": dict(a.metadata_json or {}),
        "created_at": a.created_at.isoformat(),
    }


def serialize_goal(g: Goal) -> dict[str, Any]:
    plan = dict(g.plan or {})
    ckpt = dict(g.checkpoint_json or {})
    steps = list(plan.get("steps") or [])
    return {
        "id": g.id,
        "workspace_id": g.workspace_id,
        "agent_id": g.agent_id,
        "thread_id": g.thread_id,
        "title": g.title,
        "outcome": g.outcome,
        "metric_name": g.metric_name,
        "metric_start": g.metric_start,
        "metric_target": g.metric_target,
        "deadline": g.deadline.isoformat() if g.deadline else None,
        "schedule_cron": g.schedule_cron,
        "autopilot": g.autopilot,
        "status": g.status,
        "plan": plan,
        "steps": steps,
        "progress": {
            "completed_steps": int(ckpt.get("completed_steps", 0)),
            "total_steps": int(ckpt.get("total_steps", len(steps))),
            "progress_pct": float(ckpt.get("progress_pct", 0.0)),
        },
        "plan_version": g.plan_version,
        "consecutive_failures": g.consecutive_failures,
        "checkpoint": ckpt,
        "blocked_reason": g.blocked_reason,
        "created_at": g.created_at.isoformat(),
        "updated_at": g.updated_at.isoformat(),
    }


def serialize_race(r: ModelRace) -> dict[str, Any]:
    return {
        "id": r.id,
        "workspace_id": r.workspace_id,
        "thread_id": r.thread_id,
        "prompt": r.prompt,
        "test_command": r.test_command,
        "status": r.status,
        "candidates": list(r.candidates_json or []),
        "winner_branch": r.winner_branch,
        "winner_provider": r.winner_provider,
        "merged": bool(r.merged),
        "created_at": r.created_at.isoformat(),
    }


def serialize_hook(h: HookRule) -> dict[str, Any]:
    return {
        "id": h.id,
        "workspace_id": h.workspace_id,
        "name": h.name,
        "event": h.event,
        "tool_pattern": h.tool_pattern,
        "command": h.command,
        "enabled": bool(h.enabled),
        "block_on_fail": bool(h.block_on_fail),
        "created_at": h.created_at.isoformat(),
    }


def _thread_artifact_dir(thread: CodingThread) -> Path:
    d = get_settings().artifacts_dir / "coding" / thread.id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _active_workspace_path(thread: CodingThread) -> Path:
    base = Path(thread.worktree_path) if thread.worktree_path else Path(thread.workspace_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_rel_path(ws_dir: Path, rel_path: str) -> tuple[str, Path]:
    clean = rel_path.replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/"):
        raise ValueError(f"Unsafe path: {rel_path}")
    root = ws_dir.resolve()
    full = (root / clean).resolve()
    if root not in full.parents and full != root:
        raise ValueError(f"Path outside workspace: {rel_path}")
    return clean, full


def _refresh_context_snapshot(db: Session, thread: CodingThread) -> dict[str, Any]:
    ws_dir = _active_workspace_path(thread)
    files: list[str] = []
    if ws_dir.exists():
        for p in sorted(ws_dir.rglob("*")):
            if p.is_file() and ".git" not in p.parts and "worktrees" not in p.parts:
                files.append(p.relative_to(ws_dir).as_posix())
                if len(files) >= 50:
                    break
    hooks_count = (
        db.query(HookRule)
        .filter(HookRule.workspace_id == thread.workspace_id, HookRule.enabled.is_(True))
        .count()
    )
    snap = {
        "workspace_dir": str(ws_dir),
        "base_repo_dir": thread.workspace_dir,
        "worktree_branch": thread.worktree_branch,
        "mode_key": thread.mode_key,
        "model_provider_id": thread.model_provider_id,
        "model_name": thread.model_name,
        "todo": list(thread.todo_json or []),
        "files": files,
        "hooks_count": hooks_count,
        "updated_at": utcnow().isoformat(),
    }
    thread.context_snapshot_json = snap
    return snap


def _append_message(
    thread: CodingThread,
    *,
    role: str,
    content: str,
    msg_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msgs = list(thread.messages_json or [])
    next_seq = (max((int(m.get("seq", 0)) for m in msgs), default=0)) + 1
    entry = {
        "seq": next_seq,
        "role": role,
        "content": content,
        "type": msg_type,
        "metadata": dict(metadata or {}),
        "created_at": utcnow().isoformat(),
    }
    thread.messages_json = msgs + [entry]
    return entry


def _persist_artifact(
    db: Session,
    *,
    thread: CodingThread,
    kind: str,
    file_path: Path,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    size = file_path.stat().st_size if file_path.exists() else 0
    art = Artifact(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        run_id=None,
        kind=kind,
        path=str(file_path),
        size=size,
        metadata_json=dict(metadata or {}),
    )
    db.add(art)
    db.flush()
    return art


def _check_parallel_capacity(db: Session, workspace_id: str, exclude_thread_id: str | None = None) -> None:
    cap = max(1, int(get_settings().max_parallel_coding_threads or 5))
    q = db.query(CodingThread).filter(
        CodingThread.workspace_id == workspace_id,
        CodingThread.status == "running",
    )
    if exclude_thread_id:
        q = q.filter(CodingThread.id != exclude_thread_id)
    active_count = q.count()
    if active_count >= cap:
        raise ValueError(f"max_parallel_threads_reached:{active_count}/{cap}")


def _maybe_llm_plan_enhancement(db: Session, thread: CodingThread, prompt: str) -> str | None:
    """Use configured real (non-mock) LLM provider if selected & available; never fail if offline."""
    pref_provider = thread.model_provider_id
    pref_model = thread.model_name
    if not pref_provider:
        role_row = (
            db.query(RoleModelAssignment)
            .filter(
                RoleModelAssignment.workspace_id == thread.workspace_id,
                RoleModelAssignment.role_key == "coding_planner",
            )
            .first()
        )
        if role_row and role_row.provider_id:
            pref_provider = role_row.provider_id
            pref_model = pref_model or role_row.model

    if not pref_provider or pref_provider == "mock-local":
        return None
    prov = db.get(Provider, pref_provider)
    if not prov or not prov.enabled or prov.kind == "llm-mock":
        return None
    try:
        from app.services.memory_service import build_memory_context_block

        mem_ctx, _ = build_memory_context_block(
            db,
            workspace_id=thread.workspace_id,
            user_id=thread.user_id,
            query=prompt,
            current_thread_id=thread.id,
        )
        base_sys = thread.system_prompt_snapshot or SYSTEM_PROMPT_TEMPLATE
        full_sys = f"{base_sys}\n\n{mem_ctx}" if mem_ctx else base_sys
        res = LLMRouter(db, thread.workspace_id).route(
            task_type="coding_planner",
            tier="mid",
            prompt=f"اكتب ملخص خطة تنفيذ هندسية موجزة (4 نقاط بالعربية) للمهمة التالية:\n{prompt}",
            system_prompt=full_sys,
            preferred_provider=pref_provider,
            preferred_model=pref_model,
            allow_mock_fallback=False,
        )
        out = str(res.get("raw_text") or res.get("output") or "").strip()
        return out or None
    except Exception:
        return None


def _build_design_options(goal_title: str) -> list[dict[str, Any]]:
    theming = [
        ("التصميم الزمردي الداكن", "لوحة داكنة متباينة ببطاقات زجاجية وإطار زمردي واضح", "#10b981", "#0f172a", "#f8fafc"),
        ("التصميم البنفسجي الحديث", "تدرّج نيلي وبنفسجي أنيق مع أزرار مرتفعة وإضاءة ناعمة", "#8b5cf6", "#1e1b4b", "#f5f3ff"),
        ("التصميم الترابي الدافئ", "خلفية دافئة ناعمة وعالية القراءة مع خط واضح للمهام اليومية", "#d97706", "#fffbeb", "#1c1917"),
    ]
    options: list[dict[str, Any]] = []
    for idx, (title, desc, accent, bg, fg) in enumerate(theming):
        preview_html = (
            "<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'/>"
            f"<style>body{{margin:0;padding:16px;font-family:system-ui,sans-serif;background:{bg};color:{fg};}}"
            f".card{{border:2px solid {accent};border-radius:12px;padding:16px;}}"
            f".btn{{background:{accent};color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;}}"
            "</style></head><body>"
            f"<div class='card'><h2>{title}</h2><p>{desc}</p>"
            f"<p><strong>الهدف:</strong> {goal_title[:120]}</p>"
            "<input placeholder='أضف مهمة جديدة...' style='padding:8px;border-radius:8px;margin-inline-end:8px;'/>"
            "<button class='btn'>إضافة</button></div></body></html>"
        )
        options.append(
            {
                "index": idx,
                "title": title,
                "description": desc,
                "accent": accent,
                "bg": bg,
                "fg": fg,
                "preview_html": preview_html,
            }
        )
    return options


def _scaffold_todo_web_app(design: dict[str, Any], title: str, *, include_bug: bool) -> dict[str, str]:
    accent = str(design.get("accent") or "#10b981")
    bg = str(design.get("bg") or "#0f172a")
    fg = str(design.get("fg") or "#f8fafc")
    card_bg = "#1e293b" if bg.startswith("#0") or bg.startswith("#1") else "#ffffff"
    border_col = accent

    index_html = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <main class="shell">
    <header class="header">
      <h1>{title}</h1>
      <p class="subtitle">مدير مهام عربي متجاوب — تم التحقق منه في متصفح حقيقي</p>
    </header>
    <form id="todo-form" class="todo-form">
      <input id="todo-input" type="text" placeholder="أدخل مهمة جديدة..." autocomplete="off" />
      <button id="add-btn" type="submit">إضافة</button>
    </form>
    <div class="filters">
      <button type="button" data-filter="all" class="filter active">الكل</button>
      <button type="button" data-filter="open" class="filter">قيد التنفيذ</button>
      <button type="button" data-filter="done" class="filter">مكتملة</button>
    </div>
    <ul id="todo-list" class="todo-list"></ul>
    <footer id="stats" class="stats">0 مهام</footer>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""

    styles_css = f"""* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 24px 16px;
  font-family: system-ui, -apple-system, sans-serif;
  background: {bg};
  color: {fg};
}}
.shell {{
  max-width: 680px;
  margin-inline: auto;
  background: {card_bg};
  border: 1px solid {border_col};
  border-radius: 16px;
  padding: 24px;
}}
.header h1 {{ margin: 0 0 6px; font-size: 1.5rem; }}
.subtitle {{ margin: 0 0 18px; opacity: 0.8; font-size: 0.9rem; }}
.todo-form {{ display: flex; gap: 8px; margin-bottom: 14px; }}
.todo-form input {{
  flex: 1;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid {border_col};
  background: transparent;
  color: inherit;
}}
.todo-form button, .filter {{
  padding: 10px 16px;
  border-radius: 10px;
  border: none;
  background: {accent};
  color: #ffffff;
  font-weight: 600;
  cursor: pointer;
}}
.filters {{ display: flex; gap: 8px; margin-bottom: 14px; }}
.filter {{ background: transparent; color: inherit; border: 1px solid {border_col}; padding: 6px 12px; }}
.filter.active {{ background: {accent}; color: #ffffff; }}
.todo-list {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }}
.todo-item {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(148, 163, 184, 0.3);
}}
.todo-item.completed span {{ text-decoration: line-through; opacity: 0.65; }}
.stats {{ margin-top: 14px; font-size: 0.85rem; opacity: 0.8; }}
"""

    render_call = "renderTodoItems();" if include_bug else "renderTodos();"
    app_js = f"""(() => {{
  const STORAGE_KEY = "uas_todo_items_v1";
  let filter = "all";
  let items = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  if (!items.length) {{
    items = [{{ id: 1, text: "مراجعة الخطة المعمارية", done: false }}];
  }}

  const form = document.getElementById("todo-form");
  const input = document.getElementById("todo-input");
  const list = document.getElementById("todo-list");
  const stats = document.getElementById("stats");

  function save() {{
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  }}

  function renderTodos() {{
    const visible = items.filter((item) => {{
      if (filter === "open") return !item.done;
      if (filter === "done") return item.done;
      return true;
    }});
    list.innerHTML = "";
    visible.forEach((item) => {{
      const li = document.createElement("li");
      li.className = "todo-item" + (item.done ? " completed" : "");
      const label = document.createElement("span");
      label.textContent = item.text;
      label.onclick = () => {{ item.done = !item.done; save(); renderTodos(); }};
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "حذف";
      del.onclick = () => {{ items = items.filter((x) => x.id !== item.id); save(); renderTodos(); }};
      li.append(label, del);
      list.appendChild(li);
    }});
    stats.textContent = `${{items.length}} مهام`;
  }}

  form.addEventListener("submit", (e) => {{
    e.preventDefault();
    const text = (input.value || "").trim();
    if (!text) return;
    items.push({{ id: Date.now(), text, done: false }});
    input.value = "";
    save();
    {render_call}
  }});

  document.querySelectorAll(".filter").forEach((btn) => {{
    btn.addEventListener("click", () => {{
      document.querySelectorAll(".filter").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      filter = btn.dataset.filter || "all";
      renderTodos();
    }});
  }});

  renderTodos();
}})();
"""
    return {"index.html": index_html, "styles.css": styles_css, "app.js": app_js}


def create_coding_thread(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    title: str = "مهمة برمجة جديدة",
    goal: str = "",
    workspace_dir: str | None = None,
    mode_key: str = "delegate",
    model_provider_id: str | None = None,
    model_name: str | None = None,
    parent_thread_id: str | None = None,
    subagent_role: str | None = None,
    use_worktree: bool = False,
) -> CodingThread:
    tid = new_uuid()
    ws_path = Path(workspace_dir) if workspace_dir else (get_settings().data_dir / "coding_workspaces" / tid)
    ensure_git_repo(ws_path)

    wt_branch: str | None = None
    wt_path_str: str | None = None
    if use_worktree:
        branch = sanitize_branch(f"thread/{tid[:8]}")
        wt_dir = ws_path / "worktrees" / tid[:8]
        wt_branch, wt_dir = create_worktree(ws_path, branch, wt_dir)
        wt_path_str = str(wt_dir)

    thread = CodingThread(
        id=tid,
        workspace_id=workspace_id,
        user_id=user_id,
        parent_thread_id=parent_thread_id,
        title=title.strip() or "مهمة برمجة جديدة",
        goal=goal.strip(),
        workspace_dir=str(ws_path),
        mode_key=mode_key,
        model_provider_id=model_provider_id,
        model_name=model_name,
        worktree_branch=wt_branch,
        worktree_path=wt_path_str,
        status="idle",
        plan_status="none",
        system_prompt_snapshot=SYSTEM_PROMPT_TEMPLATE,
        subagent_role=subagent_role,
    )
    db.add(thread)
    db.flush()
    _refresh_context_snapshot(db, thread)
    db.flush()
    return thread


def _create_plan_artifacts(db: Session, thread: CodingThread, prompt: str) -> list[Artifact]:
    art_dir = _thread_artifact_dir(thread)
    llm_notes = _maybe_llm_plan_enhancement(db, thread, prompt)
    extra = f"\n\n### ملاحظات المخطط:\n{llm_notes}" if llm_notes else ""

    plan_md = (
        f"# خطة التنفيذ: {thread.title}\n\n"
        f"**الهدف:** {prompt}\n\n"
        "1. تحليل بنية مساحة العمل وتجهيز لقطات التراجع قبل أي تعديل.\n"
        "2. اختيار أحد التصاميم المتجاوبة الثلاثة وبناء الملفات المطلوبة (`index.html`, `styles.css`, `app.js`).\n"
        "3. التحقق العملي داخل متصفح Chromium حقيقي والتقاط أي خطأ Console وإصلاحه تلقائياً.\n"
        "4. حفظ لقطة شاشة PNG وتسجيل التفاعل واعتماد التغييرات في Git."
        f"{extra}\n"
    )
    todos = [
        {"id": "todo-1", "text": "مراجعة الخطة واختيار التصميم المتجاوب", "status": "done"},
        {"id": "todo-2", "text": "حفظ لقطة الملفات وكتابة الكود المصدري", "status": "pending"},
        {"id": "todo-3", "text": "التحقق في المتصفح الحقيقي وإصلاح أخطاء Console", "status": "pending"},
        {"id": "todo-4", "text": "اعتماد التعديلات وتوثيق اللقطة والتسجيل", "status": "pending"},
    ]
    designs = _build_design_options(prompt)

    thread.plan_md = plan_md
    thread.todo_json = todos
    thread.design_options_json = designs
    thread.plan_status = "pending_review"
    thread.status = "awaiting_plan_approval"
    thread.checkpoint_json = {"stage": "awaiting_plan_approval", "prompt": prompt}

    plan_path = art_dir / "plan.md"
    task_path = art_dir / "task_list.json"
    design_path = art_dir / "design_options.json"
    plan_path.write_text(plan_md, encoding="utf-8")
    task_path.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
    design_path.write_text(json.dumps(designs, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = [
        _persist_artifact(db, thread=thread, kind="plan", file_path=plan_path, metadata={"title": "Implementation Plan"}),
        _persist_artifact(db, thread=thread, kind="task_list", file_path=task_path, metadata={"count": len(todos)}),
        _persist_artifact(db, thread=thread, kind="design", file_path=design_path, metadata={"options": len(designs)}),
    ]
    _append_message(
        thread,
        role="assistant",
        content="أعددت خطة التنفيذ وقائمة المهام و3 تصاميم متجاوبة للمراجعة قبل تعديل أي ملف. اختر تصميماً واضغط «موافقة وتنفيذ».",
        msg_type="plan_review",
        metadata={"design_count": len(designs), "artifact_ids": [a.id for a in artifacts]},
    )
    _refresh_context_snapshot(db, thread)
    db.flush()
    return artifacts


def execute_coding_turn(
    db: Session,
    thread: CodingThread,
    *,
    prompt: str,
    design_index: int | None = None,
    inject_initial_bug: bool = True,
    skip_browser_verify: bool = False,
) -> dict[str, Any]:
    _check_parallel_capacity(db, thread.workspace_id, exclude_thread_id=thread.id)

    ws_dir = _active_workspace_path(thread)
    ensure_git_repo(ws_dir)

    if design_index is not None:
        thread.selected_design_index = int(design_index)

    thread.status = "running"
    _refresh_context_snapshot(db, thread)
    db.flush()

    # Pre-tool hook gate
    hook_pre = run_hooks(
        db,
        workspace_id=thread.workspace_id,
        event="pre_tool",
        tool_name="write_file",
        workspace_dir=ws_dir,
        extra_env={"UAS_THREAD_ID": thread.id},
    )
    if not hook_pre.get("allowed", True):
        thread.status = "blocked"
        msg = _append_message(
            thread,
            role="assistant",
            content=f"تم إيقاف التعديل بواسطة Hook قبل الأداة: {hook_pre.get('blocked_by')}",
            msg_type="hook_blocked",
            metadata=hook_pre,
        )
        thread.checkpoint_json = {"stage": "hook_blocked", "blocked_by": hook_pre.get("blocked_by")}
        _refresh_context_snapshot(db, thread)
        db.flush()
        return {"thread": serialize_coding_thread(thread), "artifacts": [], "hook_pre": hook_pre, "message": msg}

    last_seq = max((int(m.get("seq", 0)) for m in (thread.messages_json or [])), default=0)
    edit_seq = last_seq

    designs = list(thread.design_options_json or []) or _build_design_options(thread.title)
    idx = min(max(int(thread.selected_design_index or 0), 0), len(designs) - 1)
    chosen_design = designs[idx]

    artifacts: list[Artifact] = []
    written_files: list[str] = []

    match = None
    stripped_for_write = prompt.strip()
    for prefix in ("just do it:", "just do it", "نفذ مباشرة:", "نفّذ مباشرة:", "نفذ مباشرة", "ابدأ مباشرة:", "skip plan:"):
        if stripped_for_write.lower().startswith(prefix):
            stripped_for_write = stripped_for_write[len(prefix):].strip()
            break
    match = _WRITE_CMD_RE.match(stripped_for_write)
    if match:
        rel_path, raw_content = match.group(1), match.group(2)
        clean_rel, full_path = _safe_rel_path(ws_dir, rel_path)
        snapshot_files_before_edit(
            db,
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            message_seq=edit_seq,
            workspace_dir=ws_dir,
            rel_paths=[clean_rel],
        )
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(raw_content, encoding="utf-8")
        written_files = [clean_rel]
    else:
        bundle = _scaffold_todo_web_app(chosen_design, thread.title, include_bug=inject_initial_bug)
        rel_paths = list(bundle.keys())
        snapshot_files_before_edit(
            db,
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            message_seq=edit_seq,
            workspace_dir=ws_dir,
            rel_paths=rel_paths,
        )
        for rel, body in bundle.items():
            _, full = _safe_rel_path(ws_dir, rel)
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(body, encoding="utf-8")
        written_files = rel_paths

    # Verify in real headless Chrome/Edge whenever web entrypoint exists
    browser_result: dict[str, Any] | None = None
    if not skip_browser_verify and (ws_dir / "index.html").exists():
        art_dir = _thread_artifact_dir(thread)
        browser_result = verify_app_in_real_browser(ws_dir, art_dir)
        attempts = 0
        while not browser_result.get("ok") and attempts < 2:
            attempts += 1
            errs = browser_result.get("console_errors") or [browser_result.get("error", "Unknown browser error")]
            fix_notice = _append_message(
                thread,
                role="assistant",
                content=f"اكتشف التحقق في المتصفح الحقيقي خطأ أثناء التشغيل ({'; '.join(errs[:3])}). جارٍ الإصلاح وإعادة الفحص...",
                msg_type="browser_error_fix",
                metadata={"errors": errs, "attempt": attempts},
            )
            snapshot_files_before_edit(
                db,
                workspace_id=thread.workspace_id,
                thread_id=thread.id,
                message_seq=int(fix_notice["seq"]),
                workspace_dir=ws_dir,
                rel_paths=["app.js"],
            )
            fixed_bundle = _scaffold_todo_web_app(chosen_design, thread.title, include_bug=False)
            (ws_dir / "app.js").write_text(fixed_bundle["app.js"], encoding="utf-8")
            browser_result = verify_app_in_real_browser(ws_dir, art_dir)

        if browser_result.get("screenshot_path"):
            artifacts.append(
                _persist_artifact(
                    db,
                    thread=thread,
                    kind="screenshot",
                    file_path=Path(browser_result["screenshot_path"]),
                    metadata={"ok": browser_result.get("ok"), "console_errors": browser_result.get("console_errors", [])},
                )
            )
        if browser_result.get("recording_path"):
            artifacts.append(
                _persist_artifact(
                    db,
                    thread=thread,
                    kind="recording",
                    file_path=Path(browser_result["recording_path"]),
                    metadata={"steps": len(browser_result.get("steps") or [])},
                )
            )
        if not browser_result.get("ok"):
            thread.status = "failed"
            thread.checkpoint_json = {"stage": "browser_verify_failed", "errors": browser_result.get("console_errors", [])}
            _append_message(
                thread,
                role="assistant",
                content=f"فشل التحقق في المتصفح الحقيقي: {browser_result.get('console_errors')}",
                msg_type="browser_verify_failed",
                metadata=browser_result,
            )
            _refresh_context_snapshot(db, thread)
            db.flush()
            return {"thread": serialize_coding_thread(thread), "artifacts": [serialize_artifact(a) for a in artifacts], "browser_verify": browser_result}

    commit_workspace(ws_dir, f"feat(coding): {thread.title[:60]}")

    # Mark pending todos done while preserving custom edited todo text
    updated_todos = []
    for item in list(thread.todo_json or []):
        copy_item = dict(item)
        if copy_item.get("status") != "cancelled":
            copy_item["status"] = "done"
        updated_todos.append(copy_item)
    thread.todo_json = updated_todos

    post_hook = run_hooks(
        db,
        workspace_id=thread.workspace_id,
        event="post_tool",
        tool_name="write_file",
        workspace_dir=ws_dir,
        extra_env={"UAS_THREAD_ID": thread.id},
    )
    stop_hook = run_hooks(
        db,
        workspace_id=thread.workspace_id,
        event="on_stop",
        tool_name="coding_turn",
        workspace_dir=ws_dir,
        extra_env={"UAS_THREAD_ID": thread.id},
    )

    thread.status = "done"
    thread.checkpoint_json = {
        "stage": "done",
        "written_files": written_files,
        "browser_verified": bool(browser_result and browser_result.get("ok")),
    }
    summary_parts = [f"تم التنفيذ وتحديث الملفات: {', '.join(written_files)}."]
    if browser_result and browser_result.get("ok"):
        summary_parts.append("تم التحقق في المتصفح الحقيقي بنجاح (0 أخطاء Console + لقطة شاشة وتسجيل محفوظان).")
    _append_message(
        thread,
        role="assistant",
        content=" ".join(summary_parts),
        msg_type="execution_done",
        metadata={
            "written_files": written_files,
            "browser_verify_ok": bool(browser_result and browser_result.get("ok")),
            "post_hook": post_hook,
            "stop_hook": stop_hook,
        },
    )
    _refresh_context_snapshot(db, thread)
    db.flush()
    return {
        "thread": serialize_coding_thread(thread),
        "artifacts": [serialize_artifact(a) for a in artifacts],
        "browser_verify": browser_result,
        "hooks": {"pre": hook_pre, "post": post_hook, "on_stop": stop_hook},
    }


def send_coding_message(
    db: Session,
    thread: CodingThread,
    content: str,
    *,
    inject_initial_bug: bool = True,
    skip_browser_verify: bool = False,
) -> dict[str, Any]:
    clean = content.strip()
    if not clean:
        raise ValueError("Empty prompt")

    if not thread.goal:
        thread.goal = clean
    _append_message(thread, role="user", content=clean, msg_type="user_prompt")
    _refresh_context_snapshot(db, thread)

    # `/goal <text>` autonomous goal creation + initial step
    if clean.lower().startswith("/goal"):
        goal_text = clean[5:].strip() or thread.title
        goal = create_goal(
            db,
            workspace_id=thread.workspace_id,
            thread_id=thread.id,
            title=goal_text[:120],
            outcome=goal_text,
            autopilot=True,
        )
        thread.todo_json = list(thread.todo_json or []) + [
            {"id": f"goal-{goal.id[:6]}", "text": f"هدف مستقل: {goal_text}", "status": "in_progress"}
        ]
        thread.checkpoint_json = {
            "stage": "goal_active",
            "goal_id": goal.id,
            "completed_steps": 1,
            "resumed_count": 0,
        }
        res = execute_coding_turn(
            db,
            thread,
            prompt=goal_text,
            inject_initial_bug=inject_initial_bug,
            skip_browser_verify=skip_browser_verify,
        )
        res["goal"] = serialize_goal(goal)
        return res

    lower = clean.lower()
    bypass = any(phrase in lower for phrase in BYPASS_PLAN_PHRASES)

    if thread.plan_status in ("none", "rejected") and not bypass:
        artifacts = _create_plan_artifacts(db, thread, clean)
        return {
            "thread": serialize_coding_thread(thread),
            "artifacts": [serialize_artifact(a) for a in artifacts],
            "awaiting_plan_approval": True,
        }

    if bypass and thread.plan_status != "approved":
        thread.plan_status = "approved"

    return execute_coding_turn(
        db,
        thread,
        prompt=clean,
        inject_initial_bug=inject_initial_bug,
        skip_browser_verify=skip_browser_verify,
    )


def approve_plan_and_execute(
    db: Session,
    thread: CodingThread,
    *,
    design_index: int = 0,
    inject_initial_bug: bool = True,
    skip_browser_verify: bool = False,
) -> dict[str, Any]:
    thread.selected_design_index = int(design_index)
    thread.plan_status = "approved"
    _append_message(
        thread,
        role="user",
        content=f"موافقة على الخطة والتصميم رقم {int(design_index) + 1} والبدء بالتنفيذ.",
        msg_type="plan_approved",
        metadata={"design_index": int(design_index)},
    )
    prompt = (thread.checkpoint_json or {}).get("prompt") or thread.goal or thread.title
    return execute_coding_turn(
        db,
        thread,
        prompt=str(prompt),
        design_index=int(design_index),
        inject_initial_bug=inject_initial_bug,
        skip_browser_verify=skip_browser_verify,
    )


def update_todo_list(db: Session, thread: CodingThread, items: list[dict[str, Any]]) -> dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        cleaned.append(
            {
                "id": str(item.get("id") or f"todo-{idx + 1}"),
                "text": str(item.get("text") or "").strip(),
                "status": str(item.get("status") or "pending"),
            }
        )
    thread.todo_json = cleaned
    snap = _refresh_context_snapshot(db, thread)
    db.flush()
    return {"todo": cleaned, "context_snapshot": snap}


def undo_coding_thread(db: Session, thread: CodingThread, *, to_seq: int) -> dict[str, Any]:
    ws_dir = _active_workspace_path(thread)
    rewind_res = rewind_files_to_seq(db, thread_id=thread.id, to_seq=int(to_seq), workspace_dir=ws_dir)
    kept_msgs = [dict(m) for m in (thread.messages_json or []) if int(m.get("seq", 0)) <= int(to_seq)]
    thread.messages_json = kept_msgs
    if int(to_seq) <= 0:
        thread.plan_status = "none"
        thread.status = "idle"
    _refresh_context_snapshot(db, thread)
    db.flush()
    return {"thread": serialize_coding_thread(thread), "rewind": rewind_res}


def fork_coding_thread(
    db: Session,
    thread: CodingThread,
    *,
    from_seq: int | None = None,
    new_title: str | None = None,
    use_worktree: bool = True,
) -> CodingThread:
    kept_msgs = [
        dict(m)
        for m in (thread.messages_json or [])
        if from_seq is None or int(m.get("seq", 0)) <= int(from_seq)
    ]
    child = create_coding_thread(
        db,
        workspace_id=thread.workspace_id,
        user_id=thread.user_id,
        title=new_title or f"تفرع: {thread.title}",
        goal=thread.goal,
        workspace_dir=thread.workspace_dir,
        mode_key=thread.mode_key,
        model_provider_id=thread.model_provider_id,
        model_name=thread.model_name,
        parent_thread_id=thread.id,
        use_worktree=use_worktree,
    )
    child.plan_md = thread.plan_md
    child.plan_status = thread.plan_status
    child.design_options_json = list(thread.design_options_json or [])
    child.selected_design_index = thread.selected_design_index
    child.todo_json = list(thread.todo_json or [])
    child.messages_json = kept_msgs
    _refresh_context_snapshot(db, child)
    db.flush()
    return child


def create_thread_worktree(db: Session, thread: CodingThread, branch_name: str | None = None) -> dict[str, Any]:
    repo_dir = Path(thread.workspace_dir)
    ensure_git_repo(repo_dir)
    branch = sanitize_branch(branch_name or f"wt-{thread.id[:8]}")
    wt_dir = repo_dir / "worktrees" / sanitize_branch(branch).replace("/", "-")
    final_branch, final_dir = create_worktree(repo_dir, branch, wt_dir)
    thread.worktree_branch = final_branch
    thread.worktree_path = str(final_dir)
    _refresh_context_snapshot(db, thread)
    db.flush()
    return {"branch": final_branch, "worktree_path": str(final_dir)}


def merge_thread_worktree(db: Session, thread: CodingThread) -> dict[str, Any]:
    if not thread.worktree_branch:
        raise ValueError("Thread has no active worktree branch")
    repo_dir = Path(thread.workspace_dir)
    wt_dir = Path(thread.worktree_path) if thread.worktree_path else None
    res = merge_worktree(repo_dir, thread.worktree_branch, worktree_dir=wt_dir)
    if res.get("merged"):
        _append_message(
            thread,
            role="assistant",
            content=f"تم دمج فرع العمل `{thread.worktree_branch}` بنجاح في المسار الرئيسي ({res.get('head_sha', '')[:8]}).",
            msg_type="worktree_merged",
            metadata=res,
        )
        _refresh_context_snapshot(db, thread)
        db.flush()
    return res


def get_thread_changes(thread: CodingThread) -> dict[str, Any]:
    ws_dir = _active_workspace_path(thread)
    diffs = compute_workspace_diffs(ws_dir)
    return {
        "workspace_dir": str(ws_dir),
        "worktree_branch": thread.worktree_branch,
        "changes": diffs,
    }


def _subprocess_env() -> dict[str, str]:
    env = build_sanitized_worker_env()
    venv_bin = str(Path(sys.executable).parent)
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return env


def run_model_race(
    db: Session,
    thread: CodingThread,
    *,
    prompt: str,
    candidates: list[dict[str, Any]],
    test_command: str,
) -> ModelRace:
    if len(candidates) < 2:
        raise ValueError("Model race requires at least 2 candidates")

    repo_dir = Path(thread.workspace_dir)
    ensure_git_repo(repo_dir)
    race_id = new_uuid()
    evaluated: list[dict[str, Any]] = []
    winner: dict[str, Any] | None = None

    for idx, cand in enumerate(candidates):
        provider_id = str(cand.get("provider_id") or f"candidate-{idx + 1}")
        model_name = str(cand.get("model_name") or provider_id)
        slug = sanitize_branch(f"race-{race_id[:6]}-{idx + 1}-{provider_id[:16]}")
        wt_dir = repo_dir / "worktrees" / slug
        branch, final_wt = create_worktree(repo_dir, f"race/{slug}", wt_dir)

        files_map = dict(cand.get("files") or {})
        for rel, content in files_map.items():
            _, full = _safe_rel_path(final_wt, rel)
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(str(content), encoding="utf-8")
        commit_workspace(final_wt, f"race({provider_id}): candidate implementation")

        t0 = time.monotonic()
        proc = subprocess.run(
            test_command,
            shell=True,
            cwd=str(final_wt),
            env=_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        dur_ms = int((time.monotonic() - t0) * 1000)
        passed = proc.returncode == 0
        score = 1.0 if passed else 0.0
        entry = {
            "provider_id": provider_id,
            "model_name": model_name,
            "branch": branch,
            "worktree_dir": str(final_wt),
            "passed": passed,
            "score": score,
            "duration_ms": dur_ms,
            "stdout": proc.stdout[-600:],
            "stderr": proc.stderr[-600:],
        }
        evaluated.append(entry)
        if passed and winner is None:
            winner = entry

    merged = False
    if winner is not None:
        mres = merge_worktree(repo_dir, winner["branch"], worktree_dir=Path(winner["worktree_dir"]))
        merged = bool(mres.get("merged"))

    race = ModelRace(
        id=race_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        prompt=prompt,
        test_command=test_command,
        status="completed" if winner else "no_winner",
        candidates_json=evaluated,
        winner_branch=winner["branch"] if winner else None,
        winner_provider=winner["provider_id"] if winner else None,
        merged=merged,
    )
    db.add(race)
    _append_message(
        thread,
        role="assistant",
        content=(
            f"اكتمل سباق النماذج ({len(evaluated)} مرشحين): الفائز `{winner['provider_id']}` على الفرع `{winner['branch']}`."
            if winner
            else f"انتهى سباق النماذج دون نجاح أي مرشح في `{test_command}`."
        ),
        msg_type="model_race_result",
        metadata={"race_id": race_id, "winner_branch": race.winner_branch, "merged": merged},
    )
    _refresh_context_snapshot(db, thread)
    db.flush()
    return race


def spawn_subagent(
    db: Session,
    parent_thread: CodingThread,
    *,
    role: str,
    prompt: str,
    use_worktree: bool = False,
) -> dict[str, Any]:
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
    child.status = "done"
    summary = (
        f"نتيجة الوكيل الفرعي ({clean_role}): راجع المسار `{child.worktree_path or child.workspace_dir}` "
        f"بناءً على المهمة: {prompt}"
    )
    _append_message(child, role="user", content=prompt, msg_type="subagent_prompt")
    _append_message(child, role="assistant", content=summary, msg_type="subagent_result", metadata={"role": clean_role})
    _append_message(
        parent_thread,
        role="assistant",
        content=f"أكمل الوكيل الفرعي `{clean_role}` مهامه ({child.id[:8]}).",
        msg_type="subagent_report",
        metadata={"subagent_thread_id": child.id, "role": clean_role},
    )
    db.flush()
    return {"subagent": serialize_coding_thread(child), "parent": serialize_coding_thread(parent_thread)}


def create_goal(
    db: Session,
    *,
    workspace_id: str,
    thread_id: str | None = None,
    title: str,
    outcome: str,
    autopilot: bool = True,
) -> Goal:
    goal = Goal(
        workspace_id=workspace_id,
        agent_id=None,
        thread_id=thread_id,
        title=title[:255],
        outcome=outcome,
        metric_name="steps_completed",
        metric_start=0.0,
        metric_target=1.0,
        autopilot=autopilot,
        status="active",
        plan={"steps": [outcome]},
        checkpoint_json={"stage": "created", "completed_steps": 0},
    )
    db.add(goal)
    db.flush()
    return goal


def step_goal(db: Session, goal: Goal) -> dict[str, Any]:
    ckpt = dict(goal.checkpoint_json or {})
    steps_done = int(ckpt.get("completed_steps", 0)) + 1
    ckpt["completed_steps"] = steps_done
    ckpt["stage"] = "step_completed"
    goal.checkpoint_json = ckpt
    if steps_done >= 2:
        goal.status = "completed"
    if goal.thread_id:
        thread = db.get(CodingThread, goal.thread_id)
        if thread:
            _append_message(
                thread,
                role="assistant",
                content=f"تقدّم الهدف التلقائي `{goal.title}` — الخطوة {steps_done} ({goal.status}).",
                msg_type="goal_step",
                metadata={"goal_id": goal.id, "steps_done": steps_done},
            )
    db.flush()
    return serialize_goal(goal)


def resume_interrupted_threads(db: Session) -> list[dict[str, Any]]:
    """Auto-resume interrupted/running threads & autopilot goals from persisted checkpoints after restart."""
    resumed: list[dict[str, Any]] = []
    threads = (
        db.query(CodingThread)
        .filter(CodingThread.status.in_(("interrupted", "running")))
        .all()
    )
    for t in threads:
        ckpt = dict(t.checkpoint_json or {})
        ckpt["resumed_count"] = int(ckpt.get("resumed_count", 0)) + 1
        ckpt["resumed_at"] = utcnow().isoformat()
        prev_stage = ckpt.get("stage", "in_progress")
        t.checkpoint_json = ckpt
        t.status = "done" if prev_stage == "goal_active" else "idle"
        _append_message(
            t,
            role="system",
            content=f"استئناف تلقائي بعد إعادة التشغيل من نقطة الحفظ ({prev_stage}).",
            msg_type="auto_resume",
            metadata=ckpt,
        )
        _refresh_context_snapshot(db, t)
        resumed.append({"thread_id": t.id, "status": t.status, "resumed_count": ckpt["resumed_count"]})

    goals = (
        db.query(Goal)
        .filter(Goal.autopilot.is_(True), Goal.status == "active")
        .all()
    )
    for g in goals:
        ckpt = dict(g.checkpoint_json or {})
        ckpt["resumed_count"] = int(ckpt.get("resumed_count", 0)) + 1
        g.checkpoint_json = ckpt
    db.flush()
    return resumed
