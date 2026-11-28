"""Full Free-Model Catalogue (FreeLLMAPI + FCC), pagination/search, manual slug fallback, and role-model routing (§4A-§4B)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
from typing import Any

import httpx
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from app.config import get_env_secret_by_ref, get_settings
from app.db import SessionLocal, utcnow
from app.models.providers import (
    ApiKey,
    Provider,
    ProviderAccount,
    ProviderModel,
    RoleModelAssignment,
)
from app.security.crypto import decrypt_secret
from app.services.llm_router import ReviewerProviderConflictError
from app.services.runtime_checks import is_local_endpoint

AUTO_SYNC_PROVIDERS: set[str] = {"freellmapi", "free-claude-code"}

ROLE_KEYS: tuple[str, ...] = (
    "orchestrator",
    "worker",
    "reviewer",
    "coding_planner",
    "coding_coder",
    "coding_tester",
    "coding_reviewer",
    "coding_researcher",
    "coding_explorer",
    "coding_browser",
    "coding_writer",
)

CATALOGUE_STALE_HOURS = 24

DEFAULT_FREELLMAPI_API_BASE = "http://127.0.0.1:31415/v1"
FREELLMAPI_EMPTY_AR = "لا توجد مفاتيح في FreeLLMAPI — أضف مفاتيح من http://127.0.0.1:31415/Keys"
FREELLMAPI_EMPTY_EN = "No API keys configured in FreeLLMAPI — add keys at http://127.0.0.1:31415/Keys"


def freellmapi_keys_url(api_base: str | None) -> str:
    raw = (api_base or "").strip() or DEFAULT_FREELLMAPI_API_BASE
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/Keys"
    base = raw.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/Keys"


def build_freellmapi_empty_messages(api_base: str | None) -> tuple[str, str]:
    keys_url = freellmapi_keys_url(api_base)
    return (
        f"لا توجد مفاتيح في FreeLLMAPI — أضف مفاتيح من {keys_url}",
        f"No API keys configured in FreeLLMAPI — add keys at {keys_url}",
    )

FCC_FALLBACK_AR = (
    "نقطة /v1/models غير متاحة في free-claude-code — تُستخدم الموديلات الثلاثة الافتراضية أو Slugs المضافة يدوياً (<provider-id>/<model-id>)."
)
FCC_FALLBACK_EN = (
    "/v1/models is unavailable on free-claude-code — using the 3 configured tier models and manual <provider-id>/<model-id> slugs."
)


def _infer_family(model_id: str) -> str:
    clean = (model_id or "").strip()
    if not clean:
        return ""
    if "/" in clean:
        return clean.split("/", 1)[0].strip()[:120]
    parts = re.split(r"[:\-_]", clean, maxsplit=1)
    return (parts[0] if parts else clean)[:120]


def _resolve_key_for_fetch(
    db: Session,
    *,
    workspace_id: str,
    explicit_key: str | None,
    provider: Provider | None,
) -> str | None:
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()
    if provider is None:
        return None
    key_row = (
        db.query(ApiKey)
        .filter(ApiKey.workspace_id == workspace_id, ApiKey.provider == provider.id)
        .first()
    )
    if key_row:
        return decrypt_secret(key_row.secret_enc)
    acct = (
        db.query(ProviderAccount)
        .filter(
            ProviderAccount.workspace_id == workspace_id,
            ProviderAccount.provider_id == provider.id,
            ProviderAccount.status == "active",
        )
        .order_by(ProviderAccount.priority.asc(), ProviderAccount.created_at.asc())
        .first()
    )
    if acct and acct.key_ref_enc:
        return decrypt_secret(acct.key_ref_enc)
    return get_env_secret_by_ref(provider.auth_ref)


def _parse_iso_dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_catalogue_summary(db: Session, workspace_id: str, provider: Provider) -> dict[str, Any]:
    cfg = dict(provider.config_json or {})
    meta = dict(cfg.get("catalogue") or {})

    counts = (
        db.query(ProviderModel.source, func.count(ProviderModel.id))
        .filter(ProviderModel.workspace_id == workspace_id, ProviderModel.provider_id == provider.id)
        .group_by(ProviderModel.source)
        .all()
    )
    by_src = {str(src): int(cnt) for src, cnt in counts}
    fetched_count = by_src.get("fetched", 0)
    manual_count = by_src.get("manual", 0)
    total_count = fetched_count + manual_count

    last_fetched_at = meta.get("last_fetched_at")
    dt = _parse_iso_dt(last_fetched_at)
    stale = True if dt is None else (utcnow() - dt) > timedelta(hours=CATALOGUE_STALE_HOURS)

    empty_ar: str | None = None
    empty_en: str | None = None
    if provider.id == "freellmapi" and total_count == 0:
        empty_ar, empty_en = build_freellmapi_empty_messages(provider.api_base)

    fallback_ar: str | None = None
    fallback_en: str | None = None
    supports_ep = meta.get("supports_models_endpoint")
    if provider.id == "free-claude-code" and supports_ep is False:
        fallback_ar = FCC_FALLBACK_AR
        fallback_en = FCC_FALLBACK_EN

    return {
        "total_count": total_count,
        "fetched_count": fetched_count,
        "manual_count": manual_count,
        "last_fetched_at": last_fetched_at,
        "stale": stale,
        "last_fetch_ok": meta.get("last_fetch_ok"),
        "last_fetch_error": meta.get("last_fetch_error"),
        "supports_models_endpoint": supports_ep,
        "manual_slugs": list(meta.get("manual_slugs") or []),
        "empty_state_ar": empty_ar,
        "empty_state_en": empty_en,
        "fallback_note_ar": fallback_ar,
        "fallback_note_en": fallback_en,
    }


def _update_provider_catalogue_meta(
    provider: Provider,
    *,
    last_fetch_ok: bool,
    last_fetch_error: str | None,
    supports_models_endpoint: bool | None,
    manual_slugs: list[str] | None = None,
) -> None:
    cfg = dict(provider.config_json or {})
    meta = dict(cfg.get("catalogue") or {})
    meta["last_fetched_at"] = utcnow().isoformat()
    meta["last_fetch_ok"] = last_fetch_ok
    meta["last_fetch_error"] = last_fetch_error
    if supports_models_endpoint is not None:
        meta["supports_models_endpoint"] = supports_models_endpoint
    if manual_slugs is not None:
        meta["manual_slugs"] = manual_slugs
    cfg["catalogue"] = meta
    provider.config_json = cfg


async def fetch_endpoint_models(
    db: Session,
    *,
    workspace_id: str,
    api_base: str,
    api_key: str | None = None,
    protocol: str = "openai-completions",
    provider_id: str | None = None,
    persist_catalogue: bool = True,
) -> dict[str, Any]:
    """Single shared model-catalogue fetcher calling GET {base}/models (or Ollama /api/tags)."""
    base = api_base.strip().rstrip("/")
    prov: Provider | None = None
    if provider_id:
        row = db.get(Provider, provider_id)
        if row and row.workspace_id == workspace_id:
            prov = row

    resolved_key = _resolve_key_for_fetch(db, workspace_id=workspace_id, explicit_key=api_key, provider=prov)
    headers: dict[str, str] = {"Accept": "application/json"}
    if resolved_key:
        if protocol == "anthropic":
            headers["x-api-key"] = resolved_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {resolved_key}"

    target_url = (
        f"{base}/api/tags"
        if (protocol == "ollama" or ":11434" in base) and not base.endswith("/v1")
        else f"{base}/models"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(target_url, headers=headers)
    except Exception as exc:
        err_name = type(exc).__name__
        if prov and persist_catalogue:
            _update_provider_catalogue_meta(
                prov,
                last_fetch_ok=False,
                last_fetch_error=err_name,
                supports_models_endpoint=(False if prov.id == "free-claude-code" else None),
            )
            db.flush()
            summary = get_catalogue_summary(db, workspace_id, prov)
        else:
            summary = None
        return {"ok": False, "error": err_name, "models": [], "catalogue": summary}

    if resp.status_code >= 400:
        err_code = f"http_{resp.status_code}"
        supports_ep: bool | None = False if resp.status_code in {404, 405, 501} else None
        if prov and persist_catalogue:
            _update_provider_catalogue_meta(
                prov,
                last_fetch_ok=False,
                last_fetch_error=err_code,
                supports_models_endpoint=supports_ep,
            )
            db.flush()
            summary = get_catalogue_summary(db, workspace_id, prov)
        else:
            summary = None
        return {"ok": False, "error": err_code, "models": [], "catalogue": summary}

    body = resp.json()
    raw_items = body.get("data") or body.get("models") or (body if isinstance(body, list) else [])
    parsed: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
            name = str(item.get("displayName") or item.get("name") or mid).strip()
            owned_by = str(item.get("owned_by") or "").strip()
            meta_dict = {k: v for k, v in item.items() if k not in {"id", "name", "displayName"}}
        elif isinstance(item, str):
            mid = item.strip()
            name = mid
            owned_by = ""
            meta_dict = {}
        else:
            continue
        if not mid or mid in seen_ids:
            continue
        seen_ids.add(mid)
        family = _infer_family(mid) or _infer_family(owned_by)
        parsed.append({"id": mid, "name": name, "family": family, "meta": meta_dict})

    if prov and persist_catalogue:
        (
            db.query(ProviderModel)
            .filter(
                ProviderModel.workspace_id == workspace_id,
                ProviderModel.provider_id == prov.id,
                ProviderModel.source == "fetched",
            )
            .delete(synchronize_session=False)
        )
        manual_existing = {
            r[0]
            for r in db.query(ProviderModel.model_id)
            .filter(
                ProviderModel.workspace_id == workspace_id,
                ProviderModel.provider_id == prov.id,
                ProviderModel.source == "manual",
            )
            .all()
        }
        now = utcnow()
        rows_to_add = [
            ProviderModel(
                workspace_id=workspace_id,
                provider_id=prov.id,
                model_id=m["id"],
                display_name=m["name"][:255],
                family=m["family"][:128],
                source="fetched",
                metadata_json=m["meta"],
                fetched_at=now,
            )
            for m in parsed
            if m["id"] not in manual_existing
        ]
        if rows_to_add:
            db.bulk_save_objects(rows_to_add)

        _update_provider_catalogue_meta(
            prov,
            last_fetch_ok=True,
            last_fetch_error=("empty_no_keys" if not parsed else None),
            supports_models_endpoint=True,
        )
        db.flush()
        summary = get_catalogue_summary(db, workspace_id, prov)
    else:
        summary = None

    empty_ar: str | None = None
    empty_en: str | None = None
    if provider_id == "freellmapi" and len(parsed) == 0 and (not summary or summary["total_count"] == 0):
        empty_ar, empty_en = build_freellmapi_empty_messages(prov.api_base if prov else api_base)

    return {
        "ok": True,
        "models": [{"id": m["id"], "name": m["name"], "family": m["family"]} for m in parsed],
        "empty_state_ar": empty_ar,
        "empty_state_en": empty_en,
        "catalogue": summary,
    }


async def sync_provider_catalogue(db: Session, workspace_id: str, provider: Provider) -> dict[str, Any]:
    return await fetch_endpoint_models(
        db,
        workspace_id=workspace_id,
        api_base=provider.api_base,
        protocol=provider.protocol,
        provider_id=provider.id,
        persist_catalogue=True,
    )


def save_manual_slugs(
    db: Session,
    *,
    workspace_id: str,
    provider: Provider,
    slugs: list[str],
) -> dict[str, Any]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in slugs:
        s = raw.strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)

    (
        db.query(ProviderModel)
        .filter(
            ProviderModel.workspace_id == workspace_id,
            ProviderModel.provider_id == provider.id,
            ProviderModel.source == "manual",
        )
        .delete(synchronize_session=False)
    )
    fetched_existing = {
        r[0]
        for r in db.query(ProviderModel.model_id)
        .filter(
            ProviderModel.workspace_id == workspace_id,
            ProviderModel.provider_id == provider.id,
            ProviderModel.source == "fetched",
        )
        .all()
    }
    now = utcnow()
    for s in cleaned:
        if s in fetched_existing:
            continue
        db.add(
            ProviderModel(
                workspace_id=workspace_id,
                provider_id=provider.id,
                model_id=s[:255],
                display_name=s[:255],
                family=_infer_family(s)[:128],
                source="manual",
                metadata_json={"manual": True},
                fetched_at=now,
            )
        )

    cfg = dict(provider.config_json or {})
    meta = dict(cfg.get("catalogue") or {})
    meta["manual_slugs"] = cleaned
    cfg["catalogue"] = meta
    provider.config_json = cfg
    db.flush()
    return get_catalogue_summary(db, workspace_id, provider)


def get_catalogue_page(
    db: Session,
    *,
    workspace_id: str,
    provider: Provider,
    q: str = "",
    family: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    safe_size = max(1, min(int(page_size or 50), 100))
    safe_page = max(1, int(page or 1))

    base_q = db.query(ProviderModel).filter(
        ProviderModel.workspace_id == workspace_id,
        ProviderModel.provider_id == provider.id,
    )
    if q.strip():
        like = f"%{q.strip().lower()}%"
        base_q = base_q.filter(
            or_(
                func.lower(ProviderModel.model_id).like(like),
                func.lower(ProviderModel.display_name).like(like),
                func.lower(ProviderModel.family).like(like),
            )
        )
    if family.strip():
        base_q = base_q.filter(func.lower(ProviderModel.family) == family.strip().lower())

    total_matching = base_q.count()
    items = (
        base_q.order_by(ProviderModel.source.desc(), ProviderModel.model_id.asc())
        .offset((safe_page - 1) * safe_size)
        .limit(safe_size)
        .all()
    )

    families = [
        str(r[0])
        for r in db.query(ProviderModel.family)
        .filter(
            ProviderModel.workspace_id == workspace_id,
            ProviderModel.provider_id == provider.id,
            ProviderModel.family != "",
        )
        .distinct()
        .order_by(ProviderModel.family.asc())
        .limit(200)
        .all()
    ]

    summary = get_catalogue_summary(db, workspace_id, provider)
    return {
        "provider_id": provider.id,
        "page": safe_page,
        "page_size": safe_size,
        "total": total_matching,
        "total_pages": max(1, (total_matching + safe_size - 1) // safe_size),
        "families": families,
        "items": [
            {
                "id": row.model_id,
                "display_name": row.display_name or row.model_id,
                "family": row.family,
                "source": row.source,
                "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            }
            for row in items
        ],
        "summary": summary,
    }


def get_role_models(db: Session, workspace_id: str) -> dict[str, dict[str, Any]]:
    rows = {
        r.role_key: r
        for r in db.query(RoleModelAssignment)
        .filter(RoleModelAssignment.workspace_id == workspace_id)
        .all()
    }
    out: dict[str, dict[str, Any]] = {}
    for role in ROLE_KEYS:
        row = rows.get(role)
        out[role] = {
            "role_key": role,
            "provider_id": row.provider_id if row else None,
            "tier": row.tier if row else ("strong" if role in {"orchestrator", "coding_planner"} else "mid"),
            "model": row.model if row else "auto",
        }
    return out


def upsert_role_models(
    db: Session,
    workspace_id: str,
    assignments: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = get_role_models(db, workspace_id)
    for role_key, cfg in assignments.items():
        if role_key not in ROLE_KEYS:
            raise ValueError(f"Unsupported role key: {role_key}")
        merged[role_key] = {
            "role_key": role_key,
            "provider_id": (cfg.get("provider_id") or "").strip() or None,
            "tier": (cfg.get("tier") or "mid").strip() or "mid",
            "model": (cfg.get("model") or "auto").strip() or "auto",
        }

    # Reviewer separation check (§0B): orchestrator == worker allowed; reviewer MUST differ
    rev_p = merged["reviewer"]["provider_id"]
    if rev_p:
        for producer_role in ("orchestrator", "worker"):
            if merged[producer_role]["provider_id"] == rev_p:
                raise ReviewerProviderConflictError(
                    f"Reviewer provider ({rev_p}) must differ from {producer_role} provider."
                )

    crev_p = merged["coding_reviewer"]["provider_id"]
    if crev_p:
        for producer_role in ("coding_planner", "coding_coder"):
            if merged[producer_role]["provider_id"] == crev_p:
                raise ReviewerProviderConflictError(
                    f"Coding reviewer provider ({crev_p}) must differ from {producer_role} provider."
                )

    existing = {
        r.role_key: r
        for r in db.query(RoleModelAssignment)
        .filter(RoleModelAssignment.workspace_id == workspace_id)
        .all()
    }
    for role_key in ROLE_KEYS:
        if role_key not in assignments:
            continue
        item = merged[role_key]
        row = existing.get(role_key)
        if row is None:
            db.add(
                RoleModelAssignment(
                    workspace_id=workspace_id,
                    role_key=role_key,
                    provider_id=item["provider_id"],
                    tier=item["tier"],
                    model=item["model"],
                )
            )
        else:
            row.provider_id = item["provider_id"]
            row.tier = item["tier"]
            row.model = item["model"]
    db.flush()
    return get_role_models(db, workspace_id)


async def refresh_stale_catalogues_once(db: Session) -> list[str]:
    """Refresh enabled freellmapi / free-claude-code catalogues whose timestamp is older than 24h."""
    refreshed: list[str] = []
    is_prod = get_settings().is_production
    rows = (
        db.query(Provider)
        .filter(Provider.id.in_(list(AUTO_SYNC_PROVIDERS)), Provider.enabled.is_(True))
        .all()
    )
    for prov in rows:
        if is_prod and (is_local_endpoint(prov.api_base) or prov.kind == "llm-local"):
            continue
        summary = get_catalogue_summary(db, prov.workspace_id, prov)
        if summary["stale"]:
            await sync_provider_catalogue(db, prov.workspace_id, prov)
            refreshed.append(prov.id)
    db.flush()
    return refreshed


async def catalogue_daily_refresh_loop(stop_event: asyncio.Event) -> None:
    """Background daily refresh task for local model catalogues."""
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            await refresh_stale_catalogues_once(db)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=86400.0)
        except asyncio.TimeoutError:
            continue
