"""Backlink Profiles Agent (P0) — full workflow with identity rules, noindex skip, manual captcha plugin, Google/IMAP verification, step recording, and live link verification."""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, KillSwitchTriggeredError
from app.captcha.solver import CaptchaChallenge
from app.captcha.solvers.manual import pause_for_manual_captcha
from app.config import get_settings
from app.models.agents import RunItem
from app.models.backlinks import (
    BlAccount,
    BlAnchor,
    BlBio,
    BlIdentity,
    BlLink,
    BlSite,
    BlStep,
    BlTarget,
)
from app.models.google_account import GoogleAccount
from app.models.mailboxes import Mailbox
from app.security.crypto import decrypt_secret
from app.services.google_account import ask_user_for_verification_code
from app.services.mailbox import alias_for, fetch_and_find_verification_imap
from app.services.vault import create_server_generated_site_password

DEFAULT_HOMEPAGE_ANCHORS = [
    ("خبير سيو", 1.0),
    ("متخصص سيو", 1.0),
    ("خبير سيو في السعودية", 1.0),
    ("استشاري سيو", 1.0),
]

DEFAULT_MAPS_ANCHORS = [
    ("خبير سيو محترف", 1.0),
    ("خبير سيو معتمد", 1.0),
    ("خبير سيو متاجر الكترونية", 1.0),
    ("خبير سيو في مصر", 1.0),
    ("خبير سيو في بني سويف", 1.0),
]

DEFAULT_BIOS = [
    (
        "ecommerce",
        "خبير سيو معاذ حسن — دكتور السيو المتخصص في تطوير المتاجر الإلكترونية وزيادة المبيعات العضوية في السعودية ومصر والوطن العربي.",
    ),
    (
        "consulting",
        "أفضل استشاري تحسين محركات البحث معاذ حسن (دكتور السيو) — خبرة عملية في بناء استراتيجيات نمو حقيقية للمواقع والشركات في مصر والسعودية.",
    ),
    (
        "technical",
        "خبير سيو معاذ حسن — دكتور السيو لتدقيق السيو التقني وتحسين تصدّر العلامات التجارية في نتائج البحث وخرائط جوجل.",
    ),
]


def seed_backlink_defaults(db: Session, workspace_id: str, target_domain: str = "moaazhassan.com", maps_url: str = "") -> None:
    if db.query(BlIdentity).filter(BlIdentity.workspace_id == workspace_id).count() == 0:
        db.add(
            BlIdentity(
                workspace_id=workspace_id,
                display_name="خبير سيو معاذ حسن",
                extended_name="أفضل خبير سيو | أفضل استشاري تحسين محركات البحث",
                title="دكتور السيو",
                photo_url="",
            )
        )
    if db.query(BlTarget).filter(BlTarget.workspace_id == workspace_id, BlTarget.kind == "homepage").count() == 0:
        url = target_domain if target_domain.startswith("http") else f"https://{target_domain}"
        db.add(BlTarget(workspace_id=workspace_id, kind="homepage", url=url))
    if maps_url and db.query(BlTarget).filter(BlTarget.workspace_id == workspace_id, BlTarget.kind == "maps").count() == 0:
        db.add(BlTarget(workspace_id=workspace_id, kind="maps", url=maps_url))
    elif db.query(BlTarget).filter(BlTarget.workspace_id == workspace_id, BlTarget.kind == "maps").count() == 0:
        db.add(BlTarget(workspace_id=workspace_id, kind="maps", url="https://maps.google.com/?q=Moaaz+Hassan+SEO"))

    if db.query(BlBio).filter(BlBio.workspace_id == workspace_id).count() == 0:
        for angle, text in DEFAULT_BIOS:
            db.add(BlBio(workspace_id=workspace_id, angle=angle, text=text, times_used=0))

    if db.query(BlAnchor).filter(BlAnchor.workspace_id == workspace_id).count() == 0:
        for text, weight in DEFAULT_HOMEPAGE_ANCHORS:
            db.add(BlAnchor(workspace_id=workspace_id, target_kind="homepage", text=text, weight=weight, times_used=0))
        for text, weight in DEFAULT_MAPS_ANCHORS:
            db.add(BlAnchor(workspace_id=workspace_id, target_kind="maps", text=text, weight=weight, times_used=0))

    if db.query(BlSite).filter(BlSite.workspace_id == workspace_id).count() == 0:
        demo_sites = [
            ("gravatar.com", "https://gravatar.com/signup", "profile"),
            ("hashnode.com", "https://hashnode.com/onboard", "profile"),
            ("about.me", "https://about.me/signup", "profile"),
            ("linktr.ee", "https://linktr.ee/register", "profile"),
            ("carrd.co", "https://carrd.co/signup", "profile"),
            ("behance.net", "https://www.behance.net/signup", "portfolio"),
            ("medium.com", "https://medium.com/m/signin", "publishing"),
            ("dev.to", "https://dev.to/enter?state=new-user", "community"),
            ("crunchbase.com", "https://www.crunchbase.com/register", "directory"),
            ("producthunt.com", "https://www.producthunt.com/login", "launch"),
        ]
        for idx, (dom, url, cat) in enumerate(demo_sites, start=1):
            db.add(BlSite(workspace_id=workspace_id, source_row=idx, domain=dom, signup_url=url, category=cat, status="queued"))
    db.flush()


