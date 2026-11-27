"""Dynamic Plugin Agent Registry — discovers agents/<name>/manifest.json + agent.py without code changes in UI."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.agents import AgentModel
from app.models.providers import AgentProviderAssignment
from app.services.llm_router import seed_default_providers

AGENTS_DIR = Path(__file__).resolve().parent
_LOADED_CLASSES: dict[str, type[BaseAgent]] = {}
_MANIFESTS: dict[str, dict[str, Any]] = {}


def discover_agents(db: Session | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    """Scan backend/app/agents/*/manifest.json and register each agent plugin."""
    discovered: list[dict[str, Any]] = []
    for child in sorted(AGENTS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        manifest_path = child / "manifest.json"
        agent_py = child / "agent.py"
        if not manifest_path.exists() or not agent_py.exists():
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        key = manifest["key"]
        mod_name = f"app.agents.{child.name}.agent"
        mod = importlib.import_module(mod_name)
        cls_name = manifest.get("class_name", "Agent")
        agent_cls = getattr(mod, cls_name)

        _LOADED_CLASSES[key] = agent_cls
        _MANIFESTS[key] = manifest
        discovered.append(manifest)

        if db is not None and workspace_id is not None:
            seed_default_providers(db, workspace_id)
            row = db.query(AgentModel).filter(AgentModel.key == key).first()
            if row is None:
                row = AgentModel(
                    workspace_id=workspace_id,
                    key=key,
                    name_ar=manifest["name_ar"],
                    name_en=manifest["name_en"],
                    version=manifest.get("version", "1.0.0"),
                    description_ar=manifest.get("description_ar", ""),
                    description_en=manifest.get("description_en", ""),
                    input_schema=manifest.get("input_schema", {}),
                    output_schema=manifest.get("output_schema", {}),
                    tools_allowed=manifest.get("tools_allowed", []),
                    daily_limit=int(manifest.get("daily_limit", 20)),
                    daily_budget_usd=float(manifest.get("daily_budget_usd", 1.0)),
                    needs_approval=bool(manifest.get("needs_approval", False)),
                    enabled=bool(manifest.get("enabled", True)),
                    category=manifest.get("category", "seo"),
                    priority=manifest.get("priority", "P0"),
                )
                db.add(row)
                db.flush()
            else:
                row.name_ar = manifest["name_ar"]
                row.name_en = manifest["name_en"]
                row.version = manifest.get("version", row.version)
                row.description_ar = manifest.get("description_ar", row.description_ar)
                row.description_en = manifest.get("description_en", row.description_en)
                row.input_schema = manifest.get("input_schema", row.input_schema)
                row.output_schema = manifest.get("output_schema", row.output_schema)
                row.tools_allowed = manifest.get("tools_allowed", row.tools_allowed)

            # Seed default per-agent delegation assignment if none exists yet
            default_del = manifest.get("default_delegation")
            if default_del:
                existing_assign = (
                    db.query(AgentProviderAssignment)
                    .filter(
                        AgentProviderAssignment.workspace_id == workspace_id,
                        AgentProviderAssignment.agent_id == row.id,
                    )
                    .first()
                )
                if existing_assign is None:
                    db.add(
                        AgentProviderAssignment(
                            workspace_id=workspace_id,
                            agent_id=row.id,
                            orchestrator_json=default_del.get("orchestrator", {}),
                            workers_json=default_del.get("workers", {}),
                            reviewer_json=default_del.get("reviewer", {}),
                        )
                    )
            db.flush()

    if db is not None:
        db.commit()
    return discovered


def get_agent_instance(key: str) -> BaseAgent:
    if key not in _LOADED_CLASSES:
        discover_agents()
    if key not in _LOADED_CLASSES:
        raise KeyError(f"Agent '{key}' is not registered.")
    return _LOADED_CLASSES[key]()


def get_agent_manifest(key: str) -> dict[str, Any] | None:
    if key not in _MANIFESTS:
        discover_agents()
    return _MANIFESTS.get(key)
