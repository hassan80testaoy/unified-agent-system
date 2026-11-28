"""Runtime readiness checks & placeholder-email validation (§0A Honesty Rule)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_env_secret_by_ref, get_settings
from app.models.google_account import GoogleAccount
from app.models.providers import ApiKey, Provider

_EMAIL_FMT_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PLACEHOLDER_LOCALS = {
    "name",
    "yourname",
    "your_name",
    "user",
    "username",
    "test",
    "testing",
    "demo",
    "fake",
    "placeholder",
    "email",
    "someone",
    "example",
}
_PLACEHOLDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "test.com",
    "fake.com",
    "placeholder.com",
    "domain.com",
    "site.com",
    "localhost",
    "invalid",
}


def is_local_endpoint(api_base: str | None) -> bool:
    """Return True if the provider endpoint points to localhost/loopback (local-only §0B.2)."""
    low = (api_base or "").strip().lower()
    return any(
        host in low
        for host in (
            "://localhost",
            "://127.0.0.1",
            "://[::1]",
            "://0.0.0.0",
        )
    )


def validate_real_email(email: str | None) -> tuple[bool, str | None]:
    """Reject empty, malformed, or obvious placeholder email addresses before mock or real runs."""
    raw = (email or "").strip()
    if not raw or not _EMAIL_FMT_RE.match(raw):
        return False, "البريد الإلكتروني غير صالح. يرجى إدخال بريد إلكتروني حقيقي للمتابعة."
    local, dom = raw.lower().split("@", 1)
    base_local = local.split("+", 1)[0]
    if base_local in _PLACEHOLDER_LOCALS or dom in _PLACEHOLDER_DOMAINS or dom.endswith(".example"):
        return (
            False,
            f"البريد '{raw}' يبدو كبريد تجريبي/وهمي (Placeholder). من فضلك أدخل بريدك الحقيقي قبل التشغيل.",
        )
    return True, None


def get_runtime_readiness(db: Session, workspace_id: str) -> dict[str, Any]:
    """Check whether real LLM, browser-worker, Google account, and residential proxy are configured."""
    settings = get_settings()

    providers = (
        db.query(Provider)
        .filter(Provider.workspace_id == workspace_id, Provider.enabled.is_(True))
        .all()
    )
    key_providers = {
        k.provider
        for k in db.query(ApiKey).filter(ApiKey.workspace_id == workspace_id).all()
    }

    has_real_llm = False
    for p in providers:
        if p.kind == "llm-mock" or p.kind == "captcha":
            continue
        if settings.is_production and (is_local_endpoint(p.api_base) or p.kind == "llm-local"):
            continue
        if (
            p.kind == "llm-local"
            or p.id in key_providers
            or bool(get_env_secret_by_ref(p.auth_ref))
        ):
            has_real_llm = True
            break

    has_browser_worker = bool((settings.browser_worker_url or "").strip())
    has_google_account = (
        db.query(GoogleAccount)
        .filter(GoogleAccount.workspace_id == workspace_id, GoogleAccount.status == "active")
        .count()
        > 0
    )
    has_residential_proxy = bool((settings.residential_proxy or "").strip())

    missing_backlink_ar: list[str] = []
    missing_keys: list[str] = []
    if not has_real_llm:
        missing_keys.append("llm_provider")
        missing_backlink_ar.append("مفتاح مزود LLM نشط (من الإعدادات أو .env)")
    if not has_browser_worker:
        missing_keys.append("browser_worker")
        missing_backlink_ar.append("خادم المتصفح Playwright (`BROWSER_WORKER_URL`)")
    if not has_google_account:
        missing_keys.append("google_account")
        missing_backlink_ar.append("حساب Google مربوط ونشط في الخزنة (§2.3)")
    if not has_residential_proxy:
        missing_keys.append("residential_proxy")
        missing_backlink_ar.append("بروكسي سكني (`RESIDENTIAL_PROXY`)")

    real_chat_ready = has_real_llm and has_browser_worker
    real_backlink_ready = has_real_llm and has_browser_worker and has_google_account and has_residential_proxy

    return {
        "llm_provider": has_real_llm,
        "browser_worker": has_browser_worker,
        "google_account": has_google_account,
        "residential_proxy": has_residential_proxy,
        "real_chat_ready": real_chat_ready,
        "real_backlink_ready": real_backlink_ready,
        "default_mock_mode": not real_chat_ready,
        "missing_keys": missing_keys,
        "missing_for_real_backlink": missing_backlink_ar,
    }