def pick_weighted_anchor(db: Session, workspace_id: str, target_kind: str) -> BlAnchor | None:
    anchors = (
        db.query(BlAnchor)
        .filter(BlAnchor.workspace_id == workspace_id, BlAnchor.target_kind == target_kind)
        .all()
    )
    if not anchors:
        return None
    chosen = min(anchors, key=lambda a: float(a.times_used) / max(float(a.weight), 0.05))
    chosen.times_used += 1
    db.flush()
    return chosen


def pick_least_used_bio(db: Session, workspace_id: str) -> BlBio | None:
    bios = db.query(BlBio).filter(BlBio.workspace_id == workspace_id).all()
    if not bios:
        return None
    chosen = min(bios, key=lambda b: b.times_used)
    chosen.times_used += 1
    db.flush()
    return chosen


def check_html_noindex(html: str, headers: dict[str, str] | None = None) -> bool:
    if headers:
        x_robots = (headers.get("x-robots-tag") or headers.get("X-Robots-Tag") or "").lower()
        if "noindex" in x_robots:
            return True
    soup = BeautifulSoup(html or "", "html.parser")
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        if name in {"robots", "googlebot"}:
            content = (meta.get("content") or "").lower()
            if "noindex" in content:
                return True
    return False


def inspect_live_link_html(html: str, target_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    norm_target = target_url.lower().rstrip("/").replace("https://", "").replace("http://", "")
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).lower().rstrip("/").replace("https://", "").replace("http://", "")
        if norm_target in href:
            rel_vals = a.get("rel") or []
            if isinstance(rel_vals, str):
                rel_vals = rel_vals.split()
            rel_lower = {r.lower() for r in rel_vals}
            link_type = "nofollow" if ("nofollow" in rel_lower or "ugc" in rel_lower or "sponsored" in rel_lower) else "dofollow"
            return {"found": True, "link_type": link_type, "anchor_text": a.get_text(strip=True)}
    return {"found": False, "link_type": "unknown", "anchor_text": ""}


async def check_url_noindex(url: str) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "UnifiedAgentBot/1.0"})
            return check_html_noindex(resp.text, dict(resp.headers)), None
    except Exception as exc:
        return False, type(exc).__name__


