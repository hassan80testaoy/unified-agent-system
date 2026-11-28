"""Google Search Console read-only OAuth integration and honest live reporting (§7A.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.providers import Connection
from app.models.seo import GscMetric
from app.security.auth import record_audit
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.sanitizer import scrub_text

GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _load_gsc_conn(db: Session, workspace_id: str) -> Connection | None:
    return (
        db.query(Connection)
        .filter(Connection.workspace_id == workspace_id, Connection.kind == "gsc", Connection.status == "active")
        .order_by(Connection.updated_at.desc())
        .first()
    )


def _decode_gsc_config(conn: Connection | None) -> dict[str, Any]:
    if not conn or not conn.config_enc:
        return {}
    try:
        import json

        raw = decrypt_secret(conn.config_enc)
        return json.loads(raw)
    except Exception:
        return {}


def get_gsc_status(db: Session, workspace_id: str) -> dict[str, Any]:
    settings = get_settings()
    conn = _load_gsc_conn(db, workspace_id)
    cfg = _decode_gsc_config(conn) if conn else {}
    return {
        "connected": bool(conn and cfg.get("site_url")),
        "site_url": cfg.get("site_url"),
        "account_email": cfg.get("account_email"),
        "scope": cfg.get("scope") or GSC_READONLY_SCOPE,
        "readonly": True,
        "oauth_client_configured": bool(settings.google_oauth_client_id and settings.google_oauth_client_secret),
        "connected_at": cfg.get("connected_at"),
    }


def build_gsc_oauth_url(*, redirect_uri: str | None = None, state: str = "gsc_readonly") -> dict[str, Any]:
    settings = get_settings()
    cid = (settings.google_oauth_client_id or "").strip()
    ruri = (redirect_uri or settings.google_oauth_redirect_uri or "").strip()
    if not cid:
        return {
            "configured": False,
            "url": None,
            "scope": GSC_READONLY_SCOPE,
            "missing_ar": "اضبط `GOOGLE_OAUTH_CLIENT_ID` و `GOOGLE_OAUTH_CLIENT_SECRET` في `.env` لفتح نافذة Google OAuth.",
        }
    params = urlencode(
        {
            "client_id": cid,
            "redirect_uri": ruri,
            "response_type": "code",
            "scope": GSC_READONLY_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return {
        "configured": True,
        "url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}",
        "scope": GSC_READONLY_SCOPE,
        "redirect_uri": ruri,
    }


def connect_gsc(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    site_url: str,
    account_email: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    import json

    clean_site = (site_url or "").strip()
    if not clean_site:
        raise ValueError("site_url is required")
    if not (access_token or "").strip() and not (refresh_token or "").strip():
        raise ValueError("OAuth access_token or refresh_token is required to connect GSC.")

    payload = {
        "site_url": clean_site,
        "account_email": (account_email or "").strip(),
        "access_token": (access_token or "").strip(),
        "refresh_token": (refresh_token or "").strip(),
        "scope": GSC_READONLY_SCOPE,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    enc = encrypt_secret(json.dumps(payload, ensure_ascii=False))
    existing = _load_gsc_conn(db, workspace_id)
    if existing:
        existing.config_enc = enc
        existing.status = "active"
        conn = existing
    else:
        conn = Connection(workspace_id=workspace_id, kind="gsc", config_enc=enc, status="active")
        db.add(conn)
    record_audit(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
        action="gsc_connected",
        target=clean_site,
        details={"scope": GSC_READONLY_SCOPE, "readonly": True},
    )
    db.flush()
    return get_gsc_status(db, workspace_id)


def disconnect_gsc(db: Session, *, workspace_id: str, user_id: str) -> dict[str, Any]:
    rows = db.query(Connection).filter(Connection.workspace_id == workspace_id, Connection.kind == "gsc").all()
    for r in rows:
        db.delete(r)
    record_audit(db, user_id=user_id, workspace_id=workspace_id, action="gsc_disconnected", target="gsc")
    db.flush()
    return {"connected": False}


def _refresh_access_token_if_needed(cfg: dict[str, Any]) -> str | None:
    tok = (cfg.get("access_token") or "").strip()
    if tok:
        return tok
    rtok = (cfg.get("refresh_token") or "").strip()
    if not rtok:
        return None
    settings = get_settings()
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        return None
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "refresh_token": rtok,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        return None
    return (resp.json() or {}).get("access_token")


def fetch_gsc_report(
    db: Session,
    *,
    workspace_id: str,
    days: int = 28,
    site_url: str | None = None,
) -> dict[str, Any]:
    """Query live Google Search Console Analytics or return an honest empty state if disconnected."""
    status = get_gsc_status(db, workspace_id)
    if not status["connected"]:
        missing_ar = (
            "📭 **Google Search Console غير متصل حالياً (حالة فارغة صادقة — لا توجد أرقام مُختلقة):**\n"
            "- الاتصال المفقود: حساب Google Search Console (صلاحية قراءة فقط `webmasters.readonly`).\n"
            "- الخطوة المطلوبة: افتح **الإعدادات ← الموصلات (Connectors) ← Google Search Console** واضغط «ربط OAuth للعرض فقط»."
        )
        return {
            "connected": False,
            "site_url": None,
            "rows": [],
            "totals": None,
            "top_queries": [],
            "top_pages": [],
            "missing_connection": "gsc_oauth",
            "settings_step": "/settings#connectors",
            "report_ar": missing_ar,
        }

    conn = _load_gsc_conn(db, workspace_id)
    cfg = _decode_gsc_config(conn)
    eff_site = (site_url or cfg.get("site_url") or "").strip()
    token = _refresh_access_token_if_needed(cfg)
    if not token:
        return {
            "connected": True,
            "site_url": eff_site,
            "rows": [],
            "totals": None,
            "top_queries": [],
            "top_pages": [],
            "error": "missing_or_expired_oauth_token",
            "report_ar": (
                f"⚠️ **تعذر جلب بيانات Google Search Console للموقع `{eff_site}`:**\n"
                "- رمز الوصول OAuth غير صالح أو انتهت صلاحيته.\n"
                "- أعد الربط من **الإعدادات ← الموصلات ← Google Search Console**."
            ),
        }

    end_d = datetime.now(timezone.utc).date()
    start_d = end_d - timedelta(days=max(1, int(days)))
    encoded_site = quote(eff_site, safe="")
    endpoint = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
    req_body = {
        "startDate": start_d.isoformat(),
        "endDate": end_d.isoformat(),
        "dimensions": ["query", "page"],
        "rowLimit": 50,
    }
    try:
        resp = httpx.post(
            endpoint,
            json=req_body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
        )
    except Exception as exc:
        err = scrub_text(f"{type(exc).__name__}: {exc}")[:180]
        return {
            "connected": True,
            "site_url": eff_site,
            "rows": [],
            "totals": None,
            "top_queries": [],
            "top_pages": [],
            "error": err,
            "report_ar": f"⚠️ **فشل الاتصال بخادم Google Search Console ({eff_site}):** `{err}` — لم يتم اختلاق أي أرقام.",
        }

    if resp.status_code >= 400:
        err = scrub_text(f"http_{resp.status_code}: {resp.text[:180]}")
        return {
            "connected": True,
            "site_url": eff_site,
            "rows": [],
            "totals": None,
            "top_queries": [],
            "top_pages": [],
            "error": err,
            "report_ar": f"⚠️ **استجابة خطأ من Google Search Console ({eff_site}):** `{err}` — لم يتم اختلاق أي أرقام.",
        }

    raw_rows = (resp.json() or {}).get("rows") or []
    parsed_rows: list[dict[str, Any]] = []
    tot_clicks = 0
    tot_impr = 0
    pos_weighted = 0.0
    date_label = end_d.isoformat()

    for item in raw_rows:
        keys = item.get("keys") or []
        q_val = str(keys[0]) if len(keys) > 0 else ""
        p_val = str(keys[1]) if len(keys) > 1 else None
        clicks = int(round(float(item.get("clicks") or 0)))
        impr = int(round(float(item.get("impressions") or 0)))
        ctr = round(float(item.get("ctr") or 0.0), 4)
        pos = round(float(item.get("position") or 0.0), 2)
        tot_clicks += clicks
        tot_impr += impr
        pos_weighted += pos * max(impr, 1)
        row_dict = {
            "query": q_val,
            "page": p_val,
            "clicks": clicks,
            "impressions": impr,
            "ctr": ctr,
            "position": pos,
            "date": date_label,
        }
        parsed_rows.append(row_dict)
        db.add(
            GscMetric(
                workspace_id=workspace_id,
                site_url=eff_site,
                page=p_val,
                query=q_val,
                date=date_label,
                clicks=clicks,
                impressions=impr,
                ctr=ctr,
                position=pos,
            )
        )
    db.flush()

    avg_ctr = round((tot_clicks / tot_impr), 4) if tot_impr > 0 else 0.0
    avg_pos = round(pos_weighted / max(tot_impr, len(parsed_rows), 1), 2) if parsed_rows else 0.0
    totals = {
        "clicks": tot_clicks,
        "impressions": tot_impr,
        "ctr": avg_ctr,
        "position": avg_pos,
        "rows_count": len(parsed_rows),
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
    }

    top_queries = sorted(parsed_rows, key=lambda r: (r["clicks"], r["impressions"]), reverse=True)[:10]
    page_agg: dict[str, dict[str, Any]] = {}
    for r in parsed_rows:
        pg = r["page"] or "(root)"
        bucket = page_agg.setdefault(pg, {"page": pg, "clicks": 0, "impressions": 0, "positions": []})
        bucket["clicks"] += r["clicks"]
        bucket["impressions"] += r["impressions"]
        bucket["positions"].append(r["position"])
    top_pages = sorted(
        [
            {
                "page": v["page"],
                "clicks": v["clicks"],
                "impressions": v["impressions"],
                "position": round(sum(v["positions"]) / len(v["positions"]), 2),
            }
            for v in page_agg.values()
        ],
        key=lambda r: (r["clicks"], r["impressions"]),
        reverse=True,
    )[:10]

    lines = [
        f"📊 **تقرير Google Search Console الحقيقي — `{eff_site}` ({start_d.isoformat()} → {end_d.isoformat()}):**",
        f"- إجمالي النقرات: **{tot_clicks}** · الظهور: **{tot_impr}** · CTR: **{round(avg_ctr * 100, 2)}%** · متوسط الترتيب: **{avg_pos}**",
    ]
    if top_queries:
        lines.append("\n🔎 **أبرز الكلمات من البيانات الفعلية:**")
        for q in top_queries[:5]:
            lines.append(f"- `{q['query']}` — نقرات: {q['clicks']} | ظهور: {q['impressions']} | ترتيب: {q['position']}")
    else:
        lines.append("- لا توجد صفوف searchAnalytics مسجلة في هذه الفترة.")

    return {
        "connected": True,
        "site_url": eff_site,
        "rows": parsed_rows,
        "totals": totals,
        "top_queries": top_queries,
        "top_pages": top_pages,
        "report_ar": "\n".join(lines),
    }
