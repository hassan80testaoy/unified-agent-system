"""Hermes Routing Profiles (chat/coding/planning/research) & Configure-Model-Routing Wizard with Config Backup/Rollback (§7C)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
import yaml

from app.config import get_env_secret_by_ref, get_settings
from app.db import new_uuid
from app.models.core import SystemState
from app.models.phase7 import RoutingConfigBackup, RoutingProfile
from app.models.providers import ApiKey, Provider, ProviderAccount, ProviderModel
from app.security.sanitizer import scrub_text
from app.services.model_catalogue import sync_provider_catalogue
from app.services.runtime_checks import is_local_endpoint

ROUTING_CONTEXT_ORDER = ("chat", "coding", "planning", "research")

_DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "chat": {
        "tier": "cheap",
        "model": "auto",
        "rules": {
            "default": True,
            "description_ar": "المحادثة اليومية السريعة والردود العامة",
            "description_en": "Fast general daily chat",
        },
    },
    "coding": {
        "tier": "mid",
        "model": "auto",
        "rules": {
            "if_toolsets_any": ["terminal", "file"],
            "description_ar": "مهام البرمجة وتعديل الملفات والأوامر الطرفية",
            "description_en": "Coding tasks using terminal and file toolsets",
        },
    },
    "planning": {
        "tier": "strong",
        "model": "auto",
        "rules": {
            "if_goal_matches": ["plan", "spec", "roadmap", "خطة", "هدف", "مواصفات"],
            "description_ar": "التخطيط وصياغة الأهداف والمواصفات والتحكيم",
            "description_en": "Planning, specs, roadmaps, and judging",
        },
    },
    "research": {
        "tier": "mid",
        "model": "auto",
        "rules": {
            "if_toolsets_any": ["web", "browser"],
            "description_ar": "البحث في الويب والتصفح الحي وفحص المواقع",
            "description_en": "Web research, live browser, and site auditing",
        },
    },
}


def serialize_routing_profile(row: RoutingProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "context_key": row.context_key,
        "provider_id": row.provider_id,
        "model": row.model,
        "base_url": row.base_url,
        "api_key_env": row.api_key_env,
        "tier": row.tier,
        "rules": row.rules_json or {},
        "enabled": bool(row.enabled),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_default_routing_profiles(db: Session, workspace_id: str) -> list[RoutingProfile]:
    """Seed the 4 Hermes routing profiles idempotently for a workspace."""
    existing = {
        row.context_key: row
        for row in db.query(RoutingProfile).filter(RoutingProfile.workspace_id == workspace_id).all()
    }
    created_any = False
    for ctx_key in ROUTING_CONTEXT_ORDER:
        if ctx_key not in existing:
            spec = _DEFAULT_PROFILES[ctx_key]
            row = RoutingProfile(
                workspace_id=workspace_id,
                context_key=ctx_key,
                provider_id=None,
                model=spec["model"],
                base_url="",
                api_key_env="",
                tier=spec["tier"],
                rules_json=dict(spec["rules"]),
                enabled=True,
            )
            db.add(row)
            existing[ctx_key] = row
            created_any = True
    if created_any:
        db.flush()
    return [existing[k] for k in ROUTING_CONTEXT_ORDER]


def resolve_routing_profile(
    db: Session,
    workspace_id: str,
    *,
    task_type: str,
    toolsets: list[str] | None = None,
    goal_text: str | None = None,
    profile_override: str | None = None,
) -> RoutingProfile | None:
    """Resolve the active Hermes RoutingProfile matching explicit override, toolsets, goal text, or task_type."""
    ensure_default_routing_profiles(db, workspace_id)
    rows = {
        r.context_key: r
        for r in db.query(RoutingProfile)
        .filter(RoutingProfile.workspace_id == workspace_id, RoutingProfile.enabled.is_(True))
        .all()
    }
    if not rows:
        return None

    if profile_override and profile_override in rows:
        return rows[profile_override]

    tset = {str(t).strip().lower() for t in (toolsets or []) if str(t).strip()}
    if tset:
        # Respect configured profile `if_toolsets_any` rules cleanly
        for ctx_key in ("coding", "research", "planning", "chat"):
            prof = rows.get(ctx_key)
            if not prof:
                continue
            rule_tools = {str(x).strip().lower() for x in (prof.rules_json or {}).get("if_toolsets_any", [])}
            if rule_tools and (tset & rule_tools):
                return prof

    low_goal = (goal_text or "").strip().lower()
    if low_goal:
        for ctx_key in ("planning", "coding", "research", "chat"):
            prof = rows.get(ctx_key)
            if not prof:
                continue
            goal_kws = [str(x).strip().lower() for x in (prof.rules_json or {}).get("if_goal_matches", []) if x]
            if goal_kws and any(kw in low_goal for kw in goal_kws):
                return prof

    low_task = (task_type or "").strip().lower()
    if low_task.startswith("coding") or low_task == "code_review":
        return rows.get("coding") or rows.get("chat")
    if low_task in {"browser", "research", "site_audit", "gsc_report"}:
        return rows.get("research") or rows.get("chat")
    if low_task in {"plan", "planning", "goal_planner", "orchestrate", "judge"}:
        return rows.get("planning") or rows.get("chat")
    return rows.get("chat")


def _export_yaml_snapshot(snapshot: dict[str, Any], rel_name: str) -> str:
    backup_dir = get_settings().data_dir / "config_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = (backup_dir / rel_name).resolve()
    target.write_text(yaml.safe_dump(snapshot, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(target)


def backup_routing_config(
    db: Session,
    workspace_id: str,
    *,
    reason: str = "manual_backup",
    user_id: str | None = None,
) -> RoutingConfigBackup:
    """Persist an atomic routing configuration snapshot before any edit or wizard write."""
    profiles = ensure_default_routing_profiles(db, workspace_id)
    pb_row = db.get(SystemState, f"planner_brain:{workspace_id}")
    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "reason": scrub_text(reason)[:160],
        "profiles": [serialize_routing_profile(p) for p in profiles],
        "planner_brain": dict(pb_row.value_json or {}) if pb_row else {},
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    file_path = _export_yaml_snapshot(snapshot, f"routing_{workspace_id[:8]}_{ts}_{new_uuid()[:6]}.yaml")
    rec = RoutingConfigBackup(
        workspace_id=workspace_id,
        reason=scrub_text(reason)[:160],
        snapshot_json=snapshot,
        file_path=file_path,
        created_by=user_id,
    )
    db.add(rec)
    db.flush()
    return rec


def restore_routing_backup(
    db: Session,
    workspace_id: str,
    backup_id: str,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Restore saved routing profiles from a RoutingConfigBackup snapshot (creating a pre-restore backup first)."""
    backup = db.get(RoutingConfigBackup, backup_id)
    if not backup or backup.workspace_id != workspace_id:
        raise ValueError("Routing backup not found")

    safety_bk = backup_routing_config(db, workspace_id, reason=f"pre_restore:{backup_id[:8]}", user_id=user_id)
    snap = dict(backup.snapshot_json or {})
    ensure_default_routing_profiles(db, workspace_id)
    existing = {
        row.context_key: row
        for row in db.query(RoutingProfile).filter(RoutingProfile.workspace_id == workspace_id).all()
    }
    for item in snap.get("profiles") or []:
        ctx_key = str(item.get("context_key") or "").strip()
        if ctx_key not in existing:
            continue
        row = existing[ctx_key]
        row.provider_id = item.get("provider_id")
        row.model = str(item.get("model") or "auto")
        row.base_url = str(item.get("base_url") or "")
        row.api_key_env = str(item.get("api_key_env") or "")
        row.tier = str(item.get("tier") or "mid")
        row.rules_json = dict(item.get("rules") or {})
        row.enabled = bool(item.get("enabled", True))
    if isinstance(snap.get("planner_brain"), dict) and snap["planner_brain"]:
        pb_key = f"planner_brain:{workspace_id}"
        pb_row = db.get(SystemState, pb_key)
        if pb_row is None:
            db.add(SystemState(key=pb_key, value_json=dict(snap["planner_brain"])))
        else:
            pb_row.value_json = dict(snap["planner_brain"])
    db.flush()
    return {
        "restored_from": backup.id,
        "safety_backup_id": safety_bk.id,
        "profiles": [serialize_routing_profile(existing[k]) for k in ROUTING_CONTEXT_ORDER if k in existing],
    }