async def verify_live_link_url(profile_url: str, target_url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(profile_url, headers={"User-Agent": "UnifiedAgentBot/1.0"})
            if resp.status_code >= 400:
                return {"found": False, "link_type": "unknown", "note": f"HTTP {resp.status_code}"}
            info = inspect_live_link_html(resp.text, target_url)
            info["note"] = "verified_http" if info["found"] else "link_missing_on_profile"
            return info
    except Exception as exc:
        return {"found": False, "link_type": "unknown", "note": f"error:{type(exc).__name__}"}


class BacklinkAgent:
    key = "backlink"

    def plan(self, task_input: dict[str, Any], ctx_info: dict[str, Any]) -> list[dict[str, Any]]:
        limit = int(task_input.get("max_per_day", 10))
        return [{"step": "register_profiles", "max_per_day": limit, "target_domain": task_input.get("target_domain")}]

    def verify(self, item: dict[str, Any]) -> dict[str, Any]:
        profile_url = item.get("profile_url")
        target_url = item.get("target_url")
        if not profile_url or not target_url:
            return {"ok": False, "reason": "missing_urls"}
        if str(profile_url).startswith("mock://"):
            return {"found": True, "link_type": item.get("link_type", "dofollow"), "note": "mock_verified"}
        return {"pending_async_check": True, "profile_url": profile_url, "target_url": target_url}

    async def run(self, ctx: AgentContext, task_input: dict[str, Any]) -> dict[str, Any]:
        db = ctx.db
        ws = ctx.workspace_id
        target_domain = str(task_input.get("target_domain") or "moaazhassan.com").strip()
        maps_url = str(task_input.get("maps_url") or "https://maps.google.com/?q=Moaaz+Hassan+SEO").strip()
        max_per_day = int(task_input.get("max_per_day") or 10)
        site_filter = str(task_input.get("site_filter") or "queued")
        auth_strategy = str(task_input.get("auth_strategy") or "google_first")
        mock_mode = bool(task_input.get("mock_mode", True))
        simulate_captcha_sites = set(task_input.get("simulate_captcha_sites") or [])
        simulate_2fa_sites = set(task_input.get("simulate_2fa_sites") or [])

        seed_backlink_defaults(db, ws, target_domain=target_domain, maps_url=maps_url)
        identity = db.query(BlIdentity).filter(BlIdentity.workspace_id == ws).first()
        hp_target = db.query(BlTarget).filter(BlTarget.workspace_id == ws, BlTarget.kind == "homepage").first()
        maps_target = db.query(BlTarget).filter(BlTarget.workspace_id == ws, BlTarget.kind == "maps").first()

        # Resolve optional Mailbox or Google Account
        mailbox: Mailbox | None = None
        if task_input.get("mailbox_id"):
            mailbox = db.get(Mailbox, task_input["mailbox_id"])
        else:
            mailbox = db.query(Mailbox).filter(Mailbox.workspace_id == ws, Mailbox.status == "ok").first()

        google_acct: GoogleAccount | None = None
        if task_input.get("google_account_id"):
            google_acct = db.get(GoogleAccount, task_input["google_account_id"])
        else:
            google_acct = (
                db.query(GoogleAccount)
                .filter(GoogleAccount.workspace_id == ws, GoogleAccount.status == "active")
                .first()
            )

        statuses = ["queued"] if site_filter == "queued" else (
            ["queued", "needs_manual", "needs_captcha_manual"] if site_filter == "all_pending" else ["failed", "blocked"]
        )
        sites = (
            db.query(BlSite)
            .filter(BlSite.workspace_id == ws, BlSite.status.in_(statuses))
            .order_by(BlSite.source_row.asc())
            .limit(max_per_day)
            .all()
        )

        ctx.run.progress_total = len(sites)
        ctx.run.progress_done = 0
        ctx.run.status = "running"
        ctx.run.started_at = datetime.now(timezone.utc)
        db.commit()

        await ctx.emit(
            "info",
            "run_started",
            f"بدء تشغيل وكيل الباك لينك على {len(sites)} موقع (وضع المحاكاة: {'مفعّل' if mock_mode else 'حقيقي'}).",
            {"total": len(sites), "mock_mode": mock_mode},
        )

        counts = {
            "processed": 0,
            "live": 0,
            "pending_review": 0,
            "excluded_noindex": 0,
            "needs_manual": 0,
            "failed": 0,
        }
        consecutive_failures = 0

        for idx, site in enumerate(sites, start=1):
            ctx.check_killswitch()
            if consecutive_failures >= 3:
                ctx.run.status = "paused"
                ctx.run.error = "Auto-stopped after 3 consecutive failures."
                db.commit()
                await ctx.emit(
                    "error",
                    "auto_stopped",
                    "توقف التشغيل تلقائياً بعد 3 حالات فشل متتالية لحماية الحسابات والـ IP.",
                )
                break

            site.status = "in_progress"
            site.last_attempt_at = datetime.now(timezone.utc).isoformat()
            run_item = RunItem(
                workspace_id=ws,
                run_id=ctx.run.id,
                item_type="site",
                item_ref=site.domain,
                status="in_progress",
            )
            db.add(run_item)
            db.commit()

            try:
                # 1. Pre-check noindex
                is_noindex = False
                if mock_mode:
                    is_noindex = "linktr.ee" in site.domain.lower() or site.domain in set(task_input.get("simulate_noindex_sites") or [])
                elif site.signup_url:
                    is_noindex, _ = await check_url_noindex(site.signup_url)

                site.noindex_checked = 1
                if is_noindex:
                    site.is_noindex = 1
                    site.status = "excluded_noindex"
                    run_item.status = "excluded_noindex"
                    run_item.result = {"reason": "noindex_detected"}
                    counts["excluded_noindex"] += 1
                    counts["processed"] += 1
                    ctx.run.progress_done = idx
                    db.commit()
                    await ctx.emit("info", "site_skipped", f"تخطي {site.domain}: الصفحة تحمل noindex.")
                    continue

                # 2. Prepare identity, varied bio (worker LLM + different-provider reviewer), weighted anchors
                bio_row = pick_least_used_bio(db, ws)
                anchor_hp = pick_weighted_anchor(db, ws, "homepage")
                anchor_maps = pick_weighted_anchor(db, ws, "maps")

                bio_text = bio_row.text if bio_row else f"{identity.display_name} — {identity.title}"
                try:
                    bio_gen = ctx.llm(
                        "bio",
                        "mid",
                        json.dumps(
                            {
                                "task": "Rewrite a concise Arabic SEO bio without saying 'أقدم نفسي'",
                                "display_name": identity.display_name if identity else "خبير سيو معاذ حسن",
                                "title": identity.title if identity else "دكتور السيو",
                                "angle": bio_row.angle if bio_row else "general",
                                "site": site.domain,
                            },
                            ensure_ascii=False,
                        ),
                        json_schema={
                            "type": "object",
                            "required": ["bio"],
                            "properties": {"bio": {"type": "string"}, "angle": {"type": "string"}},
                        },
                    )
                    candidate_bio = bio_gen["output"]["bio"]
                    review = ctx.review(
                        bio_gen["provider_id"],
                        candidate_bio,
                        "Ensure Arabic bio highlights SEO expertise naturally, includes title without 'أقدم نفسي'.",
                    )
                    if review["output"].get("approved"):
                        bio_text = candidate_bio
                except Exception:
                    # Keep fallback bio row if LLM disabled/unavailable
                    pass

                # 3. Determine signup email / Google Account & server-generated password
                site_slug = site.domain.split(".")[0]
                using_google_oauth = bool(auth_strategy == "google_first" and google_acct and google_acct.status == "active")

                if using_google_oauth and google_acct is not None:
                    raw_google_email = decrypt_secret(google_acct.email_enc)
                    email_used = alias_for(raw_google_email, site_slug, google_acct.supports_plus)
                    del raw_google_email
                elif mailbox is not None:
                    email_used = alias_for(mailbox.email, site_slug, mailbox.supports_plus)
                else:
                    email_used = f"seo+{site_slug}@example.com"

                _, site_pw_enc = create_server_generated_site_password()
                username = f"moaaz_seo_{site_slug}"[:32]
                account = BlAccount(
                    workspace_id=ws,
                    site_id=site.id,
                    run_id=ctx.run.id,
                    mailbox_id=mailbox.id if mailbox else None,
                    google_account_id=google_acct.id if (using_google_oauth and google_acct) else None,
                    email_alias=email_used,
                    username=username,
                    password_enc=site_pw_enc,
                )
                db.add(account)
                db.flush()

                # Record steps into bl_steps (NEVER raw secret!)
                steps_to_record = [
                    ("goto", None, None, None, None, "signup_url"),
                    ("click", "button[data-provider='google']" if using_google_oauth else "input[type='email']", None, None, None, "google_signin" if using_google_oauth else "email_alias"),
                    ("type", "input[name='display_name']", "display_name", "form-input", None, "display_name"),
                    ("type", "textarea[name='bio']", "bio", "form-textarea", None, "bio_variant"),
                    ("submit", "button[type='submit']", "submit-btn", "btn-primary", None, None),
                ]
                now_ts = datetime.now(timezone.utc).isoformat()
                for s_idx, (act, sel, el_id, el_cls, el_txt, v_key) in enumerate(steps_to_record, start=1):
                    db.add(
                        BlStep(
                            workspace_id=ws,
                            account_id=account.id,
                            n=s_idx,
                            action=act,
                            selector_css=sel,
                            el_id=el_id,
                            el_class=el_cls,
                            el_text=el_txt,
                            value_key=v_key,
                            ok=1,
                            ts=now_ts,
                        )
                    )
                db.flush()

                # 4. Captcha Plugin Check (ManualSolver only; no automated bypass/token injection)
                solver = ctx.captcha_solver
                challenge: CaptchaChallenge | None = None
                if mock_mode:
                    if site.domain in simulate_captcha_sites:
                        challenge = solver.detect(
                            {
                                "run_id": ctx.run.id,
                                "site": site.domain,
                                "challenge_type": "recaptcha",
                                "page_url": site.signup_url or f"https://{site.domain}",
                            }
                        )
                elif get_settings().browser_worker_url:
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        resp = await client.post(
                            f"{get_settings().browser_worker_url.rstrip('/')}/sessions/run-steps",
                            json={
                                "run_id": ctx.run.id,
                                "profile_id": f"google_{google_acct.id}" if google_acct else "default",
                                "url": site.signup_url or f"https://{site.domain}",
                                "steps": [],
                            },
                        )
                        if resp.status_code == 200:
                            bw_data = resp.json()
                            if bw_data.get("status") == "awaiting_captcha_manual":
                                ch = bw_data.get("challenge") or {}
                                challenge = solver.detect(
                                    {
                                        "run_id": ctx.run.id,
                                        "site": site.domain,
                                        "challenge_type": ch.get("challenge_type", "recaptcha"),
                                        "page_url": bw_data.get("current_url", site.signup_url or ""),
                                        "blocked_reason": ch.get("reason", "captcha_manual"),
                                    }
                                )

                if challenge is not None:
                    cap_res = await pause_for_manual_captcha(
                        db,
                        workspace_id=ws,
                        user_id=ctx.user_id,
                        run=ctx.run,
                        challenge=challenge,
                        timeout_sec=int(task_input.get("captcha_timeout_sec") or get_settings().captcha_manual_timeout_sec),
                    )
                    if cap_res.status != "passed":
                        site.status = "needs_captcha_manual" if cap_res.status != "phone_verify_blocked" else "blocked"
                        site.blocked_reason = cap_res.blocked_reason
                        run_item.status = site.status
                        run_item.result = {"blocked_reason": cap_res.blocked_reason}
                        counts["needs_manual"] += 1
                        counts["processed"] += 1
                        ctx.run.progress_done = idx
                        db.commit()
                        continue

                # 5. Google 2FA / Verification Code Ask Flow if requested
                if mock_mode and site.domain in simulate_2fa_sites:
                    code_res = await ask_user_for_verification_code(
                        db,
                        workspace_id=ws,
                        user_id=ctx.user_id,
                        run=ctx.run,
                        site=site.domain,
                        google_account=google_acct,
                        code_type="google_2fa",
                        verifier_fn=lambda code: len(code.strip()) >= 6,
                        timeout_sec=int(task_input.get("verification_timeout_sec") or get_settings().verification_code_timeout_sec),
                    )
                    if code_res["status"] != "ok":
                        site.status = "needs_manual"
                        site.blocked_reason = f"2fa_{code_res['status']}"
                        run_item.status = "needs_manual"
                        run_item.result = code_res
                        counts["needs_manual"] += 1
                        counts["processed"] += 1
                        ctx.run.progress_done = idx
                        db.commit()
                        continue

                # 6. Email activation (IMAP read-only if connected and not mock)
                if not mock_mode and mailbox is not None and mailbox.status == "ok":
                    try:
                        mail_match = fetch_and_find_verification_imap(
                            db,
                            mailbox_id=mailbox.id,
                            site_domain=site.domain,
                            target_address=email_used,
                        )
                        if mail_match and mail_match.get("activation_url"):
                            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                                await client.get(mail_match["activation_url"])
                            # Drop raw link immediately
                            mail_match.clear()
                    except Exception:
                        pass

                # 7. Capture profile URL & verify live link
                profile_url = (
                    f"mock://{site.domain}/u/{username}"
                    if mock_mode
                    else f"https://{site.domain}/{username}"
                )
                account.profile_url = profile_url
                account.verified_at = datetime.now(timezone.utc).isoformat()

                if mock_mode:
                    link_type = "nofollow" if idx % 3 == 0 else "dofollow"
                    is_live = 1
                    note = "verified_mock"
                else:
                    check_res = await verify_live_link_url(profile_url, hp_target.url if hp_target else target_domain)
                    link_type = check_res["link_type"]
                    is_live = 1 if check_res["found"] else 0
                    note = check_res["note"]

                if hp_target:
                    db.add(
                        BlLink(
                            workspace_id=ws,
                            account_id=account.id,
                            target_id=hp_target.id,
                            anchor_id=anchor_hp.id if anchor_hp else None,
                            link_type=link_type,
                            live=is_live,
                            last_checked_at=datetime.now(timezone.utc).isoformat(),
                            check_note=note,
                        )
                    )
                if maps_target and anchor_maps:
                    db.add(
                        BlLink(
                            workspace_id=ws,
                            account_id=account.id,
                            target_id=maps_target.id,
                            anchor_id=anchor_maps.id,
                            link_type=link_type,
                            live=is_live,
                            last_checked_at=datetime.now(timezone.utc).isoformat(),
                            check_note=note,
                        )
                    )

                site.status = "live" if is_live else "pending_review"
                run_item.status = site.status
                run_item.result = {
                    "profile_url": profile_url,
                    "link_type": link_type,
                    "anchor": anchor_hp.text if anchor_hp else "",
                    "bio_preview": bio_text[:60],
                }
                if is_live:
                    counts["live"] += 1
                else:
                    counts["pending_review"] += 1
                counts["processed"] += 1
                consecutive_failures = 0
                ctx.run.progress_done = idx
                db.commit()

                await ctx.emit(
                    "info",
                    "site_done",
                    f"{site.domain} -> {site.status} ({link_type})",
                    {"site": site.domain, "status": site.status, "link_type": link_type, "profile_url": profile_url},
                )
                await asyncio.sleep(0.01 if mock_mode else random.uniform(0.8, 2.2))

            except KillSwitchTriggeredError:
                raise
            except Exception as exc:
                consecutive_failures += 1
                site.status = "failed"
                site.blocked_reason = type(exc).__name__
                run_item.status = "failed"
                run_item.result = {"error": type(exc).__name__}
                counts["failed"] += 1
                counts["processed"] += 1
                ctx.run.progress_done = idx
                ctx.run.consecutive_failures = consecutive_failures
                db.commit()
                await ctx.emit("error", "site_failed", f"فشل في {site.domain}: {type(exc).__name__}")

        if ctx.run.status == "running":
            ctx.run.status = "done"
            ctx.run.finished_at = datetime.now(timezone.utc)
        ctx.run.summary_json = counts
        db.commit()
        await ctx.emit("info", "run_completed", "اكتمل تشغيل وكيل الباك لينك.", counts)
        return counts
