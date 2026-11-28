"""Coded Platform Skills Dispatcher & Shared Schema Contracts (§8L.1 — 13 real coded platform skills)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.agents import Approval, Run, Task
from app.models.phase5 import AutomationTrigger
from app.security.sanitizer import scrub_text
from app.services.artifact_builder import build_presentation_artifact
from app.services.wakes_service import create_wake_subscription

CODED_SKILLS_SPECS: list[dict[str, Any]] = [
    {
        "slug": "flights",
        "title_ar": "البحث وحجز الطيران",
        "category": "travel",
        "requires_approval": True,
        "schema": {
            "type": "object",
            "required": ["origin", "destination"],
            "properties": {
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "date": {"type": "string"},
                "passengers": {"type": "integer", "minimum": 1},
            },
        },
        "guide": "Search flight routes honestly via browser/API and always pause for explicit approval before payment/booking.",
    },
    {
        "slug": "hotels",
        "title_ar": "البحث وحجز الفنادق",
        "category": "travel",
        "requires_approval": True,
        "schema": {
            "type": "object",
            "required": ["city"],
            "properties": {
                "city": {"type": "string"},
                "checkin": {"type": "string"},
                "checkout": {"type": "string"},
                "guests": {"type": "integer", "minimum": 1},
            },
        },
        "guide": "Search real hotel availability and pause on checkpoint approval before committing reservation or card details.",
    },
    {
        "slug": "agent-mail",
        "title_ar": "إدارة البريد والتحقق",
        "category": "communication",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "site_slug": {"type": "string"},
                "action": {"type": "string", "enum": ["status", "alias"]},
            },
        },
        "guide": "Manage read-only IMAP mailboxes, generate safe +aliases, and extract verification links transiently without storage.",
    },
    {
        "slug": "scheduling",
        "title_ar": "جدولة المهام والتقويم",
        "category": "calendar",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["name", "cron_expr", "action_prompt"],
            "properties": {
                "name": {"type": "string"},
                "cron_expr": {"type": "string"},
                "action_prompt": {"type": "string"},
                "channels": {"type": "array", "items": {"type": "string"}},
            },
        },
        "guide": "Create recurring cron automations wired to the triggers scheduler with visible workspace notifications.",
    },
    {
        "slug": "shopping",
        "title_ar": "مقارنة وشراء المنتجات",
        "category": "commerce",
        "requires_approval": True,
        "schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_price_usd": {"type": "number", "minimum": 0},
            },
        },
        "guide": "Compare real product prices via live browser and require approval + Blind-Fill Vault before checkout.",
    },
    {
        "slug": "deep-research",
        "title_ar": "البحث العميق والمتوازي",
        "category": "research",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
            },
        },
        "guide": "Fan out isolated parallel sub-agent research items (Wide Research) and export structured Markdown/CSV deliverable.",
    },
    {
        "slug": "presentations",
        "title_ar": "إنشاء العروض التقديمية",
        "category": "output",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["title", "topic"],
            "properties": {
                "title": {"type": "string"},
                "topic": {"type": "string"},
                "slides": {"type": "array"},
            },
        },
        "guide": "Generate interactive multi-slide RTL HTML presentation decks saved as previewable Artifacts.",
    },
    {
        "slug": "messaging",
        "title_ar": "بوابة المراسلة الموحدة",
        "category": "communication",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["channel", "message"],
            "properties": {
                "channel": {"type": "string", "enum": ["whatsapp", "telegram", "email"]},
                "peer_id": {"type": "string"},
                "message": {"type": "string"},
            },
        },
        "guide": "Route cross-platform messages (WhatsApp, Telegram, Email) to the unified thread; never send unsolicited.",
    },
    {
        "slug": "files",
        "title_ar": "معالجة وتصدير الملفات",
        "category": "output",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["action", "filename"],
            "properties": {
                "action": {"type": "string", "enum": ["write", "read", "list"]},
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        "guide": "Sandboxed artifact file creation & reading inside workspace artifacts/files only (no path traversal).",
    },
    {
        "slug": "payments",
        "title_ar": "الدفع الآمن ببوابة موافقة",
        "category": "finance",
        "requires_approval": True,
        "schema": {
            "type": "object",
            "required": ["merchant", "amount_usd"],
            "properties": {
                "merchant": {"type": "string"},
                "amount_usd": {"type": "number", "minimum": 0},
                "currency": {"type": "string"},
            },
        },
        "guide": "Always create an explicit spend approval gate + Blind-Fill Vault card reference; never store raw card details.",
    },
    {
        "slug": "restaurant-booking",
        "title_ar": "حجز المطاعم",
        "category": "personal",
        "requires_approval": True,
        "schema": {
            "type": "object",
            "required": ["restaurant", "party_size", "datetime_iso"],
            "properties": {
                "restaurant": {"type": "string"},
                "party_size": {"type": "integer", "minimum": 1},
                "datetime_iso": {"type": "string"},
            },
        },
        "guide": "Check table availability via browser and ask approval confirmation before finalizing reservation.",
    },
    {
        "slug": "voice-calls",
        "title_ar": "المكالمات والصوتيات (STT/TTS)",
        "category": "voice",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "mode": {"type": "string", "enum": ["transcribe", "tts_plan"]},
            },
        },
        "guide": "Transcribe incoming voice memos cleanly and prepare TTS audio synthesis payloads for chat read-aloud.",
    },
    {
        "slug": "wake-subscriptions",
        "title_ar": "اشتراكات الاستيقاظ الفوري",
        "category": "proactive",
        "requires_approval": False,
        "schema": {
            "type": "object",
            "required": ["source", "trigger_prompt"],
            "properties": {
                "source": {"type": "string"},
                "trigger_prompt": {"type": "string"},
                "site_domain": {"type": "string"},
            },
        },
        "guide": "Create event-driven (non-polling) wake subscriptions for Gmail/Calendar/Webhook events.",
    },
]


def _validate_against_schema(params: dict[str, Any], schema: dict[str, Any]) -> None:
    for req_key in schema.get("required") or []:
        if params.get(req_key) in (None, ""):
            raise ValueError(f"Missing required parameter '{req_key}'")


def _create_approval_gate(db: Session, workspace_id: str, user_id: str, kind: str, payload: dict[str, Any]) -> str:
    ap = Approval(
        workspace_id=workspace_id,
        run_id=payload.get("run_id") or _ensure_placeholder_run(db, workspace_id, user_id),
        kind=kind,
        payload=payload,
        status="pending",
    )
    db.add(ap)
    db.flush()
    return ap.id


def _ensure_placeholder_run(db: Session, workspace_id: str, user_id: str) -> str:
    from app.models.agents import AgentModel

    existing_run = db.query(Run).filter(Run.workspace_id == workspace_id).order_by(Run.created_at.desc()).first()
    if existing_run:
        return existing_run.id
    ag = db.query(AgentModel).filter(AgentModel.workspace_id == workspace_id).first()
    if not ag:
        ag = AgentModel(
            workspace_id=workspace_id,
            key=f"skill-gate-{workspace_id[:6]}",
            name_ar="وكيل المهارات",
            name_en="Skills Gate",
            description_ar="بوابة الموافقات للمهارات",
            description_en="Skill approval gate",
        )
        db.add(ag)
        db.flush()
    t = Task(workspace_id=workspace_id, agent_id=ag.id, request_text="skill_approval_gate", created_by=user_id)
    db.add(t)
    db.flush()
    r = Run(workspace_id=workspace_id, task_id=t.id, agent_id=ag.id, status="awaiting_approval")
    db.add(r)
    db.flush()
    return r.id


def execute_coded_skill(slug: str, db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = next((s for s in CODED_SKILLS_SPECS if s["slug"] == slug), None)
    if spec is None:
        raise ValueError(f"Unknown coded skill: {slug}")
    p = dict(params or {})
    _validate_against_schema(p, spec["schema"])

    if slug in {"flights", "hotels", "shopping", "payments", "restaurant-booking"}:
        ap_kind = "spend" if slug in {"payments", "shopping"} else "outbound"
        ap_id = _create_approval_gate(
            db,
            workspace_id,
            user_id,
            ap_kind,
            {"skill": slug, "params": {k: scrub_text(str(v)) for k, v in p.items()}},
        )
        return {
            "skill": slug,
            "status": "awaiting_approval",
            "approval_id": ap_id,
            "requires_approval": True,
            "summary_ar": f"تم تجهيز طلب `{slug}` ووضعه قيد الموافقة (`{ap_id[:8]}`) دون تنفيذ أي دفع أو حجز تلقائي.",
        }

    if slug == "agent-mail":
        from app.services.mailbox import alias_for, detect_plus_support

        email_addr = str(p.get("email") or "")
        site_slug = str(p.get("site_slug") or "site")
        if email_addr:
            plus_ok = detect_plus_support(email_addr)
            return {"skill": slug, "status": "ok", "alias": alias_for(email_addr, site_slug, plus_ok), "supports_plus": plus_ok}
        return {"skill": slug, "status": "ok", "mode": "readonly_imap"}

    if slug == "scheduling":
        tr = AutomationTrigger(
            workspace_id=workspace_id,
            name=str(p["name"]),
            trigger_type="schedule",
            cron_expr=str(p["cron_expr"]),
            action_prompt=str(p["action_prompt"]),
            enabled=True,
            paused=False,
        )
        db.add(tr)
        db.flush()
        return {"skill": slug, "status": "ok", "trigger_id": tr.id, "cron": tr.cron_expr}

    if slug == "deep-research":
        from app.services.wide_research import run_wide_research

        items = [str(x) for x in (p.get("items") or [p["query"]])]
        res = run_wide_research(db, workspace_id=workspace_id, query=str(p["query"]), items=items)
        return {"skill": slug, "status": "ok", "job_id": res["id"], "artifact_id": res.get("artifact_id"), "count": len(items)}

    if slug == "presentations":
        art = build_presentation_artifact(
            db,
            workspace_id=workspace_id,
            title=str(p["title"]),
            topic=str(p["topic"]),
            slides=p.get("slides"),
        )
        return {"skill": slug, "status": "ok", "artifact": art}

    if slug == "messaging":
        from app.services.messaging_gateway import deliver_scheduled_summary

        out = deliver_scheduled_summary(db, workspace_id=workspace_id, channels=[str(p["channel"])], summary_text=str(p["message"]))
        return {"skill": slug, "status": "ok", "delivery": out}

    if slug == "files":
        safe_name = Path(str(p["filename"])).name
        fdir = (get_settings().artifacts_dir / "files").resolve()
        fdir.mkdir(parents=True, exist_ok=True)
        target = (fdir / safe_name).resolve()
        if not str(target).startswith(str(fdir)):
            raise ValueError("Invalid filename")
        act = str(p["action"])
        if act == "write":
            txt = scrub_text(str(p.get("content") or ""))
            target.write_text(txt, encoding="utf-8")
            return {"skill": slug, "status": "ok", "path": str(target), "bytes": len(txt.encode("utf-8"))}
        if act == "read":
            return {"skill": slug, "status": "ok", "content": target.read_text(encoding="utf-8") if target.exists() else ""}
        return {"skill": slug, "status": "ok", "files": sorted(x.name for x in fdir.iterdir() if x.is_file())}

    if slug == "voice-calls":
        return {"skill": slug, "status": "ok", "mode": p.get("mode", "tts_plan"), "text": scrub_text(str(p["text"]))[:500]}

    if slug == "wake-subscriptions":
        sub = create_wake_subscription(
            db,
            workspace_id=workspace_id,
            source=str(p["source"]),
            trigger_prompt=str(p["trigger_prompt"]),
            filter_json={"site_domain": p.get("site_domain", "")} if p.get("site_domain") else {},
        )
        return {"skill": slug, "status": "ok", "subscription_id": sub.id}

    return {"skill": slug, "status": "ok"}


def ensure_coded_skill_packages_on_disk() -> Path:
    """Materialize SKILL.md + skill.py + references/guide.md for all 13 coded platform skills (§8L.1)."""
    base = Path(__file__).resolve().parent
    for spec in CODED_SKILLS_SPECS:
        slug = spec["slug"]
        sdir = base / slug
        rdir = sdir / "references"
        rdir.mkdir(parents=True, exist_ok=True)

        md = (
            f"---\nname: {slug}\ncategory: {spec['category']}\nrequires_approval: {str(spec['requires_approval']).lower()}\n---\n\n"
            f"# {spec['title_ar']} (`{slug}`)\n\n{spec['guide']}\n\n## Input Schema\n```json\n"
            + json.dumps(spec["schema"], indent=2, ensure_ascii=False)
            + "\n```\n"
        )
        md_file = sdir / "SKILL.md"
        if not md_file.exists():
            md_file.write_text(md, encoding="utf-8")

        py_code = (
            f'"""Coded platform skill `{slug}` ({spec["title_ar"]})."""\n\n'
            f"from app.skills.coded.catalog import execute_coded_skill\n\n"
            f"SKILL_SLUG = {slug!r}\n"
            f"INPUT_SCHEMA = {spec['schema']!r}\n\n"
            f"def execute(db, workspace_id: str, user_id: str, params: dict):\n"
            f"    return execute_coded_skill(SKILL_SLUG, db, workspace_id, user_id, params)\n"
        )
        py_file = sdir / "skill.py"
        if not py_file.exists():
            py_file.write_text(py_code, encoding="utf-8")

        ref_file = rdir / "guide.md"
        if not ref_file.exists():
            ref_file.write_text(f"# Reference Guide — {slug}\n\n{spec['guide']}\n", encoding="utf-8")
    return base
