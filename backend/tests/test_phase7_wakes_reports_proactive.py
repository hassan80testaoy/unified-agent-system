"""Phase 7 tests: §7B.1 Event-Driven Wakes (NO polling), §7B.2 Daily 08:00 Digest (+ Skip), §7B.3 Six Proactive Value Loops."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy.orm import Session

from app.models.agents import Approval, Goal, Report, Run, Task
from app.models.backlinks import BlAccount, BlAnchor, BlLink, BlSite, BlTarget
from app.models.phase7 import ProactiveProposal, SiteAudit, WakeEventLog
from app.models.seo import RankSnapshot
from app.models.v3_harness import ChatMessage
from app.services.proxy_health import record_proxy_outcome, reset_proxy_health_cache


def test_event_driven_gmail_wake_fires_once_on_real_event_and_posts_to_chat(
    owner_session: dict,
    temp_db_session: Session,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    tid = client.post("/api/chat/threads", json={"title": "Wakes Stream"}, headers=headers).json()["thread"]["id"]

    sub_res = client.post(
        "/api/wakes/subscriptions",
        json={
            "source": "gmail",
            "trigger_prompt": "فعّل الحساب تلقائياً عند وصول رسالة التأكيد",
            "action_type": "activation",
            "thread_id": tid,
            "filter": {"subject_contains": "Verify", "site_domain": "portal-demo.org"},
        },
        headers=headers,
    )
    assert sub_res.status_code == 200
    sub_id = sub_res.json()["subscription"]["id"]

    raw_activation_url = "https://portal-demo.org/activate?token=SECRET_ACT_TOKEN_999"
    ev_res = client.post(
        "/api/wakes/events/gmail",
        json={
            "event_id": "gmail-msg-101",
            "from": "noreply@portal-demo.org",
            "subject": "Verify your account",
            "body_text": f"Click to confirm: {raw_activation_url}",
        },
        headers=headers,
    )
    assert ev_res.status_code == 200
    eb = ev_res.json()
    assert eb["fired_count"] == 1
    assert eb["fired"][0]["activation_found"] is True
    msg_id = eb["fired"][0]["message_id"]

    # Raw activation secret is NEVER written into ChatMessage or WakeEventLog
    chat_msg = temp_db_session.get(ChatMessage, msg_id)
    assert chat_msg is not None
    assert "SECRET_ACT_TOKEN_999" not in chat_msg.content
    log_row = temp_db_session.query(WakeEventLog).filter(WakeEventLog.external_event_id == "gmail-msg-101").first()
    assert log_row is not None and "SECRET_ACT_TOKEN_999" not in log_row.summary

    # Duplicate event delivery MUST deduplicate and never post a second message
    dup_res = client.post(
        "/api/wakes/events/gmail",
        json={
            "event_id": "gmail-msg-101",
            "from": "noreply@portal-demo.org",
            "subject": "Verify your account",
            "body_text": f"Click to confirm: {raw_activation_url}",
        },
        headers=headers,
    )
    assert dup_res.status_code == 200
    assert dup_res.json()["fired_count"] == 0
    assert dup_res.json()["deduplicated_count"] == 1

    # Also test Gmail Pub/Sub webhook base64 envelope
    pubsub_payload = {
        "from": "noreply@portal-demo.org",
        "subject": "Verify backup event",
        "body_text": "Verification code: 482910",
    }
    b64 = base64.urlsafe_b64encode(json.dumps(pubsub_payload).encode("utf-8")).decode("ascii")
    push_res = client.post(
        "/api/wakes/gmail/push",
        json={"message": {"messageId": "pubsub-ev-202", "data": b64}},
        headers=headers,
    )
    assert push_res.status_code == 200
    assert push_res.json()["fired_count"] == 1
    assert temp_db_session.query(WakeEventLog).filter(WakeEventLog.workspace_id == ws_id).count() == 2


def test_daily_0800_report_sends_with_news_and_skips_when_quiet(
    owner_session: dict,
    temp_db_session: Session,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    reset_proxy_health_cache()

    # Quiet workspace first -> enforce_hour=False with NO activity must SKIP cleanly
    empty_run = client.post(
        "/api/reports/daily-0800/run",
        json={"enforce_hour": False, "post_to_chat": False},
        headers=headers,
    )
    assert empty_run.status_code == 200
    assert empty_run.json()["sent"] is False
    assert empty_run.json()["skipped"] is True

    # Set timezone and create real goal news
    tz_res = client.put("/api/reports/daily-0800/timezone", json={"timezone": "Africa/Cairo"}, headers=headers)
    assert tz_res.status_code == 200
    client.post(
        "/api/goals/plan-from-text",
        json={"sentence": "رفع ترتيب كلمة متجر عربي في جوجل", "execute_auto_steps": True},
        headers=headers,
    )

    # At 09:00 Africa/Cairo (07:00 UTC) with enforce_hour=True -> outside 08:00 window
    outside = client.post(
        "/api/reports/daily-0800/run",
        json={"enforce_hour": True, "post_to_chat": True, "now_iso": "2026-11-29T07:05:00+00:00"},
        headers=headers,
    )
    assert outside.status_code == 200
    assert outside.json()["sent"] is False

    # At 08:05 Africa/Cairo (06:05 UTC in winter) -> fires ONCE with all 5 buckets
    at_eight = client.post(
        "/api/reports/daily-0800/run",
        json={"enforce_hour": True, "post_to_chat": True, "now_iso": "2026-11-29T06:05:00+00:00"},
        headers=headers,
    )
    assert at_eight.status_code == 200
    ab = at_eight.json()
    assert ab["sent"] is True
    assert set(ab["sections"].keys()) == {"done", "learned", "blockers", "next", "need_from_you"}
    assert len(ab["sections"]["done"]) >= 1

    # Immediate re-run with NO new events -> skipped
    quiet_again = client.post(
        "/api/reports/daily-0800/run",
        json={"enforce_hour": False, "post_to_chat": True, "now_iso": "2026-11-29T06:10:00+00:00"},
        headers=headers,
    )
    assert quiet_again.status_code == 200
    assert quiet_again.json()["sent"] is False
    assert quiet_again.json()["skipped"] is True
    assert temp_db_session.query(Report).filter(Report.workspace_id == ws_id, Report.period == "daily_0800").count() == 1


def test_six_proactive_loops_create_proposals_requiring_approval_never_silent(
    owner_session: dict,
    temp_db_session: Session,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]
    uid = owner_session["data"]["user"]["id"]
    reset_proxy_health_cache()

    # Seed signals for ALL 6 proactive value loops
    site = BlSite(workspace_id=ws_id, domain="demo-loop.example")
    temp_db_session.add(site)
    temp_db_session.flush()
    acct = BlAccount(workspace_id=ws_id, site_id=site.id, email_alias="real@mydomain.com", username="u1", password_enc="enc")
    tgt = BlTarget(workspace_id=ws_id, kind="homepage", url="https://moaazhassan.com")
    temp_db_session.add_all([acct, tgt])
    temp_db_session.flush()
    # 1. Dead backlink
    temp_db_session.add(BlLink(workspace_id=ws_id, account_id=acct.id, target_id=tgt.id, live=0, check_note="http_404"))
    # 2. Rank drop (prev pos=3.0 -> latest pos=9.0)
    t_old = datetime.now(timezone.utc) - timedelta(days=2)
    t_new = datetime.now(timezone.utc)
    temp_db_session.add(RankSnapshot(workspace_id=ws_id, keyword="سيو المتاجر", position=3.0, created_at=t_old, updated_at=t_old))
    temp_db_session.add(RankSnapshot(workspace_id=ws_id, keyword="سيو المتاجر", position=9.0, created_at=t_new, updated_at=t_new))
    # 3. Goal with due step
    goal = Goal(
        workspace_id=ws_id,
        title="هدف التصدر",
        outcome="تصدر",
        metric_name="steps",
        status="active",
        plan={"steps": [{"id": "s1", "title": "اعتماد التحديث", "owner": "user", "status": "needs_user", "deadline": "2026-12-01"}]},
    )
    temp_db_session.add(goal)
    # 4. Low-score site audit
    temp_db_session.add(
        SiteAudit(
            workspace_id=ws_id,
            url="https://moaazhassan.com",
            score=62,
            findings_json=[{"severity": "critical", "title_ar": "noindex موجود"}],
            report_md="critical",
        )
    )
    # 5. Stale pending approval
    from app.models.agents import AgentModel

    ag = AgentModel(workspace_id=ws_id, key="backlink-p7-test", name_ar="باك", name_en="Back", description_ar="d")
    temp_db_session.add(ag)
    temp_db_session.flush()
    tk = Task(workspace_id=ws_id, agent_id=ag.id, created_by=uid, request_text="test")
    temp_db_session.add(tk)
    temp_db_session.flush()
    run = Run(workspace_id=ws_id, task_id=tk.id, agent_id=ag.id, status="awaiting_approval")
    temp_db_session.add(run)
    temp_db_session.flush()
    old_ap = datetime.now(timezone.utc) - timedelta(hours=3)
    temp_db_session.add(Approval(workspace_id=ws_id, run_id=run.id, kind="outbound", payload={}, status="pending", created_at=old_ap, updated_at=old_ap))
    # 6. Proxy limit degraded
    record_proxy_outcome(temp_db_session, configured=True, path="direct_fallback", fell_back_to_direct=True, error="proxy_http_401")
    temp_db_session.commit()

    scan = client.post("/api/proactive/scan", headers=headers)
    assert scan.status_code == 200
    sb = scan.json()
    assert sb["new_proposals_count"] == 6
    loops_seen = {p["loop_name"] for p in sb["proposals"]}
    assert loops_seen == {
        "dead_backlink_watcher",
        "rank_drop_detector",
        "goal_step_due",
        "audit_issue_fixer",
        "stale_approval_nudge",
        "budget_and_proxy_guard",
    }
    # All proposals are strictly pending (NEVER auto-executed silently)
    assert all(p["status"] == "pending" for p in sb["proposals"])
    temp_db_session.refresh(goal)
    assert goal.plan["steps"][0]["status"] == "needs_user"

    # Explicit owner approval advances goal step & marks proposal executed
    goal_prop = next(p for p in sb["proposals"] if p["loop_name"] == "goal_step_due")
    app_res = client.post(f"/api/proactive/proposals/{goal_prop['id']}/approve", headers=headers)
    assert app_res.status_code == 200
    assert app_res.json()["proposal"]["status"] == "executed"
    temp_db_session.refresh(goal)
    assert goal.plan["steps"][0]["status"] == "done"

    # Reject another proposal
    other_prop = next(p for p in sb["proposals"] if p["loop_name"] == "dead_backlink_watcher")
    rej_res = client.post(f"/api/proactive/proposals/{other_prop['id']}/reject", headers=headers)
    assert rej_res.status_code == 200
    assert rej_res.json()["proposal"]["status"] == "rejected"
    assert temp_db_session.query(ProactiveProposal).filter(ProactiveProposal.workspace_id == ws_id, ProactiveProposal.status == "rejected").count() == 1
