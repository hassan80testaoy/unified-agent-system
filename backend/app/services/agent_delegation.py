"""Named Agents CRUD + Inter-Agent Delegation with Max-Depth & Signed I2I Trust Bundles (§8F, §8L.4)."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from app.db import new_uuid
from app.models.agents import AgentModel
from app.models.instinct import I2IBundle
from app.models.phase8 import AgentDelegationTask
from app.security.sanitizer import scrub_text
from app.services.llm_router import LLMRouter

MAX_DELEGATION_DEPTH = 2


def serialize_named_agent(a: AgentModel) -> dict[str, Any]:
    return {
        "id": a.id,
        "key": a.key,
        "name_ar": a.name_ar,
        "name_en": a.name_en,
        "description_ar": a.description_ar,
        "description_en": a.description_en,
        "provider_id": a.provider_id,
        "model_name": a.model_name,
        "mode_key": a.mode_key,
        "skills": list(a.skills_json or []),
        "tools_allowed": list(a.tools_allowed or []),
        "system_prompt": a.system_prompt,
        "is_custom": bool(a.is_custom),
        "status": a.status,
        "enabled": bool(a.enabled),
        "category": a.category,
        "priority": a.priority,
    }


def serialize_delegation_task(t: AgentDelegationTask) -> dict[str, Any]:
    return {
        "id": t.id,
        "workspace_id": t.workspace_id,
        "parent_agent_id": t.parent_agent_id,
        "child_agent_id": t.child_agent_id,
        "trust_bundle_id": t.trust_bundle_id,
        "depth": int(t.depth),
        "prompt": t.prompt,
        "status": t.status,
        "result_summary": t.result_summary,
        "permissions_snapshot": t.permissions_snapshot_json or {},
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }


def _bundle_signature(bundle_payload: dict[str, Any]) -> str:
    secret = (os.environ.get("JWT_SECRET_KEY") or "uas-i2i-local-signing-key").encode("utf-8")
    raw = json.dumps(bundle_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(secret, raw, hashlib.sha256).hexdigest()


def create_custom_agent(
    db: Session,
    *,
    workspace_id: str,
    name: str,
    key: str | None = None,
    description: str = "",
    provider_id: str | None = None,
    model_name: str = "auto",
    mode_key: str = "delegate",
    skills: list[str] | None = None,
    tools_allowed: list[str] | None = None,
    system_prompt: str = "",
) -> dict[str, Any]:
    clean_name = scrub_text(name or "").strip()[:120]
    if not clean_name:
        raise ValueError("Agent name is required.")
    raw_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (key or clean_name).lower()).strip("-")
    if len(raw_slug) < 2:
        raw_slug = "agent-" + hashlib.sha1(clean_name.encode("utf-8")).hexdigest()[:6]
    # Ensure global unique AgentModel.key
    cand_key = raw_slug[:48]
    if db.query(AgentModel).filter(AgentModel.key == cand_key).first() is not None:
        cand_key = f"{raw_slug[:36]}-{new_uuid()[:6]}"

    ag = AgentModel(
        workspace_id=workspace_id,
        key=cand_key,
        name_ar=clean_name,
        name_en=clean_name,
        description_ar=scrub_text(description or f"وكيل مخصص: {clean_name}"),
        description_en=scrub_text(description or f"Custom agent: {clean_name}"),
        provider_id=provider_id,
        model_name=(model_name or "auto").strip(),
        mode_key=(mode_key or "delegate").strip(),
        skills_json=list(skills or []),
        tools_allowed=list(tools_allowed or ["search", "read", "chat"]),
        system_prompt=scrub_text(system_prompt or f"You are {clean_name}."),
        is_custom=True,
        status="active",
        enabled=True,
        category="custom",
        priority="P1",
    )
    db.add(ag)
    db.flush()
    return serialize_named_agent(ag)


def create_custom_agent_from_prompt(db: Session, *, workspace_id: str, prompt: str) -> dict[str, Any]:
    clean = scrub_text(prompt or "").strip()
    name = re.sub(
        r"^(?:أنشئ وكيل|انشئ وكيل|اعمل وكيل|create agent|new agent)\s*[:：\-]*\s*",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()[:80] or "وكيل مخصص"
    return create_custom_agent(db, workspace_id=workspace_id, name=name, description=clean)


def update_custom_agent(
    db: Session,
    *,
    workspace_id: str,
    agent_id_or_key: str,
    name: str | None = None,
    description: str | None = None,
    provider_id: str | None = None,
    model_name: str | None = None,
    mode_key: str | None = None,
    skills: list[str] | None = None,
    system_prompt: str | None = None,
    status: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    ag = (
        db.query(AgentModel)
        .filter(
            AgentModel.workspace_id == workspace_id,
            (AgentModel.id == agent_id_or_key) | (AgentModel.key == agent_id_or_key),
        )
        .first()
    )
    if not ag:
        raise ValueError("Agent not found.")
    if name is not None and name.strip():
        ag.name_ar = name.strip()[:120]
        ag.name_en = name.strip()[:120]
    if description is not None:
        ag.description_ar = scrub_text(description)
        ag.description_en = scrub_text(description)
    if provider_id is not None:
        ag.provider_id = provider_id or None
    if model_name is not None:
        ag.model_name = model_name.strip()
    if mode_key is not None:
        ag.mode_key = mode_key.strip()
    if skills is not None:
        ag.skills_json = list(skills)
    if system_prompt is not None:
        ag.system_prompt = scrub_text(system_prompt)
    if status is not None and status in {"active", "idle", "paused"}:
        ag.status = status
        ag.enabled = status != "paused"
    if enabled is not None:
        ag.enabled = bool(enabled)
        ag.status = "active" if enabled else "paused"
    db.flush()
    return serialize_named_agent(ag)


def delete_custom_agent(db: Session, *, workspace_id: str, agent_id_or_key: str) -> bool:
    ag = (
        db.query(AgentModel)
        .filter(
            AgentModel.workspace_id == workspace_id,
            AgentModel.is_custom.is_(True),
            (AgentModel.id == agent_id_or_key) | (AgentModel.key == agent_id_or_key),
        )
        .first()
    )
    if not ag:
        return False
    db.delete(ag)
    db.flush()
    return True


def delegate_agent_task(
    db: Session,
    *,
    workspace_id: str,
    parent_agent_id: str,
    child_agent_id: str,
    prompt: str,
    depth: int = 1,
    allowed_tools: list[str] | None = None,
    max_cost_usd: float = 0.25,
    execute_now: bool = True,
) -> dict[str, Any]:
    """Delegate work from one agent to another enforcing max delegation depth & signed I2IBundle trust bundle (§8F.2, §8L.4)."""
    if depth > MAX_DELEGATION_DEPTH:
        raise ValueError(f"Delegation depth {depth} exceeds limit ({MAX_DELEGATION_DEPTH}).")

    child = (
        db.query(AgentModel)
        .filter(
            AgentModel.workspace_id == workspace_id,
            (AgentModel.id == child_agent_id) | (AgentModel.key == child_agent_id),
        )
        .first()
    )
    if not child:
        raise ValueError(f"Child agent '{child_agent_id}' not found.")
    if not child.enabled or child.status == "paused":
        raise ValueError(f"Child agent '{child.key}' is currently paused.")

    scoped_tools = list(allowed_tools or child.tools_allowed or ["read", "search"])
    bundle_core = {
        "parent_agent_id": parent_agent_id,
        "child_agent_id": child.key,
        "allowed_tools": scoped_tools,
        "skills": list(child.skills_json or []),
        "max_cost_usd": float(max_cost_usd),
        "depth": int(depth),
    }
    sig = _bundle_signature(bundle_core)
    bundle = I2IBundle(
        workspace_id=workspace_id,
        peer_label=f"{parent_agent_id}->{child.key}",
        permissions_json={**bundle_core, "signature": sig},
        status="active",
    )
    db.add(bundle)
    db.flush()

    clean_prompt = scrub_text(prompt or "").strip()
    task = AgentDelegationTask(
        workspace_id=workspace_id,
        parent_agent_id=parent_agent_id,
        child_agent_id=child.key,
        trust_bundle_id=bundle.id,
        depth=int(depth),
        prompt=clean_prompt,
        status="running",
        permissions_snapshot_json=bundle.permissions_json,
    )
    db.add(task)
    db.flush()

    if execute_now:
        try:
            res = LLMRouter(db, workspace_id).route(
                task_type="worker",
                tier="cheap",
                prompt=clean_prompt,
                system_prompt=child.system_prompt or f"You are delegated sub-agent {child.name_en}.",
                preferred_provider=child.provider_id,
                preferred_model=child.model_name if child.model_name and child.model_name != "auto" else None,
                run_max_budget_usd=max_cost_usd,
                use_cache=False,
            )
            task.result_summary = scrub_text(str(res.get("raw_text") or "Delegation completed."))[:2000]
            task.status = "done"
            task.finished_at = datetime.now(timezone.utc)
        except Exception as exc:
            task.result_summary = f"Delegation error: {type(exc).__name__}"
            task.status = "failed"
            task.finished_at = datetime.now(timezone.utc)
        db.flush()

    return serialize_delegation_task(task)


def cancel_delegation_task(db: Session, *, workspace_id: str, delegation_id: str) -> dict[str, Any]:
    task = db.get(AgentDelegationTask, delegation_id)
    if not task or task.workspace_id != workspace_id:
        raise ValueError("Delegation task not found.")
    task.status = "canceled"
    task.finished_at = datetime.now(timezone.utc)
    if task.trust_bundle_id:
        b = db.get(I2IBundle, task.trust_bundle_id)
        if b:
            b.status = "revoked"
    db.flush()
    return serialize_delegation_task(task)


def search_delegation_history(db: Session, *, workspace_id: str, query: str = "") -> list[dict[str, Any]]:
    rows = (
        db.query(AgentDelegationTask)
        .filter(AgentDelegationTask.workspace_id == workspace_id)
        .order_by(AgentDelegationTask.created_at.desc())
        .limit(100)
        .all()
    )
    q = (query or "").strip().lower()
    if q:
        rows = [r for r in rows if q in (r.prompt or "").lower() or q in (r.result_summary or "").lower() or q in r.child_agent_id.lower()]
    return [serialize_delegation_task(r) for r in rows]
