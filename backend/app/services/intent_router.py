"""Fast Arabic/Egyptian/English Intent Router for the Chat-First General Agent (§1C)."""

from __future__ import annotations

import re
from typing import Any

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_URL_OR_DOMAIN_RE = re.compile(
    r"(?:https?://)?([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+)"
)

_FLIGHTS_KEYWORDS = (
    "أرخص طيران",
    "ارخص طيران",
    "تذاكر طيران",
    "حجز طيران",
    "رحلة طيران",
    "طيران",
    "تذكرة",
    "رحلة",
    "سفر",
    "مطار",
    "flight",
    "flights",
    "airline",
    "airfare",
    "ticket",
)

_BACKLINK_KEYWORDS = (
    "باك لينك",
    "باكلينك",
    "backlink",
    "backlinks",
    "سجل لي حساب في موقع",
    "سجل حساب في موقع",
    "سجّل لي حساب",
    "سجّل باك لينك",
    "سجل باك لينك",
    "اعمل باك لينك",
    "حط باك لينك",
    "سجل في موقع",
    "بروفايل لينك",
    "profile link",
)

_MAIL_KEYWORDS = (
    "شوف إيميلي",
    "شوف ايميلي",
    "افحص إيميلي",
    "افحص ايميلي",
    "اقرأ بريدي",
    "افحص البريد",
    "الرسائل الواردة",
    "صندوق الوارد",
    "إيميلي",
    "ايميلي",
    "بريدي",
    "check email",
    "check my email",
    "check mail",
    "read gmail",
    "gmail",
    "inbox",
)

_LINK_MONITOR_KEYWORDS = (
    "راقب الروابط",
    "افحص الروابط",
    "مراقبة الروابط",
    "حالة الروابط",
    "link monitor",
    "check links",
    "dofollow",
    "nofollow",
)

_REPORTS_KEYWORDS = (
    "تقرير",
    "ملخص اليوم",
    "التقارير",
    "report",
    "reports",
    "daily summary",
)

_CODING_KEYWORDS = (
    "ابني",
    "برمج",
    "برمجة",
    "تطبيق",
    "مهام",
    "كود",
    "build me",
    "create app",
    "build an app",
    "todo app",
    "web app",
    "coding",
)

_BROWSER_KEYWORDS = (
    "افتح",
    "إفتح",
    "خش على",
    "ادخل على",
    "ادخل موقع",
    "روح على",
    "تصفح",
    "شوف موقع",
    "شوفلي",
    "زور موقع",
    "زيارة موقع",
    "دوّر على",
    "دور على",
    "دورلي",
    "دور لي",
    "ابحث عن",
    "ابحثلي",
    "ابحث لي",
    "ابحث في",
    "بحث عن",
    "بحث في جوجل",
    "هاتلي رابط",
    "رابط شركة",
    "موقع شركة",
    "صفحة منتج",
    "سجّل في",
    "سجل في",
    "سجل حساب",
    "اعمل حساب",
    "سكرين شوت",
    "لقطة شاشة",
    "جوجل",
    "قوقل",
    "open ",
    "go to ",
    "visit ",
    "browse ",
    "navigate to",
    "search for",
    "search on",
    "search ",
    "find website",
    "find company",
    "look up",
    "google",
    "screenshot",
    "sign up on",
    "register on",
)

_SEARCH_PREFIXES = (
    "ابحثلي عن",
    "ابحث لي عن",
    "دورلي على",
    "دور لي على",
    "هاتلي رابط",
    "دوّر على",
    "دور على",
    "ابحث عن",
    "بحث عن",
    "ابحث في جوجل عن",
    "بحث في جوجل عن",
    "search for",
    "search ",
    "look up",
    "find ",
)

_SITE_NAME_HINTS = {
    "جوجل": "google",
    "قوقل": "google",
    "google": "google",
    "ويكيبيديا": "wikipedia",
    "wikipedia": "wikipedia",
    "يوتيوب": "youtube",
    "youtube": "youtube",
    "جيت هاب": "github",
    "github": "github",
    "بينج": "bing",
    "bing": "bing",
    "duckduckgo": "duckduckgo",
}

_FULL_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)


