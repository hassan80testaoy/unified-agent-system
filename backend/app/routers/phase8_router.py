"""Phase 8 Unified Platform Router: Memory, Artifacts, Image Gen, Multi-Site Backlinks, Messaging Gateway, Prompt Skills, Agents/i2i, Self-Dev, Dev Tools, Manus Projects/Wide Research, Plugin Catalog & Eval Harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.agents import AgentModel, Approval, Artifact
from app.models.core import SystemState, Workspace
from app.models.phase5 import InstalledSkill
from app.models.phase8 import (
    BacklinkPlacement,
    MessagingEventLog,
    PlatformProject,
    SelfDevProposal,
    TargetSiteProject,
    WideResearchJob,
)
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles
from app.security.sanitizer import scrub_text
from app.services.agent_delegation import (
    cancel_delegation_task,
    create_custom_agent,
    delegate_agent_task,
    delete_custom_agent,
    search_delegation_history,
    serialize_named_agent,
    update_custom_agent,
)
from app.services.artifact_builder import (
    build_presentation_artifact,
    build_web_app_artifact,
    list_workspace_artifacts,
    serialize_platform_artifact,
)
from app.services.backlink_library import (
    create_target_project,
    discover_and_add_library_site,
    ensure_seeded_backlink_library,
    record_backlink_placement,
    serialize_library_site,
    serialize_placement,
    serialize_target_project,
)
from app.services.dev_tools_runner import opt_in_auto_git_commit, run_workspace_checks
from app.services.eval_harness import run_platform_eval_suite, serialize_eval_run
from app.services.image_gen_service import generate_image_artifact
from app.services.llm_router import get_session_cost_summary
from app.services.memory_service import (
    create_memory,
    delete_all_user_memory,
    delete_memory,
    ensure_memory_files,
    export_user_memory,
    list_memories,
    search_memories_vector,
    search_past_sessions,
    update_memory,
)
from app.services.messaging_gateway import (
    configure_gateway_channel,
    handle_inbound_gateway_message,
    list_gateway_channels,
    transcribe_voice_memo,
)
from app.services.plugin_catalog import install_pinned_plugin, list_plugin_catalog
from app.services.repo_map import build_repo_map
from app.services.self_dev_service import (
    decide_self_dev_proposal,
    propose_self_dev_worktree,
    serialize_self_dev_proposal,
)
from app.services.skill_builder import improve_skill_on_use, register_skill_from_prompt
from app.services.wide_research import run_wide_research, serialize_wide_job
from app.skills.coded.catalog import CODED_SKILLS_SPECS, execute_coded_skill

router = APIRouter(tags=["phase8"])


# --- Request Schemas ---


class MemoryCreateP8Request(BaseModel):
    category: str = "fact"
    content: str = Field(min_length=1, max_length=4000)
    allow_training: bool = False


class MemoryPatchP8Request(BaseModel):
    category: str | None = None
    content: str | None = None


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class BuildAppRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=4000)
    thread_id: str | None = None


class PresentationCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    topic: str = Field(min_length=2, max_length=2000)
    slides: list[dict[str, str]] | None = None
    thread_id: str | None = None


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=2000)
    size: str = "1024x1024"
    mock_mode: bool = False
    model: str | None = None
    thread_id: str | None = None


class TargetProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=3, max_length=512)
    maps_url: str | None = None
    notes: str = ""


class LibraryDiscoverRequest(BaseModel):
    domain_or_url: str = Field(min_length=3, max_length=512)
    category: str = "profile"
    notes: str = ""


class PlacementCreateRequest(BaseModel):
    target_project_id: str
    library_site_id: str
    email_used: str
    profile_url: str | None = None
    link_type: str = "unknown"
    live: bool = False
    status: str = "planned"
    notes: str = ""


class GatewayChannelConfigRequest(BaseModel):
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    webhook_secret: str | None = None
    continuity_mode: str = "shared_owner_thread"


class GatewayWebhookInboundRequest(BaseModel):
    peer_id: str = "owner"
    peer_name: str = ""
    text: str = ""
    voice_transcript: str = ""
    mock_mode: bool = False


class SkillRegisterPromptRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)


class SkillImproveRequest(BaseModel):
    note: str = Field(min_length=2, max_length=1000)


class CodedSkillExecuteRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class CustomAgentCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    key: str | None = None
    description: str = ""
    provider_id: str | None = None
    model_name: str = "auto"
    mode_key: str = "delegate"
    skills: list[str] = Field(default_factory=list)
    tools_allowed: list[str] = Field(default_factory=lambda: ["search", "read", "chat"])
    system_prompt: str = ""


class CustomAgentPatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    provider_id: str | None = None
    model_name: str | None = None
    mode_key: str | None = None
    skills: list[str] | None = None
    system_prompt: str | None = None
    status: str | None = None
    enabled: bool | None = None


class AgentDelegateRequest(BaseModel):
    parent_agent_id: str = "lead"
    child_agent_id: str
    prompt: str = Field(min_length=2, max_length=4000)
    depth: int = 1
    allowed_tools: list[str] | None = None
    max_cost_usd: float = 0.25


class SelfDevProposeRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    thread_id: str | None = None
    target_rel_path: str | None = None
    new_content: str | None = None


class SelfDevDecideRequest(BaseModel):
    approve: bool
    commit_in_worktree: bool = False


class DevCheckRequest(BaseModel):
    action: str = Field(default="test", pattern="^(test|lint|git_undo)$")
    workspace_dir: str | None = None


class PlatformProjectCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str | None = None
    instructions_md: str = ""
    knowledge_files: list[dict[str, Any]] = Field(default_factory=list)
    autonomy_mode: str = "checkpoint"


class WideResearchCreateRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    items: list[str] = Field(min_length=1, max_length=20)
    thread_id: str | None = None


class VoiceTranscribeRequest(BaseModel):
    transcript_hint: str = ""


class BrowserModeRequest(BaseModel):
    mode: str = Field(pattern="^(cloud|local_operator)$")


# --- §8K + §8L.3 Memory Endpoints ---


@router.get("/api/memory")
def list_user_memories_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    mem_md, user_md = ensure_memory_files(auth.user.id)
    items = list_memories(db, workspace_id=auth.workspace.id, user_id=auth.user.id)
    return {
        "memory": items,
        "memories": items,
        "memory_md": mem_md.read_text(encoding="utf-8", errors="replace"),
        "user_md": user_md.read_text(encoding="utf-8", errors="replace"),
    }


@router.post("/api/memory")
def create_user_memory_endpoint(
    req: MemoryCreateP8Request,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        item = create_memory(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            content=req.content,
            category=req.category,
            allow_training=req.allow_training,
        )
        db.commit()
        return {"id": item["id"], "memory": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/memory/{memory_id}")
def update_user_memory_endpoint(
    memory_id: str,
    req: MemoryPatchP8Request,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        item = update_memory(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            memory_id=memory_id,
            content=req.content,
            category=req.category,
        )
        db.commit()
        return {"memory": item}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/memory/{memory_id}")
def delete_user_memory_endpoint(
    memory_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    ok = delete_memory(db, workspace_id=auth.workspace.id, user_id=auth.user.id, memory_id=memory_id)
    db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return {"ok": True, "deleted": True, "id": memory_id}


@router.post("/api/memory/search")
def search_user_memory_endpoint(
    req: MemorySearchRequest,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    mem_hits = search_memories_vector(db, workspace_id=auth.workspace.id, user_id=auth.user.id, query=req.query)
    sess_hits = search_past_sessions(db, workspace_id=auth.workspace.id, query=req.query)
    return {"memory_hits": mem_hits, "session_hits": sess_hits}


@router.get("/api/memory/export")
def export_user_memory_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return export_user_memory(db, workspace_id=auth.workspace.id, user_id=auth.user.id)


@router.delete("/api/memory/purge/all")
def purge_all_user_memory_endpoint(
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    removed = delete_all_user_memory(db, workspace_id=auth.workspace.id, user_id=auth.user.id)
    record_audit(db, workspace_id=auth.workspace.id, user_id=auth.user.id, action="memory.purge_all", target=auth.user.id, details={"removed": removed})
    db.commit()
    return {"deleted_all": True, "removed_count": removed}


# --- §8A + §8G Artifacts & Image Generation Endpoints ---


@router.get("/api/artifacts")
def list_artifacts_endpoint(kind: str | None = None, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return {"artifacts": list_workspace_artifacts(db, auth.workspace.id, kind=kind)}


@router.post("/api/artifacts/build-app")
def build_app_artifact_endpoint(
    req: BuildAppRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = build_web_app_artifact(db, workspace_id=auth.workspace.id, prompt=req.prompt, thread_id=req.thread_id)
    db.commit()
    return res


@router.post("/api/artifacts/presentations")
def build_presentation_endpoint(
    req: PresentationCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    art = build_presentation_artifact(
        db,
        workspace_id=auth.workspace.id,
        title=req.title,
        topic=req.topic,
        slides=req.slides,
        thread_id=req.thread_id,
    )
    db.commit()
    return {"artifact": art}


@router.post("/api/images/generate")
def generate_image_endpoint(
    req: ImageGenerateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = generate_image_artifact(
        db,
        workspace_id=auth.workspace.id,
        prompt=req.prompt,
        thread_id=req.thread_id,
        size=req.size,
        mock_mode=req.mock_mode,
        model_override=req.model,
    )
    db.commit()
    return res


def _get_owned_artifact_path(db: Session, workspace_id: str, artifact_id: str) -> tuple[Artifact, Path]:
    art = db.get(Artifact, artifact_id)
    if not art or art.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Artifact not found")
    fp = Path(art.path).resolve()
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="Artifact file missing on disk")
    return art, fp


@router.get("/api/artifacts/{artifact_id}/preview")
def preview_artifact_endpoint(
    artifact_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    art, fp = _get_owned_artifact_path(db, auth.workspace.id, artifact_id)
    if fp.suffix.lower() in {".html", ".htm"}:
        return HTMLResponse(content=fp.read_text(encoding="utf-8", errors="replace"))
    if fp.suffix.lower() == ".png":
        return FileResponse(str(fp), media_type="image/png")
    return FileResponse(str(fp))


@router.get("/api/artifacts/{artifact_id}/download")
def download_artifact_endpoint(
    artifact_id: str,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    _, fp = _get_owned_artifact_path(db, auth.workspace.id, artifact_id)
    return FileResponse(str(fp), filename=fp.name)


# --- §8B Multi-Site Backlink Library & Target Projects ---


@router.get("/api/backlinks/targets/projects")
def list_target_projects_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(TargetSiteProject).filter(TargetSiteProject.workspace_id == auth.workspace.id).order_by(TargetSiteProject.created_at.desc()).all()
    return {"projects": [serialize_target_project(r) for r in rows]}


@router.post("/api/backlinks/targets/projects")
def create_target_project_endpoint(
    req: TargetProjectCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        row = create_target_project(
            db,
            workspace_id=auth.workspace.id,
            name=req.name,
            url=req.url,
            maps_url=req.maps_url,
            notes=req.notes,
        )
        db.commit()
        return {"project": serialize_target_project(row)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/backlinks/targets/projects/{project_id}")
def delete_target_project_endpoint(
    project_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    proj = db.get(TargetSiteProject, project_id)
    if not proj or proj.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail="Target project not found")
    db.query(BacklinkPlacement).filter(BacklinkPlacement.target_project_id == proj.id).delete()
    db.delete(proj)
    db.commit()
    return {"deleted": True, "id": project_id}


@router.get("/api/backlinks/library")
def list_backlink_library_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = ensure_seeded_backlink_library(db, auth.workspace.id)
    db.commit()
    return {"sites": [serialize_library_site(s) for s in rows]}


@router.post("/api/backlinks/library/discover")
async def discover_library_site_endpoint(
    req: LibraryDiscoverRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        site = await discover_and_add_library_site(
            db,
            workspace_id=auth.workspace.id,
            domain_or_url=req.domain_or_url,
            category=req.category,
            notes=req.notes,
        )
        db.commit()
        return {"site": site}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/backlinks/placements")
def list_placements_endpoint(
    target_project_id: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    q = db.query(BacklinkPlacement).filter(BacklinkPlacement.workspace_id == auth.workspace.id)
    if target_project_id:
        q = q.filter(BacklinkPlacement.target_project_id == target_project_id)
    rows = q.order_by(BacklinkPlacement.created_at.desc()).limit(200).all()
    return {"placements": [serialize_placement(r) for r in rows]}


@router.post("/api/backlinks/placements")
def create_placement_endpoint(
    req: PlacementCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        pl = record_backlink_placement(
            db,
            workspace_id=auth.workspace.id,
            target_project_id=req.target_project_id,
            library_site_id=req.library_site_id,
            email_used=req.email_used,
            profile_url=req.profile_url,
            link_type=req.link_type,
            live=req.live,
            status=req.status,
            notes=req.notes,
        )
        db.commit()
        return {"placement": pl}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- §8C + §8L.5 Unified Messaging Gateway & Voice ---


@router.get("/api/messaging/channels")
def list_messaging_channels_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = list_gateway_channels(db, auth.workspace.id)
    db.commit()
    return {"channels": rows}


@router.put("/api/messaging/channels/{channel}")
def configure_messaging_channel_endpoint(
    channel: str,
    req: GatewayChannelConfigRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = configure_gateway_channel(
            db,
            workspace_id=auth.workspace.id,
            channel=channel,
            enabled=req.enabled,
            config=req.config,
            webhook_secret=req.webhook_secret,
            continuity_mode=req.continuity_mode,
        )
        db.commit()
        return {"channel": res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/messaging/webhook/{channel}")
async def inbound_messaging_webhook_endpoint(
    channel: str,
    req: GatewayWebhookInboundRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        out = await handle_inbound_gateway_message(
            db,
            workspace_id=auth.workspace.id,
            channel=channel,
            peer_id=req.peer_id,
            peer_name=req.peer_name,
            text=req.text,
            voice_transcript=req.voice_transcript,
            mock_mode=req.mock_mode,
        )
        db.commit()
        return out
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/messaging/logs")
def list_messaging_logs_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = (
        db.query(MessagingEventLog)
        .filter(MessagingEventLog.workspace_id == auth.workspace.id)
        .order_by(MessagingEventLog.created_at.desc())
        .limit(80)
        .all()
    )
    return {
        "logs": [
            {
                "id": r.id,
                "channel": r.channel,
                "direction": r.direction,
                "external_peer_id": r.external_peer_id,
                "thread_id": r.thread_id,
                "content_preview": r.content_preview,
                "media_kind": r.media_kind,
                "transcript": r.transcript,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.post("/api/voice/transcribe")
def voice_transcribe_endpoint(req: VoiceTranscribeRequest, auth: AuthContext = Depends(get_current_auth)):
    return {"transcript": transcribe_voice_memo(transcript_hint=req.transcript_hint)}


# --- §8D + §8L.1 Skills-by-Prompt & Coded Skills ---


@router.post("/api/skills/register-from-prompt")
def register_skill_prompt_endpoint(
    req: SkillRegisterPromptRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    sk = register_skill_from_prompt(db, workspace_id=auth.workspace.id, prompt=req.prompt, user_id=auth.user.id)
    db.commit()
    return {"skill": sk}


@router.post("/api/skills/{slug}/improve")
def improve_skill_endpoint(
    slug: str,
    req: SkillImproveRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        res = improve_skill_on_use(db, workspace_id=auth.workspace.id, slug=slug, note=req.note)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/skills/coded")
def list_coded_skills_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    custom_rows = db.query(InstalledSkill).filter(InstalledSkill.workspace_id == auth.workspace.id).order_by(InstalledSkill.created_at.desc()).all()
    return {
        "coded_skills": CODED_SKILLS_SPECS,
        "coded_count": len(CODED_SKILLS_SPECS),
        "prompt_skills": [
            {
                "id": r.id,
                "slug": r.slug,
                "name": r.name,
                "description": r.description,
                "source": r.source,
                "install_path": r.install_path,
                "has_script": bool(r.has_script),
                "enabled": bool(r.enabled),
                "tools": r.tools_json or [],
            }
            for r in custom_rows
        ],
    }


@router.post("/api/skills/coded/{slug}/execute")
def execute_coded_skill_endpoint(
    slug: str,
    req: CodedSkillExecuteRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        res = execute_coded_skill(slug, db, auth.workspace.id, auth.user.id, req.params)
        db.commit()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- §8F + §8L.4 Named Agents CRUD & Inter-Agent Delegation ---


@router.get("/api/agents/custom")
def list_custom_agents_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(AgentModel).filter(AgentModel.workspace_id == auth.workspace.id).order_by(AgentModel.is_custom.desc(), AgentModel.created_at.desc()).all()
    return {"agents": [serialize_named_agent(a) for a in rows]}


@router.post("/api/agents/custom")
def create_custom_agent_endpoint(
    req: CustomAgentCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        ag = create_custom_agent(
            db,
            workspace_id=auth.workspace.id,
            name=req.name,
            key=req.key,
            description=req.description,
            provider_id=req.provider_id,
            model_name=req.model_name,
            mode_key=req.mode_key,
            skills=req.skills,
            tools_allowed=req.tools_allowed,
            system_prompt=req.system_prompt,
        )
        db.commit()
        return {"agent": ag}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/agents/custom/{agent_id}")
def patch_custom_agent_endpoint(
    agent_id: str,
    req: CustomAgentPatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        ag = update_custom_agent(
            db,
            workspace_id=auth.workspace.id,
            agent_id_or_key=agent_id,
            name=req.name,
            description=req.description,
            provider_id=req.provider_id,
            model_name=req.model_name,
            mode_key=req.mode_key,
            skills=req.skills,
            system_prompt=req.system_prompt,
            status=req.status,
            enabled=req.enabled,
        )
        db.commit()
        return {"agent": ag}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/api/agents/custom/{agent_id}")
def delete_custom_agent_endpoint(
    agent_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    ok = delete_custom_agent(db, workspace_id=auth.workspace.id, agent_id_or_key=agent_id)
    db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="Custom agent not found")
    return {"deleted": True, "id": agent_id}


@router.post("/api/agents/delegate")
def delegate_agent_endpoint(
    req: AgentDelegateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        res = delegate_agent_task(
            db,
            workspace_id=auth.workspace.id,
            parent_agent_id=req.parent_agent_id,
            child_agent_id=req.child_agent_id,
            prompt=req.prompt,
            depth=req.depth,
            allowed_tools=req.allowed_tools,
            max_cost_usd=req.max_cost_usd,
        )
        db.commit()
        return {"delegation": res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/agents/delegations")
def list_delegations_endpoint(
    q: str = "",
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    return {"delegations": search_delegation_history(db, workspace_id=auth.workspace.id, query=q)}


@router.post("/api/agents/delegations/{delegation_id}/cancel")
def cancel_delegation_endpoint(
    delegation_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    try:
        res = cancel_delegation_task(db, workspace_id=auth.workspace.id, delegation_id=delegation_id)
        db.commit()
        return {"delegation": res}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- §8E Self-Development Worktree Workflow ---


@router.get("/api/self-dev/proposals")
def list_self_dev_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(SelfDevProposal).filter(SelfDevProposal.workspace_id == auth.workspace.id).order_by(SelfDevProposal.created_at.desc()).limit(30).all()
    return {"proposals": [serialize_self_dev_proposal(r) for r in rows]}


@router.post("/api/self-dev/propose")
def create_self_dev_endpoint(
    req: SelfDevProposeRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = propose_self_dev_worktree(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            prompt=req.prompt,
            thread_id=req.thread_id,
            target_rel_path=req.target_rel_path,
            new_content=req.new_content,
        )
        db.commit()
        return {"proposal": res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/self-dev/proposals/{proposal_id}/decide")
def decide_self_dev_endpoint(
    proposal_id: str,
    req: SelfDevDecideRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        res = decide_self_dev_proposal(
            db,
            workspace_id=auth.workspace.id,
            user_id=auth.user.id,
            proposal_id=proposal_id,
            approve=req.approve,
            commit_in_worktree=req.commit_in_worktree,
        )
        db.commit()
        return {"proposal": res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- §8H Dev Tools, §8I Manus Patterns, §8L.6 Eval Harness, Quick Approvals, Browser Mode ---


@router.get("/api/dev/repo-map")
def get_repo_map_endpoint(
    query: str = "",
    token_budget: int = 1800,
    auth: AuthContext = Depends(get_current_auth),
):
    return build_repo_map(query=query, token_budget=token_budget)


@router.post("/api/dev/check")
def run_dev_check_endpoint(
    req: DevCheckRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
):
    return run_workspace_checks(action=req.action, workspace_dir=req.workspace_dir)


@router.get("/api/stats/session-cost")
def get_session_cost_endpoint(
    hours: int = 24,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    return get_session_cost_summary(db, auth.workspace.id, since_hours=hours)


@router.get("/api/projects")
def list_platform_projects_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(PlatformProject).filter(PlatformProject.workspace_id == auth.workspace.id).order_by(PlatformProject.created_at.desc()).all()
    return {
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "instructions_md": p.instructions_md,
                "knowledge_files": p.knowledge_files_json or [],
                "sandbox_dir": p.sandbox_dir,
                "autonomy_mode": p.autonomy_mode,
                "status": p.status,
            }
            for p in rows
        ]
    }


@router.post("/api/projects")
def create_platform_project_endpoint(
    req: PlatformProjectCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    import re as _re
    from app.config import get_settings

    raw_slug = req.slug or _re.sub(r"[^a-zA-Z0-9_-]+", "-", req.name.lower()).strip("-") or f"proj-{len(req.name)}"
    sdir = (get_settings().data_dir / "sandboxes" / raw_slug[:40]).resolve()
    sdir.mkdir(parents=True, exist_ok=True)
    row = PlatformProject(
        workspace_id=auth.workspace.id,
        name=req.name.strip(),
        slug=raw_slug[:64],
        instructions_md=scrub_text(req.instructions_md),
        knowledge_files_json=req.knowledge_files,
        sandbox_dir=str(sdir),
        autonomy_mode=req.autonomy_mode if req.autonomy_mode in {"autonomous", "checkpoint", "supervised"} else "checkpoint",
    )
    db.add(row)
    db.commit()
    return {
        "project": {
            "id": row.id,
            "name": row.name,
            "slug": row.slug,
            "instructions_md": row.instructions_md,
            "knowledge_files": row.knowledge_files_json,
            "sandbox_dir": row.sandbox_dir,
            "autonomy_mode": row.autonomy_mode,
            "status": row.status,
        }
    }


@router.post("/api/research/wide")
def start_wide_research_endpoint(
    req: WideResearchCreateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = run_wide_research(db, workspace_id=auth.workspace.id, query=req.query, items=req.items, thread_id=req.thread_id)
    db.commit()
    return {"job": res}


@router.get("/api/research/wide")
def list_wide_research_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(WideResearchJob).filter(WideResearchJob.workspace_id == auth.workspace.id).order_by(WideResearchJob.created_at.desc()).limit(25).all()
    return {"jobs": [serialize_wide_job(r) for r in rows]}


@router.get("/api/plugins/catalog")
def get_plugin_catalog_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return list_plugin_catalog(db, auth.workspace.id)


@router.post("/api/plugins/catalog/{slug}/install")
def install_plugin_catalog_endpoint(
    slug: str,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    try:
        inst = install_pinned_plugin(db, workspace_id=auth.workspace.id, slug=slug)
        db.commit()
        return {"plugin": inst}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/evals")
def list_eval_runs_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    from app.models.phase8 import EvalRun

    rows = db.query(EvalRun).filter(EvalRun.workspace_id == auth.workspace.id).order_by(EvalRun.created_at.desc()).limit(20).all()
    return {"runs": [serialize_eval_run(r) for r in rows]}


@router.post("/api/evals/run")
def execute_eval_suite_endpoint(
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    res = run_platform_eval_suite(db, workspace_id=auth.workspace.id)
    db.commit()
    return {"run": res}


@router.get("/api/browser/mode")
def get_browser_mode_endpoint(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    row = db.get(SystemState, f"browser_mode:{auth.workspace.id}")
    mode = (row.value_json or {}).get("mode", "cloud") if row and isinstance(row.value_json, dict) else "cloud"
    return {"mode": mode}


@router.put("/api/browser/mode")
def set_browser_mode_endpoint(
    req: BrowserModeRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    key = f"browser_mode:{auth.workspace.id}"
    row = db.get(SystemState, key)
    if row is None:
        db.add(SystemState(key=key, value_json={"mode": req.mode}))
    else:
        row.value_json = {"mode": req.mode}
    db.commit()
    return {"mode": req.mode}


@router.get("/api/browser/screen-latest")
def get_latest_browser_screen_endpoint(auth: AuthContext = Depends(get_current_auth)):
    from app.config import get_settings

    bdir = (get_settings().artifacts_dir / "browser").resolve()
    if not bdir.exists():
        return {"available": False, "screenshot_url": None}
    pngs = sorted(bdir.rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pngs:
        return {"available": False, "screenshot_url": None}
    top = pngs[0]
    rel_sid = top.parent.name
    return {
        "available": True,
        "screenshot_url": f"/api/browser/screenshots/{rel_sid}/{top.name}",
        "updated_at": top.stat().st_mtime,
    }
