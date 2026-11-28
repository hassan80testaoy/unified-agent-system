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
    "زور موقع",
    "زيارة موقع",
    "دوّر على",
    "دور على",
    "ابحث عن",
    "ابحث في",
    "بحث في جوجل",
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
    "look up",
    "google",
    "screenshot",
    "sign up on",
    "register on",
)

_SEARCH_PREFIXES = (
    "دوّر على",
    "دور على",
    "ابحث عن",
    "بحث عن",
    "ابحث في جوجل عن",
    "بحث في جوجل عن",
    "search for",
    "search ",
    "look up",
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

    for alias, hint in _SITE_NAME_HINTS.items():
        if alias in lower:
            entities.setdefault("site_hint", hint)
            break

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