def _discover_credentialed_providers(db: Session, workspace_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    providers = (
        db.query(Provider)
        .filter(Provider.workspace_id == workspace_id, Provider.enabled.is_(True))
        .all()
    )
    key_pids = {
        k.provider
        for k in db.query(ApiKey).filter(ApiKey.workspace_id == workspace_id).all()
    }
    acct_pids = {
        a.provider_id
        for a in db.query(ProviderAccount)
        .filter(ProviderAccount.workspace_id == workspace_id, ProviderAccount.status == "active", ProviderAccount.key_ref_enc.isnot(None))
        .all()
    }

    discovered: list[dict[str, Any]] = []
    for p in providers:
        if settings.is_production and (p.kind == "llm-local" or is_local_endpoint(p.api_base)):
            continue
        env_var = ""
        if (p.auth_ref or "").startswith("env:"):
            env_var = p.auth_ref.split(":", 1)[1].strip()
        has_env = bool(get_env_secret_by_ref(p.auth_ref))
        has_db_key = p.id in key_pids or p.id in acct_pids
        is_local = p.kind == "llm-local" or (not settings.is_production and is_local_endpoint(p.api_base))
        has_cred = has_env or has_db_key or is_local
        discovered.append(
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "api_base": p.api_base,
                "api_key_env": env_var,
                "has_credentials": bool(has_cred and p.kind != "llm-mock"),
                "models_json": dict(p.models_json or {}),
                "provider_obj": p,
            }
        )
    return discovered


