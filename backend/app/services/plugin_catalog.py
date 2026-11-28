"""Commit-Pinned Plugin Catalog with One-Click Install (Hermes plugin-catalog pattern §8H.8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.phase8 import PluginCatalogInstall

BUILTIN_PLUGIN_CATALOG: list[dict[str, Any]] = [
    {
        "slug": "hermes-honcho-memory",
        "name": "Honcho Dialectic User Modeling",
        "category": "memory",
        "repo_url": "https://github.com/NousResearch/hermes-agent",
        "pinned_commit": "d71a65744c569c1cd01a713692542a8d163e106b",
        "description_ar": "بناء نموذج تدريجي لعادات وتفضيلات المالك داخل USER.md عبر الجلسات.",
        "description_en": "Builds evolving owner preferences & habits inside USER.md across sessions.",
    },
    {
        "slug": "dsh-timeline-panel",
        "name": "DeepSeek Harness Timeline View",
        "category": "ui",
        "repo_url": "https://github.com/deepseek-ai/deepseek-harness",
        "pinned_commit": "4b399c39a6d1479cd84ca591b4d7c88e24d38a96",
        "description_ar": "خط زمني بصري لتتبّع مراحل التشغيل وأدوات الدردشة لحظياً.",
        "description_en": "Visual run & chat execution timeline drawer.",
    },
    {
        "slug": "afh-loop-guard",
        "name": "AFH Anti-Loop Execution Guard",
        "category": "reliability",
        "repo_url": "https://github.com/Lucifer-xD/agent-flow-harness",
        "pinned_commit": "dcfa95e4b89492084e27e46cc65299d5fa49805f",
        "description_ar": "حارس كشف التكرار اللانهائي وإيقاف الدوران المبكر مع إعادة التعيين عند التعديل.",
        "description_en": "Detects repeated tool calls / identical responses and halts runaway loops.",
    },
    {
        "slug": "openmausbot-replay-scrubber",
        "name": "OpenMausBot Secret-Safe Replay Scrubber",
        "category": "security",
        "repo_url": "https://github.com/milind-soni/OpenMausBot",
        "pinned_commit": "e1cc6a290d024203be40f41928f5cf1ee3ba83b6",
        "description_ar": "تنظيف القيم الحساسة تلقائياً في إعادة تشغيل الخطوات والتسجيلات.",
        "description_en": "Strips raw typed secret values across saved-step replay and logs.",
    },
]


def _plugins_dir() -> Path:
    d = get_settings().data_dir / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def serialize_plugin_install(row: PluginCatalogInstall) -> dict[str, Any]:
    return {
        "id": row.id,
        "slug": row.plugin_slug,
        "name": row.name,
        "repo_url": row.repo_url,
        "pinned_commit": row.pinned_commit,
        "category": row.category,
        "installed_path": row.installed_path,
        "enabled": bool(row.enabled),
        "manifest": row.manifest_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_plugin_catalog(db: Session, workspace_id: str) -> dict[str, Any]:
    installed = {
        r.plugin_slug: r
        for r in db.query(PluginCatalogInstall).filter(PluginCatalogInstall.workspace_id == workspace_id).all()
    }
    catalog = []
    for item in BUILTIN_PLUGIN_CATALOG:
        inst = installed.get(item["slug"])
        catalog.append(
            {
                **item,
                "installed": inst is not None,
                "enabled": bool(inst.enabled) if inst else False,
                "install_record": serialize_plugin_install(inst) if inst else None,
            }
        )
    return {"catalog": catalog, "installed": [serialize_plugin_install(v) for v in installed.values()]}


def install_pinned_plugin(db: Session, *, workspace_id: str, slug: str) -> dict[str, Any]:
    entry = next((x for x in BUILTIN_PLUGIN_CATALOG if x["slug"] == slug), None)
    if entry is None:
        raise ValueError(f"Plugin '{slug}' not found in pinned catalog.")

    pdir = _plugins_dir() / slug
    pdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "slug": entry["slug"],
        "name": entry["name"],
        "repo_url": entry["repo_url"],
        "pinned_commit": entry["pinned_commit"],
        "category": entry["category"],
    }
    (pdir / "plugin.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    row = (
        db.query(PluginCatalogInstall)
        .filter(PluginCatalogInstall.workspace_id == workspace_id, PluginCatalogInstall.plugin_slug == slug)
        .first()
    )
    if row is None:
        row = PluginCatalogInstall(
            workspace_id=workspace_id,
            plugin_slug=entry["slug"],
            name=entry["name"],
            repo_url=entry["repo_url"],
            pinned_commit=entry["pinned_commit"],
            category=entry["category"],
            installed_path=str(pdir),
            enabled=True,
            manifest_json=manifest,
        )
        db.add(row)
    else:
        row.pinned_commit = entry["pinned_commit"]
        row.enabled = True
        row.installed_path = str(pdir)
        row.manifest_json = manifest
    db.flush()
    return serialize_plugin_install(row)
