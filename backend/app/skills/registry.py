"""Skill registry covering all 38 Instinct platform skills + 18 site playbooks with typed execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session


@dataclass
class SkillMetadata:
    name: str
    category: str
    roles: list[str]
    description: str
    implemented_in_phase: int
    input_schema: dict[str, Any] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)


ALL_38_PLATFORM_SKILLS: list[dict[str, Any]] = [
    {"name": "agent-mail", "category": "communication", "roles": ["main_agent", "task_agent"], "phase": 1},
    {"name": "authenticator-codes", "category": "security", "roles": ["main_agent", "task_agent"], "phase": 1},
    {"name": "browser-management", "category": "browser", "roles": ["main_agent"], "phase": 1},
    {"name": "browser-use", "category": "browser", "roles": ["task_agent"], "phase": 1},
    {"name": "bug-report", "category": "ops", "roles": ["main_agent"], "phase": 0},
    {"name": "care-booking", "category": "personal", "roles": ["task_agent"], "phase": 4},
    {"name": "company-identity", "category": "knowledge", "roles": ["main_agent"], "phase": 0},
    {"name": "data-privacy", "category": "security", "roles": ["main_agent", "task_agent"], "phase": 0},
    {"name": "deep-research", "category": "research", "roles": ["task_agent"], "phase": 3},
    {"name": "document-signing", "category": "ops", "roles": ["task_agent"], "phase": 4},
    {"name": "early-access", "category": "ops", "roles": ["main_agent"], "phase": 0},
    {"name": "files", "category": "output", "roles": ["main_agent", "task_agent"], "phase": 2},
    {"name": "flights", "category": "travel", "roles": ["task_agent"], "phase": 4},
    {"name": "food-ordering", "category": "personal", "roles": ["task_agent"], "phase": 4},
    {"name": "gamepigeon", "category": "messaging", "roles": ["main_agent"], "phase": 5},
    {"name": "google-docs", "category": "workspace", "roles": ["task_agent"], "phase": 3},
    {"name": "hotels", "category": "travel", "roles": ["task_agent"], "phase": 4},
    {"name": "i2i-execution", "category": "i2i", "roles": ["task_agent"], "phase": 5},
    {"name": "i2i-management", "category": "i2i", "roles": ["main_agent"], "phase": 5},
    {"name": "instinct-faq", "category": "knowledge", "roles": ["main_agent"], "phase": 0},
    {"name": "integrations", "category": "workspace", "roles": ["main_agent"], "phase": 0},
    {"name": "messaging", "category": "communication", "roles": ["main_agent"], "phase": 1},
    {"name": "onboarding", "category": "cohorts", "roles": ["main_agent"], "phase": 0},
    {"name": "payments", "category": "finance", "roles": ["main_agent", "task_agent"], "phase": 4},
    {"name": "personal-finance", "category": "finance", "roles": ["task_agent"], "phase": 4},
    {"name": "presentations", "category": "output", "roles": ["task_agent"], "phase": 4},
    {"name": "proactive-value-creation", "category": "proactive", "roles": ["main_agent"], "phase": 3},
    {"name": "restaurant-booking", "category": "personal", "roles": ["task_agent"], "phase": 4},
    {"name": "restaurant-recommendations", "category": "personal", "roles": ["main_agent"], "phase": 4},
    {"name": "rideshare", "category": "travel", "roles": ["task_agent"], "phase": 4},
    {"name": "scheduling", "category": "calendar", "roles": ["main_agent", "task_agent"], "phase": 3},
    {"name": "shopping", "category": "commerce", "roles": ["task_agent"], "phase": 4},
    {"name": "slack", "category": "communication", "roles": ["task_agent"], "phase": 4},
    {"name": "speaking", "category": "voice", "roles": ["main_agent"], "phase": 4},
    {"name": "travel", "category": "travel", "roles": ["main_agent", "task_agent"], "phase": 4},
    {"name": "vault-links", "category": "security", "roles": ["main_agent", "task_agent"], "phase": 0},
    {"name": "voice-calls", "category": "voice", "roles": ["main_agent"], "phase": 4},
    {"name": "wake-subscriptions", "category": "proactive", "roles": ["main_agent"], "phase": 3},
]

SITE_PLAYBOOKS: list[str] = [
    "aa.com", "apple.com", "ashbyhq.com", "bestbuy.com", "booking.com", "doordash.com",
    "drive.google.com", "ebay.com", "facebook.com", "google.com-travel", "ikea.com",
    "marriott.com", "opentable.com", "resy.com", "southwest.com", "target.com",
    "weather.com", "zillow.com",
]

ONBOARDING_COHORTS: dict[str, dict[str, str]] = {
    "students": {"ar": "الطلاب", "first_ritual": "تنظيم الجدول الدراسي ومواعيد التسليم"},
    "younger-adults": {"ar": "الشباب المهنيون", "first_ritual": "تتبع الفرص الوظيفية والمهام اليومية"},
    "midlife-adults": {"ar": "المتوسطون مهنياً", "first_ritual": "إدارة المشاريع والتنبيهات الاستباقية"},
    "parents": {"ar": "الأهالي", "first_ritual": "تقويم العائلة والمواعيد المدرسية والصحية"},
    "elderly": {"ar": "كبار السن", "first_ritual": "تذكير المواعيد البسيط وحماية الخصوصية"},
    "business-owners": {"ar": "أصحاب الأعمال والوكالات", "first_ritual": "وكلاء السيو والباك لينك وتقرير 8 صباحاً اليومي"},
}

PROACTIVE_VALUE_LOOPS: list[str] = [
    "outcome_momentum",
    "work_state_truth",
    "safety_from_circumstance",
    "emotional_continuity",
    "project_creation_enrichment",
    "follow_through_accountability",
]


_SKILL_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {}


def register_skill_handler(name: str):
    def _wrap(fn: Callable[..., dict[str, Any]]):
        _SKILL_HANDLERS[name] = fn
        return fn

    return _wrap


@register_skill_handler("vault-links")
def _skill_vault_links(db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.vault import create_vault_request

    return create_vault_request(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        kind=str(params.get("kind", "login")),
        label=str(params.get("label", "Vault Credential")),
        allowed_sites=list(params.get("allowed_sites") or []),
        reason=str(params.get("reason", "Task credential request")),
    )


@register_skill_handler("data-privacy")
def _skill_data_privacy(db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.models.instinct import MemoryFact, Observation
    from app.security.crypto import decrypt_secret

    action = params.get("action", "status")
    if action == "export":
        facts = db.query(MemoryFact).filter(MemoryFact.workspace_id == workspace_id, MemoryFact.user_id == user_id).all()
        return {
            "facts": [
                {"id": f.id, "category": f.category, "content": decrypt_secret(f.content_enc), "allow_training": f.allow_training}
                for f in facts
            ],
            "observations_training_allowed": False,
        }
    return {
        "observations_never_train": True,
        "training_opt_in_default": False,
        "observation_count": db.query(Observation).filter(Observation.workspace_id == workspace_id).count(),
    }


@register_skill_handler("authenticator-codes")
def _skill_authenticator_codes(db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.google_account import verification_coordinator

    run_id = str(params.get("run_id") or "")
    action = str(params.get("action") or "status")
    if action == "relay" and run_id and params.get("code"):
        delivered = verification_coordinator.deliver_code(run_id, str(params["code"]))
        return {"skill": "authenticator-codes", "delivered": delivered, "code_stored": False}
    return {"skill": "authenticator-codes", "status": "ready", "max_asks_per_step": 2}


@register_skill_handler("agent-mail")
def _skill_agent_mail(db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services.mailbox import alias_for, detect_plus_support

    email_addr = str(params.get("email") or "")
    site_slug = str(params.get("site_slug") or "site")
    if email_addr:
        plus_ok = detect_plus_support(email_addr)
        return {
            "skill": "agent-mail",
            "alias": alias_for(email_addr, site_slug, plus_ok),
            "supports_plus": plus_ok,
        }
    return {"skill": "agent-mail", "status": "ready"}


@register_skill_handler("proactive-value-creation")
def _skill_proactive(db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    delta_score = int(params.get("earned_score", 0))
    should_interrupt = delta_score >= 70
    return {
        "loops": PROACTIVE_VALUE_LOOPS,
        "earned_engagement_threshold": 70,
        "earned_score": delta_score,
        "interrupt_user": should_interrupt,
    }


def _register_all_coded_skills() -> None:
    from app.skills.coded.catalog import CODED_SKILLS_SPECS, execute_coded_skill

    for spec in CODED_SKILLS_SPECS:
        slug = spec["slug"]

        def _make_handler(s_name: str):
            def _fn(db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
                return execute_coded_skill(s_name, db, workspace_id, user_id, params)

            return _fn

        _SKILL_HANDLERS[slug] = _make_handler(slug)


_register_all_coded_skills()


def list_all_skills() -> dict[str, Any]:
    from app.skills.coded.catalog import CODED_SKILLS_SPECS

    coded_map = {s["slug"]: s for s in CODED_SKILLS_SPECS}
    items = []
    for entry in ALL_38_PLATFORM_SKILLS:
        c = coded_map.get(entry["name"])
        items.append(
            {
                **entry,
                "executable": entry["name"] in _SKILL_HANDLERS or entry["phase"] <= 1,
                "coded": c is not None,
                "input_schema": c["schema"] if c else {},
                "requires_approval": bool(c["requires_approval"]) if c else False,
            }
        )
    return {
        "platform_skills_count": len(items),
        "coded_skills_count": len(coded_map),
        "platform_skills": items,
        "site_playbooks_count": len(SITE_PLAYBOOKS),
        "site_playbooks": SITE_PLAYBOOKS,
        "onboarding_cohorts": ONBOARDING_COHORTS,
        "proactive_loops": PROACTIVE_VALUE_LOOPS,
    }


def execute_skill(name: str, db: Session, workspace_id: str, user_id: str, params: dict[str, Any]) -> dict[str, Any]:
    if name in _SKILL_HANDLERS:
        return _SKILL_HANDLERS[name](db, workspace_id, user_id, params)
    return {"skill": name, "status": "scaffolded", "note": "Contract registered; full automation scheduled in later phase."}
