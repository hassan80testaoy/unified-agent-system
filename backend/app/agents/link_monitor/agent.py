"""Link Monitor Agent (P0) — verifies live backlinks and detects removed or downgraded links."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agents.backlink.agent import verify_live_link_url
from app.agents.base import AgentContext
from app.models.backlinks import BlAccount, BlLink, BlSite, BlTarget
from app.services.notifications import create_notification


class LinkMonitorAgent:
    key = "link_monitor"

    def plan(self, task_input: dict[str, Any], ctx_info: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"step": "check_links", "limit": int(task_input.get("limit", 50))}]

    def verify(self, item: dict[str, Any]) -> dict[str, Any]:
        return {"ok": bool(item.get("live"))}

    async def run(self, ctx: AgentContext, task_input: dict[str, Any]) -> dict[str, Any]:
        db = ctx.db
        ws = ctx.workspace_id
        limit = int(task_input.get("limit") or 50)
        mock_mode = bool(task_input.get("mock_mode", True))
        simulate_lost_domains = {str(d).lower() for d in (task_input.get("simulate_lost_domains") or [])}
        simulate_downgrade_domains = {str(d).lower() for d in (task_input.get("simulate_downgrade_domains") or [])}

        links = db.query(BlLink).filter(BlLink.workspace_id == ws).limit(limit).all()
        ctx.run.progress_total = len(links)
        ctx.run.status = "running"
        ctx.run.started_at = datetime.now(timezone.utc)
        db.commit()

        checked = 0
        live_count = 0
        lost_count = 0
        downgraded_count = 0

        for idx, link in enumerate(links, start=1):
            ctx.check_killswitch()
            acct = db.get(BlAccount, link.account_id)
            target = db.get(BlTarget, link.target_id)
            site = db.get(BlSite, acct.site_id) if acct else None
            domain = site.domain.lower() if site else ""
            if not acct or not acct.profile_url or not target:
                continue

            was_live = bool(link.live)
            prev_type = link.link_type

            if mock_mode or acct.profile_url.startswith("mock://"):
                link.last_checked_at = datetime.now(timezone.utc).isoformat()
                if domain in simulate_lost_domains:
                    link.live = 0
                    link.check_note = "mock_link_lost"
                elif domain in simulate_downgrade_domains:
                    link.live = 1
                    link.link_type = "nofollow"
                    link.check_note = "mock_downgraded_to_nofollow"
                else:
                    link.check_note = "mock_monitor_ok"
            else:
                res = await verify_live_link_url(acct.profile_url, target.url)
                link.live = 1 if res["found"] else 0
                link.link_type = res["link_type"]
                link.last_checked_at = datetime.now(timezone.utc).isoformat()
                link.check_note = res["note"]

            if link.live:
                live_count += 1
                if prev_type == "dofollow" and link.link_type == "nofollow":
                    downgraded_count += 1
                    create_notification(
                        db,
                        workspace_id=ws,
                        user_id=ctx.user_id,
                        kind="link_downgraded",
                        title=f"تنبيه: تغير نوع الرابط إلى nofollow — {domain or acct.profile_url}",
                        body=f"انخفض تصنيف الباك لينك في {acct.profile_url} من dofollow إلى nofollow.",
                        link=f"/runs/{ctx.run.id}",
                    )
                    await ctx.emit(
                        "warn",
                        "link_downgraded",
                        f"الرابط تغير من dofollow إلى nofollow في {acct.profile_url}",
                        {"profile_url": acct.profile_url, "from": "dofollow", "to": "nofollow"},
                    )
            else:
                lost_count += 1
                if was_live:
                    create_notification(
                        db,
                        workspace_id=ws,
                        user_id=ctx.user_id,
                        kind="link_lost",
                        title=f"تنبيه: اختفاء باك لينك — {domain or acct.profile_url}",
                        body=f"الرابط في {acct.profile_url} لم يعد ظاهراً عند الفحص.",
                        link=f"/runs/{ctx.run.id}",
                    )
                    await ctx.emit(
                        "warn",
                        "link_lost",
                        f"الرابط لم يعد ظاهراً في {acct.profile_url}",
                        {"profile_url": acct.profile_url, "domain": domain},
                    )

            checked += 1
            ctx.run.progress_done = idx
            db.commit()

        ctx.run.status = "done"
        ctx.run.finished_at = datetime.now(timezone.utc)
        summary = {"checked": checked, "live": live_count, "lost": lost_count, "downgraded": downgraded_count}
        ctx.run.summary_json = summary
        db.commit()
        await ctx.emit("info", "monitor_completed", f"تم فحص {checked} رابط ({live_count} حي، {lost_count} مفقود، {downgraded_count} متحول لـ nofollow).", summary)
        return summary
