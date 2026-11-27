"""Task Dispatcher — matches free-text requests strictly against registered agents.

Rule: Never execute outside registered agents. If no agent matches, say so explicitly
and suggest matching items from the 20-agent catalog.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.catalog import SEO_AGENT_CATALOG
from app.models.agents import AgentModel

_KEYWORD_MAP: dict[str, list[str]] = {
    "backlink": [
        "backlink", "backlinks", "باك لينك", "باكلينك", "روابط خلفية", "بروفايلات", "تسجيل مواقع", "backlinks farm",
    ],
    "link_monitor": [
        "monitor", "dofollow", "nofollow", "فحص الروابط", "فحص اللينكات", "صيانة اللينكات", "مراقبة الروابط",
    ],
}


def dispatch_request(db: Session, workspace_id: str, request_text: str) -> dict[str, Any]:
    text = (request_text or "").strip().lower()
    agents = (
        db.query(AgentModel)
        .filter(AgentModel.workspace_id == workspace_id)
        .all()
    )

    if not text:
        return {
            "matched": False,
            "message_ar": "اكتب وصفاً للمهمة أو اختر وكيلاً مباشرة.",
            "message_en": "Enter a task description or choose an agent directly.",
            "suggestions": [],
        }

    # 1. Rule-based match against enabled registered agents
    best_agent: AgentModel | None = None
    best_score = 0
    for agent in agents:
        score = 0
        keywords = _KEYWORD_MAP.get(agent.key, []) + [
            agent.key.lower(),
            agent.name_ar.lower(),
            agent.name_en.lower(),
        ]
        for kw in keywords:
            if kw and kw in text:
                score += 2
        for token in text.split():
            if len(token) >= 3 and (token in agent.description_ar.lower() or token in agent.description_en.lower()):
                score += 1
        if score > best_score and agent.enabled:
            best_score = score
            best_agent = agent

    if best_agent and best_score >= 2:
        return {
            "matched": True,
            "agent_id": best_agent.id,
            "agent_key": best_agent.key,
            "name_ar": best_agent.name_ar,
            "name_en": best_agent.name_en,
            "confidence": min(0.99, 0.55 + best_score * 0.1),
            "message_ar": f"الوكيل المقترح: {best_agent.name_ar}",
            "message_en": f"Suggested agent: {best_agent.name_en}",
        }

    # 2. Check upcoming catalog if no active registered agent matches
    catalog_matches = []
    for item in SEO_AGENT_CATALOG:
        hay = f"{item['key']} {item['name_ar']} {item['name_en']} {item['description_ar']}".lower()
        if any(tok in hay for tok in text.split() if len(tok) >= 3):
            catalog_matches.append(item)

    return {
        "matched": False,
        "agent_id": None,
        "agent_key": None,
        "message_ar": "مفيش وكيل مسجل ينفذ الطلب ده لسه — لن يتم تنفيذ أي أمر خارج الوكلاء المسجلين.",
        "message_en": "No registered agent matches this request yet — execution outside registered agents is blocked.",
        "catalog_suggestions": catalog_matches[:3],
    }
