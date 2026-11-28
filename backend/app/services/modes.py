"""Agent Mode Presets & Per-Project Mode Defaults (§1G — AFH + DeepSeek Harness + Zadloop)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.providers import Provider
from app.models.v3_harness import ModePreset, ProjectMode

BUILTIN_MODES: list[dict[str, Any]] = [
    {
        "key": "delegate",
        "name_ar": "التفويض (الافتراضي)",
        "name_en": "Delegate",
        "description_ar": "الوكيل القائد يوزّع المهام على الوكلاء الفرعيين ويراجع النتائج دون تعديل مباشر إلا عند الضرورة.",
        "description_en": "Lead orchestrator delegates work to subagents/lanes and reviews results.",
        "system_prompt": (
            "You are in Delegate mode (default). Route work to specialised agents/lanes, "
            "monitor progress, enforce reviewer separation, and avoid direct destructive edits."
        ),
        "tool_allowlist": ["delegate", "review", "read", "search", "chat", "intent_route"],
        "sandbox_mode": "workspace-write",
        "approval_policy": "ask_first",
        "is_default": True,
    },
    {
        "key": "standard",
        "name_ar": "الوضع القياسي",
        "name_en": "Standard",
        "description_ar": "وكيل كامل الصلاحيات للتعديل والبحث والتشغيل والمهارات وفق سياسة الموافقة.",
        "description_en": "Full-featured mode: files, terminal, search, browser, skills, and subagents.",
        "system_prompt": "You are in Standard mode. Execute tasks carefully, respect approval gates, and verify real outputs.",
        "tool_allowlist": ["read", "write", "edit", "terminal", "search", "skills", "browser", "chat"],
        "sandbox_mode": "workspace-write",
        "approval_policy": "ask_first",
        "is_default": False,
    },
    {
        "key": "minimal",
        "name_ar": "الوضع المختصر",
        "name_en": "Minimal",
        "description_ar": "وضع خفيف منخفض التكلفة للمهام السريعة والأسئلة المباشرة.",
        "description_en": "Lightweight terminal + targeted editor preset for quick, low-cost tasks.",
        "system_prompt": "You are in Minimal mode. Prefer concise steps, shell inspection, and targeted edits.",
        "tool_allowlist": ["terminal", "str_replace_editor", "chat"],
        "sandbox_mode": "workspace-write",
        "approval_policy": "ask_first",
        "is_default": False,
    },
    {
        "key": "build",
        "name_ar": "البناء والتنفيذ",
        "name_en": "Build",
        "description_ar": "يركّز على التنفيذ الفعلي وكتابة الكود أو المهام والتحقق من النتائج.",
        "description_en": "Implementation mode: executes edits, commands, and verifies outputs.",
        "system_prompt": "You are in Build mode. Implement the approved plan completely and verify every change.",
        "tool_allowlist": ["read", "write", "edit", "terminal", "search", "browser", "skills"],
        "sandbox_mode": "workspace-write",
        "approval_policy": "ask_first",
        "is_default": False,
    },
    {
        "key": "plan",
        "name_ar": "التخطيط (قراءة فقط)",
        "name_en": "Plan",
        "description_ar": "يستكشف ويضع خطة مفصلة بدون تعديل أي ملفات أو بيانات.",
        "description_en": "Read-only exploration and structured planning without mutating files or state.",
        "system_prompt": "You are in Plan mode. Explore using non-mutating reads and produce a clear step-by-step plan.",
        "tool_allowlist": ["read", "search", "web_search", "chat"],
        "sandbox_mode": "read-only",
        "approval_policy": "ask_first",
        "is_default": False,
    },
    {
        "key": "red-team",
        "name_ar": "الفحص الأمني",
        "name_en": "Red Team",
        "description_ar": "يفحص الثغرات والتسريبات وإعدادات الأمان داخل النطاق المصرح به دون تعديل الشفرة.",
        "description_en": "Security audit mode within authorized scope; probes and reports findings without edits.",
        "system_prompt": "You are in Red Team mode. Audit security controls, permissions, and secret hygiene without editing source files.",
        "tool_allowlist": ["read", "search", "probe", "verify"],
        "sandbox_mode": "read-only",
        "approval_policy": "ask_first",
        "is_default": False,
    },
]


def seed_default_modes(db: Session, workspace_id: str) -> list[ModePreset]:
    """Ensure all 6 built-in modes exist for a workspace cleanly."""
    existing = db.query(ModePreset).filter(ModePreset.workspace_id == workspace_id).all()
    by_key = {m.key: m for m in existing}
    has_default = any(m.is_default for m in existing)

    for item in BUILTIN_MODES:
        key = item["key"]
        if key not in by_key:
            row = ModePreset(
                workspace_id=workspace_id,
                key=key,
                name_ar=item["name_ar"],
                name_en=item["name_en"],
                description_ar=item["description_ar"],
                description_en=item["description_en"],
                system_prompt=item["system_prompt"],
                tool_allowlist=list(item["tool_allowlist"]),
                sandbox_mode=item["sandbox_mode"],
                approval_policy=item["approval_policy"],
                is_default=(item["is_default"] and not has_default),
                is_builtin=True,
            )
            db.add(row)
            by_key[key] = row

    db.flush()
    return sorted(by_key.values(), key=lambda m: (not m.is_default, not m.is_builtin, m.key))


def get_effective_project_mode(db: Session, workspace_id: str, project_key: str = "default") -> dict[str, Any]:
    """Resolve active mode/model/lanes for a project, falling back gracefully with a notice if provider is unavailable."""
    modes = seed_default_modes(db, workspace_id)
    default_mode = next((m for m in modes if m.is_default), None)
    default_key = default_mode.key if default_mode else "delegate"

    pm = (
        db.query(ProjectMode)
        .filter(ProjectMode.workspace_id == workspace_id, ProjectMode.project_key == project_key)
        .first()
    )
    if not pm:
        return {
            "project_key": project_key,
            "mode_key": default_key,
            "model_provider_id": None,
            "model_name": None,
            "delegation_lanes": {},
            "fallback_notice": None,
        }

    fallback_notice: str | None = None
    active_provider = pm.model_provider_id
    if active_provider:
        prov = db.get(Provider, active_provider)
        if not prov or prov.workspace_id != workspace_id or not prov.enabled:
            fallback_notice = (
                f"المزود المحدد للمشروع ({active_provider}) غير جاهز حالياً — تم استخدام المزود الافتراضي للمنصة مؤقتاً."
            )
            active_provider = None

    return {
        "project_key": project_key,
        "mode_key": pm.mode_key or default_key,
        "model_provider_id": active_provider,
        "configured_provider_id": pm.model_provider_id,
        "model_name": pm.model_name if active_provider else None,
        "delegation_lanes": pm.delegation_lanes_json or {},
        "fallback_notice": fallback_notice,
    }