def classify_intent(text: str) -> dict[str, Any]:
    """Classify natural-language Arabic/Egyptian/English user request cleanly."""
    clean = (text or "").strip()
    lower = clean.lower()

    emails = _EMAIL_RE.findall(clean)
    email = emails[0] if emails else None
    email_domain = email.split("@", 1)[1].lower() if email and "@" in email else None

    full_urls = _FULL_URL_RE.findall(clean)
    domains: list[str] = []
    for match in _URL_OR_DOMAIN_RE.findall(clean):
        cand = match.lower()
        if email_domain and cand == email_domain:
            continue
        if cand not in domains:
            domains.append(cand)

    entities: dict[str, Any] = {}
    if email:
        entities["email"] = email
    if full_urls:
        entities["url"] = full_urls[0]
    if domains:
        entities["domain"] = domains[0]
        entities["domains"] = domains

    # Extract search query if prompt looks like a web search request
    for sp in _SEARCH_PREFIXES:
        idx = lower.find(sp)
        if idx != -1:
            q_tail = clean[idx + len(sp) :].strip(" :\"'؟?")
            if q_tail:
                entities["query"] = q_tail
            break
    if "query" not in entities and any(k in lower for k in ("ابحث", "دورل", "شركة", "منتج", "search", "find ")):
        entities["query"] = clean[:180]

    for alias, hint in _SITE_NAME_HINTS.items():
        if alias in lower:
            entities.setdefault("site_hint", hint)
            break

    # 0. Explicit Phase 8 platform actions & slash commands
    if lower.startswith("/test") or lower in {"test", "شغل الاختبارات"}:
        return {"intent": "slash_test", "confidence": 0.99, "matched_rule": "/test", "entities": entities}
    if lower.startswith("/lint") or lower == "lint":
        return {"intent": "slash_lint", "confidence": 0.99, "matched_rule": "/lint", "entities": entities}
    if lower.startswith("/git-undo"):
        return {"intent": "slash_git_undo", "confidence": 0.99, "matched_rule": "/git-undo", "entities": entities}

    for kw in ("لقائمة الباك لينك", "لمكتبة الباك لينك", "أضف موقع باك لينك", "add to backlink library"):
        if kw in lower:
            return {"intent": "add_backlink_site", "confidence": 0.97, "matched_rule": kw, "entities": entities}
    if "ضيف موقع" in lower and ("باك لينك" in lower or domains or full_urls):
        return {"intent": "add_backlink_site", "confidence": 0.96, "matched_rule": "ضيف موقع", "entities": entities}

    for kw in ("سجّل مهارة", "سجل مهارة", "مهارة جديدة", "register skill", "create skill", "add skill"):
        if kw in lower:
            return {"intent": "register_skill", "confidence": 0.97, "matched_rule": kw, "entities": entities}

    for kw in ("طوّر النظام", "طور النظام", "طوّر في النظام", "طور في النظام", "عدّل في النظام", "عدل في النظام", "ضيف زر", "أضف زر", "ضيف ميزة في النظام", "self-develop", "develop the system"):
        if kw in lower:
            return {"intent": "self_develop", "confidence": 0.97, "matched_rule": kw, "entities": entities}

    for kw in ("اعمل صورة", "ارسم صورة", "انشئ صورة", "أنشئ صورة", "ولد صورة", "generate image", "create image", "draw image"):
        if kw in lower:
            return {"intent": "generate_image", "confidence": 0.97, "matched_rule": kw, "entities": entities}

    for kw in ("بحث موسع", "ابحث بالتوازي", "wide research"):
        if kw in lower:
            return {"intent": "wide_research", "confidence": 0.96, "matched_rule": kw, "entities": entities}

    for kw in ("أنشئ وكيل", "انشئ وكيل", "اعمل وكيل", "create agent", "new agent"):
        if kw in lower:
            return {"intent": "create_agent", "confidence": 0.96, "matched_rule": kw, "entities": entities}

    for kw in ("ابني موقع", "ابني لي موقع", "انشئ تطبيق ويب", "أنشئ تطبيق ويب", "صمم موقع", "build website", "create web app", "build web app", "build a web app"):
        if kw in lower:
            return {"intent": "build_app", "confidence": 0.96, "matched_rule": kw, "entities": entities}

    # 1. Flights (explicitly NEVER mapped to backlink)
    for kw in _FLIGHTS_KEYWORDS:
        if kw in lower:
            return {"intent": "flights", "confidence": 0.96, "matched_rule": kw, "entities": entities}

    # 2. Explicit Backlink / site registration (checked before generic browser when backlink is requested)
    for kw in _BACKLINK_KEYWORDS:
        if kw in lower:
            return {"intent": "backlink", "confidence": 0.95, "matched_rule": kw, "entities": entities}

    # 3. Mailbox / Gmail read-only check (NEVER mapped to backlink)
    for kw in _MAIL_KEYWORDS:
        if kw in lower:
            return {"intent": "agent-mail", "confidence": 0.95, "matched_rule": kw, "entities": entities}

    # 4. Link Monitor
    for kw in _LINK_MONITOR_KEYWORDS:
        if kw in lower:
            return {"intent": "link_monitor", "confidence": 0.92, "matched_rule": kw, "entities": entities}

    # 4b. Google Search Console live analytics report (§7A.2)
    for kw in ("search console", "سيرش كونسول", "gsc", "نقرات البحث"):
        if kw in lower:
            return {"intent": "gsc_report", "confidence": 0.94, "matched_rule": kw, "entities": entities}

    # 4c. Site Technical SEO & Backlink Auditor (§7A.3)
    for kw in ("افحص موقع", "فحص موقع", "تدقيق موقع", "audit site", "site audit"):
        if kw in lower:
            return {"intent": "site_audit", "confidence": 0.94, "matched_rule": kw, "entities": entities}

    # 4d. One-Sentence Goal Planner (§7A.1)
    for kw in ("عايز موقعي", "يطلع الأول", "هدفي", "ضع خطة ل", "خطط لهدف", "goal:", "my goal"):
        if kw in lower:
            return {"intent": "goal_planner", "confidence": 0.94, "matched_rule": kw, "entities": entities}

    # 5. Reports
    for kw in _REPORTS_KEYWORDS:
        if kw in lower:
            return {"intent": "reports", "confidence": 0.9, "matched_rule": kw, "entities": entities}

    # 6. General Coding Agent (building apps/sites — never forced into SEO/backlinks)
    for kw in _CODING_KEYWORDS:
        if kw in lower:
            return {"intent": "coding", "confidence": 0.92, "matched_rule": kw, "entities": entities}

    # 7. General Browser Agent (any request needing live web open/search/click/signup)
    for kw in _BROWSER_KEYWORDS:
        if kw in lower:
            if kw in ("جوجل", "قوقل", "google") and "site_hint" not in entities:
                entities["site_hint"] = "google"
            return {"intent": "browser", "confidence": 0.92, "matched_rule": kw, "entities": entities}

    if full_urls or domains:
        return {"intent": "browser", "confidence": 0.88, "matched_rule": "explicit_url_or_domain", "entities": entities}

    # 8. General chat
    return {"intent": "general", "confidence": 0.75, "matched_rule": "fallback_general", "entities": entities}
