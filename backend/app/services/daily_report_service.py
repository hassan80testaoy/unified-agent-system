"""Daily 08:00 Morning Summary Report reading live Goals/Runs/Audits/Wakes and SKIPPING when nothing happened (§7B.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.agents import Approval, Goal, Report, Run
from app.models.backlinks import BlLink
from app.models.core import Notification, SystemState, Workspace
from app.models.instinct import MemoryFact, ProactiveReport
from app.models.phase7 import HypnosLoraVersion, ProactiveProposal, SiteAudit, WakeEventLog
from app.models.v3_harness import ChatMessage, ChatThread
from app.security.sanitizer import scrub_text
from app.services.proxy_health import get_proxy_health
from app.services.session_log import append_turn

DEFAULT_OWNER_TIMEZONE = "Africa/Cairo"


def get_owner_timezone(db: Session, workspace_id: str) -> str:
    row = db.get(SystemState, f"owner_timezone:{workspace_id}")
    tz_val = str((row.value_json or {}).get("timezone") or "").strip() if row else ""
    if tz_val:
        try:
            ZoneInfo(tz_val)
            return tz_val
        except Exception:
            pass
    return DEFAULT_OWNER_TIMEZONE


def set_owner_timezone(db: Session, workspace_id: str, tz_name: str) -> str:
    clean = (tz_name or "").strip() or DEFAULT_OWNER_TIMEZONE
    try:
        ZoneInfo(clean)
    except Exception as exc:
        raise ValueError(f"Invalid timezone '{tz_name}': {exc}") from exc
    key = f"owner_timezone:{workspace_id}"
    row = db.get(SystemState, key)
    if row is None:
        db.add(SystemState(key=key, value_json={"timezone": clean}))
    else:
        row.value_json = {"timezone": clean}
    db.flush()
    return clean


def _parse_iso_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def generate_daily_0800_report(
    db: Session,
    *,
    workspace_id: str,
    now: datetime | None = None,
    enforce_hour: bool = False,
    post_to_chat: bool = True,
) -> dict[str, Any]:
    """Build the 08:00 owner digest from real DB updates (including Goal records). Skips cleanly if nothing new happened."""
    tz_name = get_owner_timezone(db, workspace_id)
    cur_utc = (now or datetime.now(timezone.utc))
    if cur_utc.tzinfo is None:
        cur_utc = cur_utc.replace(tzinfo=timezone.utc)
    local_now = cur_utc.astimezone(ZoneInfo(tz_name))
    local_date = local_now.date().isoformat()

    state_key = f"daily_0800_state:{workspace_id}"
    state_row = db.get(SystemState, state_key)
    state = dict(state_row.value_json or {}) if (state_row and isinstance(state_row.value_json, dict)) else {}

    if enforce_hour:
        if local_now.hour != 8:
            return {"sent": False, "skipped": True, "reason": "not_0800_local_hour", "timezone": tz_name, "local_hour": local_now.hour}
        if state.get("last_sent_local_date") == local_date:
            return {"sent": False, "skipped": True, "reason": "already_sent_today", "timezone": tz_name, "local_date": local_date}

    cursor_dt = _parse_iso_dt(state.get("last_cursor_iso"))

    def _is_new(dt: datetime | None) -> bool:
        d = _ensure_utc(dt)
        if d is None:
            return False
        if cursor_dt is None:
            return True
        return d > cursor_dt

    done: list[str] = []
    learned: list[str] = []
    blockers: list[str] = []
    next_items: list[str] = []
    need_from_you: list[str] = []

    # 1. Goals (§7A + §7B single shared Goal source of truth)
    goals = db.query(Goal).filter(Goal.workspace_id == workspace_id).order_by(Goal.updated_at.desc()).all()
    for g in goals:
        goal_fresh = _is_new(g.updated_at) or _is_new(g.created_at)
        if not goal_fresh:
            continue
        plan = dict(g.plan or {})
        ckpt = dict(g.checkpoint_json or {})
        steps = list(plan.get("steps") or [])
        pct = ckpt.get("progress_pct")
        done_cnt = sum(1 for s in steps if isinstance(s, dict) and s.get("status") == "done")
        if g.status == "done" or done_cnt > 0:
            done.append(f"هدف «{g.title}»: {done_cnt}/{len(steps) or 1} خطوات مكتملة ({pct if pct is not None else 100}%)")
        for st in steps:
            if not isinstance(st, dict):
                continue
            if st.get("status") == "done":
                if st.get("result_summary"):
                    learned.append(f"{g.title}: {scrub_text(str(st['result_summary']))[:120]}")
            elif st.get("owner") == "user":
                need_from_you.append(f"[{g.title}] {st.get('title')} (مهلة: {st.get('deadline') or 'غير محددة'})")
            else:
                next_items.append(f"[{g.title}] {st.get('title')}")

    # 2. Runs
    for r in db.query(Run).filter(Run.workspace_id == workspace_id).order_by(Run.updated_at.desc()).limit(30).all():
        if not (_is_new(r.updated_at) or _is_new(r.finished_at)):
            continue
        if r.status == "done":
            done.append(f"تشغيل `{r.id[:8]}` اكتمل ({r.progress_done}/{r.progress_total})")
        elif r.status in {"failed", "canceled"}:
            blockers.append(f"تشغيل `{r.id[:8]}` توقف ({r.status}): {scrub_text(r.error or '')[:100]}")

    # 3. Live Backlinks & Site Audits
    for bl in db.query(BlLink).filter(BlLink.workspace_id == workspace_id).order_by(BlLink.updated_at.desc()).limit(30).all():
        if not _is_new(bl.updated_at):
            continue
        if bl.live == 1:
            done.append(f"باك لينك حي ({bl.link_type}) محقّق `{bl.id[:8]}`")
        else:
            blockers.append(f"باك لينك غير ظاهر/معطل `{bl.id[:8]}` ({bl.check_note})")

    for aud in db.query(SiteAudit).filter(SiteAudit.workspace_id == workspace_id).order_by(SiteAudit.created_at.desc()).limit(10).all():
        if not _is_new(aud.created_at):
            continue
        done.append(f"فحص موقع `{aud.url}` (الدرجة {aud.score}/100)")
        crit = [f for f in (aud.findings_json or []) if isinstance(f, dict) and f.get("severity") == "critical"]
        for c in crit[:2]:
            blockers.append(f"[{aud.url}] {c.get('title_ar')}")

    # 4. Event Wakes & Proactive Proposals & Pending Approvals
    for wk in db.query(WakeEventLog).filter(WakeEventLog.workspace_id == workspace_id).order_by(WakeEventLog.fired_at.desc()).limit(15).all():
        if _is_new(wk.fired_at):
            done.append(f"استيقاظ `{wk.source}`: {scrub_text(wk.summary)[:100]}")

    for prop in db.query(ProactiveProposal).filter(ProactiveProposal.workspace_id == workspace_id, ProactiveProposal.status == "pending").all():
        if _is_new(prop.created_at):
            need_from_you.append(f"مقترح استباقي ({prop.loop_name}): {prop.title_ar}")

    for ap in db.query(Approval).filter(Approval.workspace_id == workspace_id, Approval.status == "pending").all():
        if _is_new(ap.created_at):
            need_from_you.append(f"موافقة معلقة (`{ap.kind}`) على التشغيل `{ap.run_id[:8]}`")

    # 5. Hypnos & Memory Learned Items + Proxy Health
    for mem in db.query(MemoryFact).filter(MemoryFact.workspace_id == workspace_id).order_by(MemoryFact.created_at.desc()).limit(10).all():
        if _is_new(mem.created_at):
            learned.append(f"تفضيل/حقيقة جديدة في الذاكرة ({mem.category})")

    for hv in db.query(HypnosLoraVersion).filter(HypnosLoraVersion.workspace_id == workspace_id).order_by(HypnosLoraVersion.created_at.desc()).limit(5).all():
        if _is_new(hv.created_at):
            learned.append(f"نسخة Hypnos `{hv.version_tag}` (exam={hv.exam_score}, status={hv.status})")

    ph = get_proxy_health(db)
    if ph.get("last_fell_back_to_direct") and _is_new(_parse_iso_dt(ph.get("last_error_at"))):
        blockers.append(f"تنبيه بروكسي: {ph.get('last_error')} — تم التبديل للاتصال المباشر")

    # Deduplicate each section preserving order
    done = list(dict.fromkeys(done))
    learned = list(dict.fromkeys(learned))
    blockers = list(dict.fromkeys(blockers))
    next_items = list(dict.fromkeys(next_items))
    need_from_you = list(dict.fromkeys(need_from_you))

    total_news = len(done) + len(learned) + len(blockers) + len(next_items) + len(need_from_you)
    if total_news == 0:
        if state_row is None:
            db.add(SystemState(key=state_key, value_json={"last_cursor_iso": cur_utc.isoformat(), "last_sent_local_date": state.get("last_sent_local_date")}))
            db.flush()
        return {
            "sent": False,
            "skipped": True,
            "reason": "no_new_activity",
            "timezone": tz_name,
            "report": None,
        }

    def _fmt(items: list[str], empty_lbl: str = "لا يوجد جديد") -> str:
        return "\n".join(f"  - {it}" for it in items[:8]) if items else f"  - {empty_lbl}"

    body_md = "\n".join(
        [
            f"🌅 **التقرير الصباحي اليومي (08:00 {tz_name} — {local_date}):**",
            "",
            f"✅ **ما أُنجز ({len(done)}):**\n{_fmt(done)}",
            f"🧠 **ما تعلّمناه ({len(learned)}):**\n{_fmt(learned)}",
            f"⛔ **المعوّقات ({len(blockers)}):**\n{_fmt(blockers, 'لا توجد معوّقات')}",
            f"➡️ **الخطوة التالية ({len(next_items)}):**\n{_fmt(next_items)}",
            f"👤 **مطلوب منك ({len(need_from_you)}):**\n{_fmt(need_from_you, 'لا يوجد إجراء مطلوب حالياً')}",
        ]
    )

    pr = ProactiveReport(
        workspace_id=workspace_id,
        loop_name="daily_0800",
        earned_score=min(100, total_news * 15),
        delivered=True,
        summary_md=body_md,
    )
    rep = Report(
        workspace_id=workspace_id,
        period="daily_0800",
        title=f"التقرير الصباحي 08:00 ({local_date})",
        body_md=body_md,
    )
    db.add(pr)
    db.add(rep)
    db.flush()

    ws = db.get(Workspace, workspace_id)
    if ws and ws.owner_id:
        db.add(
            Notification(
                workspace_id=workspace_id,
                user_id=ws.owner_id,
                kind="daily_0800",
                title=f"التقرير الصباحي 08:00 ({local_date})",
                body=f"أُنجز: {len(done)} | معوّقات: {len(blockers)} | مطلوب منك: {len(need_from_you)}",
                link="/reports",
            )
        )

    if post_to_chat:
        thread = (
            db.query(ChatThread)
            .filter(ChatThread.workspace_id == workspace_id)
            .order_by(ChatThread.updated_at.desc())
            .first()
        )
        if thread is not None:
            last_msg = (
                db.query(ChatMessage)
                .filter(ChatMessage.thread_id == thread.id)
                .order_by(ChatMessage.seq.desc())
                .first()
            )
            seq = (last_msg.seq + 1) if last_msg else 1
            db.add(
                ChatMessage(
                    workspace_id=workspace_id,
                    thread_id=thread.id,
                    seq=seq,
                    role="assistant",
                    intent="reports",
                    card_type="daily_0800",
                    card_payload={
                        "report_id": rep.id,
                        "done_count": len(done),
                        "blockers_count": len(blockers),
                        "need_from_you_count": len(need_from_you),
                        "is_mock": False,
                    },
                    content=body_md,
                )
            )
            append_turn(thread.id, {"type": "daily_0800", "seq": seq, "report_id": rep.id})

    new_state = {
        "last_cursor_iso": cur_utc.isoformat(),
        "last_sent_local_date": local_date,
    }
    if state_row is None:
        db.add(SystemState(key=state_key, value_json=new_state))
    else:
        state_row.value_json = new_state
    db.flush()

    return {
        "sent": True,
        "skipped": False,
        "timezone": tz_name,
        "local_date": local_date,
        "report_id": rep.id,
        "proactive_report_id": pr.id,
        "sections": {
            "done": done,
            "learned": learned,
            "blockers": blockers,
            "next": next_items,
            "need_from_you": need_from_you,
        },
        "report_md": body_md,
    }


def tick_daily_0800_reports(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for ws in db.query(Workspace).all():
        res = generate_daily_0800_report(db, workspace_id=ws.id, now=now, enforce_hour=True)
        if res.get("sent"):
            results.append({"workspace_id": ws.id, **res})
    return results