def configure_model_routing_wizard(
    db: Session,
    workspace_id: str,
    *,
    user_id: str | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    fetch_live_models: bool = True,
) -> dict[str, Any]:
    """Wizard: discover working providers, refresh live model lists, backup current config, and update profiles."""
    profiles = {p.context_key: p for p in ensure_default_routing_profiles(db, workspace_id)}
    backup = backup_routing_config(db, workspace_id, reason="configure-model-routing wizard", user_id=user_id)

    discovered = _discover_credentialed_providers(db, workspace_id)
    working = [d for d in discovered if d["has_credentials"]]

    fetched_models: dict[str, list[str]] = {}
    for d in working:
        prov = d["provider_obj"]
        if fetch_live_models:
            try:
                sync_provider_catalogue(db, workspace_id, prov)
            except Exception:
                pass
        rows = (
            db.query(ProviderModel)
            .filter(ProviderModel.workspace_id == workspace_id, ProviderModel.provider_id == prov.id)
            .limit(40)
            .all()
        )
        mids = [r.model_id for r in rows] or list(
            dict.fromkeys([m for m in (d["models_json"].values()) if m])
        )
        fetched_models[prov.id] = mids

    primary = next((d for d in working if d["id"] != "llm-mock"), None)
    secondary = next((d for d in working if d["id"] not in {"llm-mock", primary["id"] if primary else ""}), primary)

    ov = dict(overrides or {})
    updated: list[dict[str, Any]] = []
    for ctx_key in ROUTING_CONTEXT_ORDER:
        prof = profiles[ctx_key]
        ctx_ov = ov.get(ctx_key) or {}
        if ctx_ov:
            if "provider_id" in ctx_ov:
                prof.provider_id = ctx_ov["provider_id"] or None
            if "model" in ctx_ov and ctx_ov["model"]:
                prof.model = str(ctx_ov["model"]).strip()
            if "base_url" in ctx_ov:
                prof.base_url = str(ctx_ov["base_url"] or "").strip()
            if "api_key_env" in ctx_ov:
                prof.api_key_env = str(ctx_ov["api_key_env"] or "").strip()
            if "tier" in ctx_ov and ctx_ov["tier"]:
                prof.tier = str(ctx_ov["tier"]).strip()
            if "enabled" in ctx_ov:
                prof.enabled = bool(ctx_ov["enabled"])
        elif working:
            chosen = secondary if (ctx_key == "research" and secondary) else primary
            assert chosen is not None
            tier = _DEFAULT_PROFILES[ctx_key]["tier"]
            tmap = chosen["models_json"]
            cat = fetched_models.get(chosen["id"]) or []
            chosen_model = tmap.get(tier) or tmap.get("mid") or tmap.get("cheap") or (cat[0] if cat else "auto")
            prof.provider_id = chosen["id"]
            prof.model = str(chosen_model or "auto")
            prof.base_url = chosen["api_base"]
            prof.api_key_env = chosen["api_key_env"]
            prof.tier = tier
        updated.append(serialize_routing_profile(prof))

    # Save active exported snapshot alongside data backups (no plaintext secrets)
    active_yaml = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "backup_id": backup.id,
        "profiles": updated,
    }
    _export_yaml_snapshot(active_yaml, f"active_routing_{workspace_id[:8]}.yaml")
    db.flush()

    return {
        "backup_id": backup.id,
        "backup_file": backup.file_path,
        "discovered_providers": [
            {
                "id": d["id"],
                "name": d["name"],
                "has_credentials": d["has_credentials"],
                "api_base": d["api_base"],
                "api_key_env": d["api_key_env"],
                "available_models": fetched_models.get(d["id"], []),
            }
            for d in discovered
            if d["kind"] != "llm-mock"
        ],
        "profiles": updated,
    }
