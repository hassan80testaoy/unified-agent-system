"""Link Monitor Agent (P0) — verifies live backlinks and detects removed or downgraded links."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agents.backlink.agent import verify_live_link_url
from app.agents.base import AgentContext
from app.models.backlinks import BlAccount, BlLink, BlTarget


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

        links = db.query(BlLink).filter(BlLink.workspace_id == ws).limit(limit).all()
        ctx.run.progress_total = len(links)
        ctx.run.status = "running"
        ctx.run.started_at = datetime.now(timezone.utc)
        db.commit()

        checked = 0
        live_count = 0
        lost_count = 0

        for idx, link in enumerate(links, start=1):
            ctx.check_killswitch()
            acct = db.get(BlAccount, link.account_id)
            target = db.get(BlTarget, link.target_id)
            if not acct or not acct.profile_url or not target:
                continue

            if mock_mode or acct.profile_url.startswith("mock://"):
                link.last_checked_at = datetime.now(timezone.utc).isoformat()
                link.check_note = "mock_monitor_ok"
                if link.live:
                    live_count += 1
                else:
                    lost_count += 1
            else:
                res = await verify_live_link_url(acct.profile_url, target.url)
                was_live = link.live
                link.live = 1 if res["found"] else 0
                link.link_type = res["link_type"]
                link.last_checked_at = datetime.now(timezone.utc).isoformat()
                link.check_note = res["note"]
                if link.live:
                    live_count += 1
                else:
                    lost_count += 1
                    if was_live:
                        await ctx.emit("warn", "link_lost", f"الرابط لم يعد ظاهراً في {acct.profile_url}")

            checked += 1
            ctx.run.progress_done = idx
            db.commit()

        ctx.run.status = "done"
        ctx.run.finished_at = datetime.now(timezone.utc)
        summary = {"checked": checked, "live": live_count, "lost": lost_count}
        ctx.run.summary_json = summary
        db.commit()
        await ctx.emit("info", "monitor_completed", f"تم فحص {checked} رابط ({live_count} حي، {lost_count} مفقود).", summary)
        return summary
