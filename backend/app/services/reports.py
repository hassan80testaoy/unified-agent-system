"""Real-metrics Reporting Engine (Daily 08:00 & Weekly + Arabic RTL White-Label Export).

Honesty rule: Every number comes strictly from verified DB tables. Zero guessed rankings or promises.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.captcha.solvers.manual import get_captcha_stats
from app.models.agents import Approval, Report, Run
from app.models.backlinks import BlLink, BlSite
from app.models.providers import LlmCall
from app.security.sanitizer import scrub_text


def compute_real_metrics(db: Session, workspace_id: str, days: int = 1) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    runs = db.query(Run).filter(Run.workspace_id == workspace_id, Run.created_at >= since).all()
    sites = db.query(BlSite).filter(BlSite.workspace_id == workspace_id).all()
    links = db.query(BlLink).filter(BlLink.workspace_id == workspace_id).all()
    pending_approvals = (
        db.query(Approval)
        .filter(Approval.workspace_id == workspace_id, Approval.status == "pending")
        .count()
    )
    spend_usd = float(
        db.query(func.coalesce(func.sum(LlmCall.cost_usd), 0.0))
        .filter(LlmCall.workspace_id == workspace_id, LlmCall.created_at >= since)
        .scalar()
        or 0.0
    )

    live_links = sum(1 for l in links if l.live == 1)
    dofollow_links = sum(1 for l in links if l.live == 1 and l.link_type == "dofollow")
    nofollow_links = sum(1 for l in links if l.live == 1 and l.link_type == "nofollow")

    site_counts = {
        "queued": sum(1 for s in sites if s.status == "queued"),
        "live": sum(1 for s in sites if s.status == "live"),
        "pending_review": sum(1 for s in sites if s.status == "pending_review"),
        "excluded_noindex": sum(1 for s in sites if s.status == "excluded_noindex"),
        "needs_manual": sum(1 for s in sites if s.status in {"needs_manual", "needs_captcha_manual"}),
        "blocked": sum(1 for s in sites if s.status == "blocked"),
        "failed": sum(1 for s in sites if s.status == "failed"),
    }
    captcha_stats = get_captcha_stats(db, workspace_id)

    return {
        "period_days": days,
        "runs_count": len(runs),
        "runs_done": sum(1 for r in runs if r.status == "done"),
        "runs_failed": sum(1 for r in runs if r.status == "failed"),
        "spend_usd": round(spend_usd, 4),
        "pending_approvals": pending_approvals,
        "sites": site_counts,
        "links": {
            "total": len(links),
            "live": live_links,
            "dofollow": dofollow_links,
            "nofollow": nofollow_links,
        },
        "captcha": captcha_stats,
        "ranking_promise": False,
        "note": "الأرقام من فحص قاعدة البيانات والروابط الحية فقط — بدون تخمين أو ضمان ترتيب.",
    }


def generate_report(
    db: Session,
    *,
    workspace_id: str,
    period: str = "daily",
    skip_if_empty: bool = False,
) -> Report | None:
    days = 7 if period == "weekly" else 1
    metrics = compute_real_metrics(db, workspace_id, days=days)

    if skip_if_empty and metrics["runs_count"] == 0 and metrics["pending_approvals"] == 0:
        return None

    title = "التقرير الأسبوعي لنمو الروابط" if period == "weekly" else "التقرير اليومي (08:00)"
    s = metrics["sites"]
    l = metrics["links"]
    c = metrics["captcha"]

    body_md = scrub_text(
        f"""# {title}

## ما تم إنجازه (Done)
- عدد التشغيلات في الفترة: {metrics['runs_count']} (مكتمل: {metrics['runs_done']} | فشل: {metrics['runs_failed']})
- مواقع الباك لينك الحية: {s['live']} | تحت المراجعة: {s['pending_review']} | مستبعدة (noindex): {s['excluded_noindex']}
- الروابط المؤكدة حية: {l['live']} (dofollow: {l['dofollow']} | nofollow: {l['nofollow']})
- التكلفة الفعلية للـ API: ${metrics['spend_usd']:.4f}

## ما تعلمناه (Learned)
- محاولات الكابتشا اليدوية: {c['total']} (نجح يدوياً: {c['passed']} | تخطي: {c['skipped']} | انتهاء مهلة: {c['timeout']})
- نسبة النجاح الفعلية المقاسة: {c['pass_rate_pct']}%

## العوائق (Blockers)
- مواقع محظورة: {s['blocked']} | مواقع فاشلة: {s['failed']}

## الخطوة التالية (Next)
- مواقع متبقية في الطابور: {s['queued']}

## المطلوب منك الآن (Need from you)
- طلبات بانتظار موافقتك / تدخلك (كابتشا يدوي أو كود 2FA): {metrics['pending_approvals']}

---
*تنبيه الصدق: جميع الأرقام مستخرجة من فحص فعلي للروابط وقاعدة البيانات. لا يوجد ضمان ترتيب في جوجل.*
"""
    )

    rep = Report(
        workspace_id=workspace_id,
        period=period,
        title=title,
        body_md=body_md,
        metrics_json=metrics,
        sent_at=datetime.now(timezone.utc),
        channels=["dashboard"],
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep


def render_report_printable_html(report: Report) -> str:
    """Arabic RTL print/PDF-ready HTML document."""
    m = report.metrics_json or {}
    s = m.get("sites") or {}
    l = m.get("links") or {}
    c = m.get("captcha") or {}
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8" />
  <title>{report.title}</title>
  <style>
    body {{ font-family: 'Noto Kufi Arabic', Tahoma, sans-serif; direction: rtl; margin: 32px; color: #1B2430; background: #FFFFFF; line-height: 1.7; }}
    .header {{ border-bottom: 2px solid #0B4F6C; padding-bottom: 12px; margin-bottom: 24px; }}
    .header h1 {{ margin: 0; color: #0B4F6C; font-size: 22px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }}
    .card {{ border: 1px solid #E3E7EE; border-radius: 8px; padding: 12px; background: #F7F8FA; }}
    .card .label {{ font-size: 12px; color: #4A5568; }}
    .card .val {{ font-size: 20px; font-weight: bold; color: #0B4F6C; }}
    .box {{ border: 1px solid #E3E7EE; border-radius: 8px; padding: 16px; white-space: pre-wrap; }}
    .footer {{ margin-top: 24px; font-size: 12px; color: #64748B; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>{report.title}</h1>
    <div>تاريخ الإنشاء: {report.created_at.isoformat()}</div>
  </div>
  <div class="grid">
    <div class="card"><div class="label">مواقع حية</div><div class="val">{s.get('live', 0)}</div></div>
    <div class="card"><div class="label">روابط Dofollow</div><div class="val">{l.get('dofollow', 0)}</div></div>
    <div class="card"><div class="label">تدخل كابتشا</div><div class="val">{c.get('total', 0)}</div></div>
    <div class="card"><div class="label">التكلفة (USD)</div><div class="val">${m.get('spend_usd', 0)}</div></div>
  </div>
  <div class="box">{report.body_md}</div>
  <div class="footer">Unified Agent System — كل الأرقام مبنية على فحص حقيقي بدون تخمين.</div>
</body>
</html>"""
