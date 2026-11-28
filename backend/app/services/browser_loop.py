"""Flagship 9-Step Backlink Computer-Use Loop (§1E) callable from Chat or Agents."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from app.agents.backlink.agent import check_url_noindex, seed_backlink_defaults, verify_live_link_url
from app.captcha.solver import CaptchaChallenge, CaptchaResult, dispatch_solve, get_captcha_solver
from app.config import get_settings
from app.db import new_uuid
from app.models.agents import Run
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
from app.security.sanitizer import discard_ephemeral_secret, register_ephemeral_secret, scrub_text
from app.services.accounts_sheet import record_account_in_spreadsheet
from app.services.browser_agent import (
    BrowserWorkerClient,
    _extract_json_obj,
    check_gmail_or_mailbox_activation,
)
from app.services.browser_worker_manager import resolve_browser_worker_url
from app.services.captcha_inject import inject_token_via_worker
from app.services.llm_router import LLMRouter
from app.services.runtime_checks import get_runtime_readiness, validate_real_email
from app.services.session_log import append_turn
from app.services.session_replay import replay_backlink_session, save_backlink_session
from app.services.vault import create_server_generated_site_password


async def run_flagship_backlink_loop(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    domain: str,
    email: str,
    run: Run | None = None,
    mock_mode: bool = True,
    simulate_captcha: bool = False,
    simulate_2fa: bool = False,
    target_url: str | None = None,
    google_account_id: str | None = None,
    solver: Any | None = None,
    available_elements: list[dict[str, Any]] | None = None,
    proxy: str | None = None,
    thread_id: str | None = None,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Execute the 9-step Flagship Backlink Loop cleanly (open -> verify -> report)."""
    seed_backlink_defaults(db, workspace_id)
    # Default the browser proxy from .env when the caller (e.g. chat) did
    # not pass one — otherwise the browser runs direct and any solver
    # token solved via the proxy IP is rejected (token/IP mismatch).
    proxy = (proxy or "").strip() or (get_settings().residential_proxy or "").strip() or None
    clean_domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if (target_url or "").startswith(("http://127.0.0.1", "http://localhost")) or clean_domain.startswith(("127.0.0.1", "localhost")):
        proxy = None
    stages: list[dict[str, Any]] = []
    screenshots: list[str] = []

    def _stage(num: int, name: str, status: str, detail: str, *, screenshot_url: str | None = None) -> None:
        clean_detail = scrub_text(detail)
        if mock_mode and not clean_detail.startswith("محاكاة"):
            clean_detail = f"محاكاة — {clean_detail}"
        item: dict[str, Any] = {"step": num, "name": name, "status": status, "detail": clean_detail}
        if screenshot_url:
            item["screenshot_url"] = screenshot_url
            if screenshot_url not in screenshots:
                screenshots.append(screenshot_url)
        stages.append(item)
        if thread_id:
            append_turn(
                thread_id,
                {"type": "backlink_step", "step": num, "name": name, "status": status, "screenshot_url": screenshot_url},
            )

    ok_email, email_err = validate_real_email(email)
    if not ok_email:
        return {
            "status": "invalid_email",
            "failed_step": 0,
            "domain": clean_domain,
            "is_mock": bool(mock_mode),
            "report_ar": email_err or "يرجى إدخال بريد إلكتروني حقيقي قبل التشغيل.",
            "stages": stages,
            "screenshots": screenshots,
        }

    if not mock_mode:
        readiness = get_runtime_readiness(db, workspace_id)
        if not readiness.get("real_chat_ready"):
            missing_list = "\n- ".join(readiness["missing_for_real_backlink"])
            return {
                "status": "missing_requirements",
                "failed_step": 0,
                "domain": clean_domain,
                "is_mock": False,
                "missing": readiness["missing_keys"],
                "runtime_status": readiness,
                "report_ar": f"لا يمكن بدء التشغيل الحقيقي قبل استكمال المتطلبات التالية:\n- {missing_list}",
                "stages": stages,
                "screenshots": screenshots,
            }

    # 1. OPEN
    site = (
        db.query(BlSite)
        .filter(BlSite.workspace_id == workspace_id, BlSite.domain == clean_domain)
        .first()
    )
    is_new_site = site is None
    if not site:
        site = BlSite(
            workspace_id=workspace_id,
            domain=clean_domain,
            signup_url=f"https://{clean_domain}",
            category="profile",
            status="queued",
        )
        db.add(site)
        db.flush()

    target_open_url = target_url or site.signup_url or f"https://{clean_domain}"
    if mock_mode:
        noindex_hit = False
    else:
        noindex_hit, _ = await check_url_noindex(target_open_url)
    site.noindex_checked = 1
    site.is_noindex = 1 if noindex_hit else 0
    if noindex_hit:
        site.status = "excluded_noindex"
        site.blocked_reason = "noindex"
        db.commit()
        _stage(1, "open", "stopped", "تم استبعاد الموقع لوجود وسم noindex")
        return {
            "status": "blocked",
            "failed_step": 1,
            "domain": clean_domain,
            "is_mock": bool(mock_mode),
            "report_ar": f"فشل في خطوة 1: الموقع {clean_domain} يحتوي على noindex وتم استبعاده.",
            "stages": stages,
            "screenshots": screenshots,
        }

    replay_info = replay_backlink_session(
        db, workspace_id=workspace_id, domain=clean_domain, available_elements=available_elements
    )

    if not mock_mode:
        return await _execute_real_backlink_browser_flow(
            db,
            workspace_id=workspace_id,
            clean_domain=clean_domain,
            open_url=target_open_url,
            is_new_site=is_new_site,
            site=site,
            email=email,
            run=run,
            simulate_captcha=simulate_captcha,
            simulate_2fa=simulate_2fa,
            target_url=target_url,
            google_account_id=google_account_id,
            solver=solver,
            proxy=proxy,
            replay_info=replay_info,
            stages=stages,
            screenshots=screenshots,
            _stage=_stage,
            thread_id=thread_id,
            on_progress=on_progress,
        )

    _stage(
        1,
        "open",
        "ok",
        f"فتح {clean_domain}" + (f" (replay v{replay_info['version']})" if replay_info["found"] else ""),
    )

    # 2. FIND SIGNUP
    g_acct = (
        db.get(GoogleAccount, google_account_id)
        if google_account_id
        else db.query(GoogleAccount)
        .filter(GoogleAccount.workspace_id == workspace_id, GoogleAccount.status == "active")
        .first()
    )
    signup_method = "google_signin" if (g_acct and clean_domain.endswith(".com") and False) else "email_form"
    _stage(2, "find_signup", "ok", f"اكتشاف مسار التسجيل ({signup_method})")

    # 3. REGISTER (Vault password ONLY — never stored plaintext in chat/steps/logs)
    raw_pw, site_pw_enc = create_server_generated_site_password()
    username = f"moaaz_seo_{clean_domain.split('.')[0][:10]}"
    account = BlAccount(
        workspace_id=workspace_id,
        site_id=site.id,
        run_id=run.id if run else None,
        google_account_id=g_acct.id if g_acct else None,
        email_alias=email.strip(),
        username=username,
        password_enc=site_pw_enc,
    )
    db.add(account)
    db.flush()
    record_account_in_spreadsheet(
        domain=clean_domain,
        email_username=email.strip(),
        password=raw_pw,
        profile_url="",
        notes="simulation" if mock_mode else "live",
    )
    raw_pw = ""
    del raw_pw

    steps_payload = [
        {"n": 1, "action": "goto", "selector_css": None, "value_key": "signup_url", "ok": 1},
        {"n": 2, "action": "type", "selector_css": "input[type='email']", "el_id": "email", "value_key": "email_alias", "ok": 1},
        {"n": 3, "action": "type", "selector_css": "input[type='password']", "el_id": "password", "value_key": "site_password_vault", "ok": 1},
        {"n": 4, "action": "type", "selector_css": "input[name='website']", "el_id": "website", "value_key": "homepage_url", "ok": 1},
        {"n": 5, "action": "submit", "selector_css": "button[type='submit']", "el_id": "submit-btn", "el_text": "Register", "ok": 1},
    ]
    now_iso = datetime.now(timezone.utc).isoformat()
    for st in steps_payload:
        db.add(
            BlStep(
                workspace_id=workspace_id,
                account_id=account.id,
                n=st["n"],
                action=st["action"],
                selector_css=st.get("selector_css"),
                el_id=st.get("el_id"),
                el_class=st.get("el_class"),
                el_text=st.get("el_text"),
                value_key=st.get("value_key"),
                ok=st["ok"],
                ts=now_iso,
            )
        )
    save_backlink_session(db, workspace_id=workspace_id, domain=clean_domain, steps=steps_payload, site_id=site.id)
    _stage(3, "register", "ok", "تعبئة بيانات التسجيل بكلمة مرور من الخزنة المشفرة")

    # 4. CAPTCHA
    active_solver = solver or get_captcha_solver()
    if simulate_captcha:
        challenge = active_solver.detect(
            {
                "run_id": run.id if run else "",
                "site": clean_domain,
                "challenge_type": "recaptcha",
                "page_url": site.signup_url or f"https://{clean_domain}",
                "proxy": proxy,
            }
        )
        if challenge is None:
            challenge = CaptchaChallenge(
                run_id=run.id if run else "",
                site=clean_domain,
                challenge_type="recaptcha",
                page_url=site.signup_url or f"https://{clean_domain}",
                proxy=proxy,
            )
        cap_res = await dispatch_solve(active_solver, challenge)
        # --- Captcha plugin pack: inject the token into the live session now ---
        # A "passed" solve only means a token was obtained. Without injection
        # in the same session/IP the token is worthless, so a failed injection
        # falls back to manual like any other failed solve.
        if cap_res.status == "passed":
            _token = (cap_res.instructions or {}).get("token") or ""
            _field = (cap_res.instructions or {}).get("token_field") or "g-recaptcha-response"
            if _token and get_settings().browser_worker_url:
                _inj = await inject_token_via_worker(
                    worker_url=get_settings().browser_worker_url,
                    run_id=run.id if run else "",
                    profile_id="default",
                    url=challenge.page_url,
                    token=_token,
                    token_field=_field,
                    challenge_type=challenge.challenge_type,
                    proxy=proxy,
                )
                _token = ""  # wipe immediately after use
                cap_res.instructions = {}
                if _inj != "completed":
                    cap_res = CaptchaResult(
                        status="skipped", method=cap_res.method,
                        blocked_reason=f"captcha_inject_{_inj}",
                        duration_ms=cap_res.duration_ms, cost_usd=0.0,
                    )
        if cap_res.status != "passed":
            site.status = "needs_captcha_manual"
            site.blocked_reason = cap_res.blocked_reason or "captcha_manual"
            db.commit()
            _stage(4, "captcha", "needs_user", "الكابتشا تتطلب حلاً يدوياً عبر noVNC")
            return {
                "status": "awaiting_captcha_manual",
                "failed_step": 4,
                "card_type": "captcha_manual",
                "domain": clean_domain,
                "report_ar": "فشل في خطوة 4: الكابتشا محتاجة حل يدوي — اضغط Solve manually عبر noVNC.",
                "stages": stages,
                "replay": replay_info,
            }
        _stage(4, "captcha", "ok", f"تجاوز التحدي عبر {cap_res.method}")
    else:
        _stage(4, "captcha", "ok", "لا يوجد تحدي كابتشا ظاهر")

    # 5. EMAIL ACTIVATION / 2FA
    if simulate_2fa:
        site.status = "needs_manual"
        site.blocked_reason = "2fa_required"
        db.commit()
        _stage(5, "email_activation", "needs_user", "مطلوب كود تحقق ثنائي من المالك")
        return {
            "status": "awaiting_verification_code",
            "failed_step": 5,
            "card_type": "verification_code",
            "domain": clean_domain,
            "report_ar": "فشل في خطوة 5: الحساب يطلب كود التحقق (2FA) — أدخل الكود في البطاقة لإكمال التفعيل.",
            "stages": stages,
            "replay": replay_info,
        }
    _stage(5, "email_activation", "ok", "تم رصد/محاكاة تفعيل الإيميل بنجاح")

    # 6. LOG IN + EXPLORE
    _stage(6, "login_explore", "ok", "الدخول واكتشاف حقول الملف الشخصي (Website / Bio)")

    # 7. PLACE BACKLINK
    identity = db.query(BlIdentity).filter(BlIdentity.workspace_id == workspace_id).first()
    hp_target = db.query(BlTarget).filter(BlTarget.workspace_id == workspace_id, BlTarget.kind == "homepage").first()
    anchor_hp = db.query(BlAnchor).filter(BlAnchor.workspace_id == workspace_id, BlAnchor.target_kind == "homepage").first()
    bio_row = db.query(BlBio).filter(BlBio.workspace_id == workspace_id).first()
    if bio_row:
        bio_row.times_used += 1
    if anchor_hp:
        anchor_hp.times_used += 1

    eff_target_url = (target_url or (hp_target.url if hp_target else None) or "https://moaazhassan.com")
    _stage(
        7,
        "place_backlink",
        "ok",
        f"إضافة رابط {eff_target_url} بالأنكر '{anchor_hp.text if anchor_hp else 'خبير سيو'}' وهوية {identity.display_name if identity else 'خبير سيو معاذ حسن'}",
    )

    # 8. VERIFY PUBLIC PROFILE
    profile_url = f"mock://{clean_domain}/u/{username}" if mock_mode else f"https://{clean_domain}/{username}"
    account.profile_url = profile_url
    account.verified_at = now_iso
    record_account_in_spreadsheet(
        domain=clean_domain,
        email_username=email.strip(),
        password="",
        profile_url=profile_url,
        notes="simulation" if mock_mode else "live",
    )
    if mock_mode:
        link_type = "dofollow"
        is_live = 1
        check_note = "verified_mock"
    else:
        chk = await verify_live_link_url(profile_url, eff_target_url)
        link_type = chk["link_type"]
        is_live = 1 if chk["found"] else 0
        check_note = chk["note"]

    if hp_target:
        db.add(
            BlLink(
                workspace_id=workspace_id,
                account_id=account.id,
                target_id=hp_target.id,
                anchor_id=anchor_hp.id if anchor_hp else None,
                link_type=link_type,
                live=is_live,
                last_checked_at=now_iso,
                check_note=check_note,
            )
        )
    site.status = "live" if is_live else "not_found_on_profile"
    db.commit()

    if not is_live:
        _stage(8, "verify", "failed", "الرابط غير ظاهر في الصفحة العامة (not_found_on_profile)")
        return {
            "status": "not_found_on_profile",
            "failed_step": 8,
            "domain": clean_domain,
            "is_mock": bool(mock_mode),
            "profile_url": profile_url,
            "report_ar": f"فشل في خطوة 8: الحساب مسجل لكن الرابط غير ظاهر في الصفحة العامة ({profile_url}).",
            "stages": stages,
            "replay": replay_info,
        }

    if mock_mode:
        _stage(8, "verify", "ok", f"فحص تجريبي فقط ({profile_url} — محاكاة، ليس رابطاً حياً)")
        report_ar = (
            f"محاكاة — لو كان هذا تشغيلاً حقيقياً على موقع {clean_domain}: سيتم تسجيل الحساب، "
            f"وتفعيل البريد، وفحص الرابط ({profile_url} — محاكاة، ليس رابطاً حياً). لم يتم إنشاء حساب حقيقي ولا رابط حي."
        )
    else:
        _stage(8, "verify", "ok", f"تم التحقق من الصفحة العامة ({profile_url}) — {link_type}")
        report_ar = (
            f"تم ✅ — موقع {clean_domain}: حساب مسجل، إيميل مفعل، الباك لينك live ({link_type}) — {profile_url}"
        )

    _stage(9, "report", "ok", report_ar)
    return {
        "status": "done",
        "domain": clean_domain,
        "account_id": account.id,
        "is_mock": bool(mock_mode),
        "simulated": bool(mock_mode),
        "profile_url": profile_url if not mock_mode else f"{profile_url} (محاكاة، ليس رابطاً حياً)",
        "link_type": link_type,
        "report_ar": report_ar,
        "stages": stages,
        "screenshots": screenshots,
        "replay": replay_info,
    }


async def _execute_real_backlink_browser_flow(
    db: Session,
    *,
    workspace_id: str,
    clean_domain: str,
    open_url: str,
    is_new_site: bool,
    site: BlSite,
    email: str,
    run: Run | None,
    simulate_captcha: bool,
    simulate_2fa: bool,
    target_url: str | None,
    google_account_id: str | None,
    solver: Any | None,
    proxy: str | None,
    replay_info: dict[str, Any],
    stages: list[dict[str, Any]],
    screenshots: list[str],
    _stage: Callable[..., None],
    thread_id: str | None,
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> dict[str, Any]:
    """Run genuine Playwright interactive registration, Gmail activation, link placement & live verify."""
    g_acct = (
        db.get(GoogleAccount, google_account_id)
        if google_account_id
        else db.query(GoogleAccount)
        .filter(GoogleAccount.workspace_id == workspace_id, GoogleAccount.status == "active")
        .first()
    )
    identity = db.query(BlIdentity).filter(BlIdentity.workspace_id == workspace_id).first()
    hp_target = db.query(BlTarget).filter(BlTarget.workspace_id == workspace_id, BlTarget.kind == "homepage").first()
    anchor_hp = db.query(BlAnchor).filter(BlAnchor.workspace_id == workspace_id, BlAnchor.target_kind == "homepage").first()
    bio_row = db.query(BlBio).filter(BlBio.workspace_id == workspace_id).first()
    eff_target_url = (target_url or (hp_target.url if hp_target else None) or "https://moaazhassan.com")

    raw_pw, site_pw_enc = create_server_generated_site_password()
    register_ephemeral_secret(raw_pw)
    username = f"moaaz_seo_{clean_domain.split('.')[0][:10]}_{new_uuid()[:4]}"
    account = BlAccount(
        workspace_id=workspace_id,
        site_id=site.id,
        run_id=run.id if run else None,
        google_account_id=g_acct.id if g_acct else None,
        email_alias=email.strip(),
        username=username,
        password_enc=site_pw_enc,
    )
    db.add(account)
    db.flush()

    substitutions = {
        "__EMAIL__": email.strip(),
        "__PASSWORD__": raw_pw,
        "__USERNAME__": username,
        "__TARGET_URL__": eff_target_url,
        "__DISPLAY_NAME__": identity.display_name if identity else "خبير سيو معاذ حسن",
        "__BIO__": bio_row.text if bio_row else "خبير سيو",
    }

    worker = BrowserWorkerClient(resolve_browser_worker_url())
    session_id = f"bl_{(thread_id or account.id)[:12]}_{new_uuid()[:5]}"
    recorded_steps: list[dict[str, Any]] = []
    last_url = open_url
    now_iso = datetime.now(timezone.utc).isoformat()
    active_solver = solver or get_captcha_solver()

    async def _emit(step_num: int, name: str, status: str, detail: str, snap: dict[str, Any] | None = None) -> None:
        nonlocal last_url
        if snap and snap.get("url"):
            last_url = str(snap["url"])
        sc = snap.get("screenshot_url") if snap else None
        _stage(step_num, name, status, detail, screenshot_url=sc)
        if on_progress:
            await on_progress({"step": step_num, "name": name, "status": status, "detail": detail, "screenshot_url": sc})

    def _add_bl_step(n: int, action: str, selector_css: str | None = None, value_key: str | None = None, ok: int = 1) -> None:
        recorded_steps.append({"n": n, "action": action, "selector_css": selector_css, "value_key": value_key, "ok": ok})
        db.add(
            BlStep(
                workspace_id=workspace_id,
                account_id=account.id,
                n=n,
                action=action,
                selector_css=selector_css,
                value_key=value_key,
                ok=ok,
                ts=now_iso,
            )
        )

    try:
        # Step 1: Open actual site in Playwright
        try:
            snap = await worker.open(session_id=session_id, url=open_url, proxy=proxy)
        except Exception:
            if not is_new_site and open_url != f"https://{clean_domain}":
                snap = await worker.open(session_id=session_id, url=f"https://{clean_domain}", proxy=proxy)
            else:
                raise
        _add_bl_step(1, "goto", value_key="signup_url", ok=1)
        await _emit(1, "open", "ok", f"فتح {snap.get('url')}", snap)

        if simulate_2fa:
            site.status = "needs_manual"
            site.blocked_reason = "2fa_required"
            db.commit()
            await _emit(5, "email_activation", "needs_user", "مطلوب كود تحقق ثنائي من المالك", snap)
            return {
                "status": "awaiting_verification_code",
                "failed_step": 5,
                "card_type": "verification_code",
                "domain": clean_domain,
                "is_mock": False,
                "stages": stages,
                "screenshots": screenshots,
                "replay": replay_info,
                "report_ar": "فشل في خطوة 5: الحساب يطلب كود التحقق (2FA) — أدخل الكود في البطاقة لإكمال التفعيل.",
            }

        # Handle immediate CAPTCHA if present
        if snap.get("challenge") or simulate_captcha:
            chal_info = snap.get("challenge") or {"challenge_type": "recaptcha"}
            ch = CaptchaChallenge(
                run_id=run.id if run else "",
                site=clean_domain,
                challenge_type=str(chal_info.get("challenge_type") or "recaptcha"),
                site_key=chal_info.get("site_key"),
                page_url=str(snap.get("url") or open_url),
                proxy=proxy,
            )
            cap_res = await dispatch_solve(active_solver, ch)
            if cap_res.status == "passed":
                tok = (cap_res.instructions or {}).get("token") or ""
                fld = (cap_res.instructions or {}).get("token_field") or "g-recaptcha-response"
                if tok and resolve_browser_worker_url():
                    inj = await inject_token_via_worker(
                        worker_url=resolve_browser_worker_url(),
                        run_id=run.id if run else "",
                        profile_id="default",
                        url=ch.page_url,
                        token=tok,
                        token_field=fld,
                        challenge_type=ch.challenge_type,
                        proxy=proxy,
                    )
                    tok = ""
                    cap_res.instructions = {}
                    if inj != "completed":
                        cap_res = CaptchaResult(status="skipped", method=cap_res.method, blocked_reason=f"captcha_inject_{inj}", duration_ms=cap_res.duration_ms, cost_usd=0.0)
            if cap_res.status != "passed":
                site.status = "needs_captcha_manual"
                site.blocked_reason = cap_res.blocked_reason or "captcha_manual"
                db.commit()
                await _emit(4, "captcha", "needs_user", "الكابتشا تتطلب حلاً يدوياً عبر noVNC", snap)
                return {
                    "status": "awaiting_captcha_manual",
                    "failed_step": 4,
                    "card_type": "captcha_manual",
                    "domain": clean_domain,
                    "is_mock": False,
                    "current_url": snap.get("url"),
                    "screenshots": screenshots,
                    "stages": stages,
                    "replay": replay_info,
                    "report_ar": f"توقف صادق في خطوة 4 ({clean_domain}): الموقع يطلب كابتشا ({ch.challenge_type}) — افتح noVNC لحلها أو فعّل مزود الحل في الإعدادات.",
                }

        # Step 2-7: LLM-driven real Playwright interaction with Blind-Fill placeholders
        llm = LLMRouter(db, workspace_id)
        sys_prompt = (
            "You automate website registration and profile link placement inside a REAL Playwright Chromium browser.\n"
            "Available tools (respond ONLY in JSON {\"tool\": ..., \"args\": {...}}):\n"
            "- browser_open: {\"url\": \"...\"}\n"
            "- browser_click: {\"selector\": \"css\", \"text\": \"button/link text\"}\n"
            "- browser_type: {\"selector\": \"css\", \"label\": \"label\", \"text\": \"use __EMAIL__, __PASSWORD__, __USERNAME__, __TARGET_URL__, __DISPLAY_NAME__, __BIO__\", \"press_enter\": false, \"submit_selector\": \"optional css\", \"secret\": true/false}\n"
            "- browser_scroll: {\"direction\": \"down\"}\n"
            "- gmail_check_activation: {\"site_domain\": \"" + clean_domain + "\"}\n"
            "- finish_backlink: {\"profile_url\": \"https://exact-public-profile-url\"}\n"
            "- blocked: {\"reason\": \"exact honest reason\", \"needs\": \"captcha\"|\"email_code\"|\"manual\"}\n"
            "NEVER fabricate profile_url if registration/placement did not complete in the live browser."
        )

        profile_url: str | None = None
        blocked_reason: str | None = None
        blocked_needs: str | None = None
        step_n = 2

        for _ in range(16):
            obs = {
                "url": snap.get("url"),
                "title": snap.get("title"),
                "visible_text": str(snap.get("visible_text") or "")[:1800],
                "links": (snap.get("links") or [])[:15],
                "inputs": (snap.get("inputs") or [])[:15],
                "buttons": (snap.get("buttons") or [])[:12],
                "challenge": snap.get("challenge"),
                "target_link": eff_target_url,
            }
            try:
                res = llm.route(
                    task_type="backlink_browser",
                    tier="cheap",
                    prompt=f"Current page DOM JSON:\n{json.dumps(obs, ensure_ascii=False)}\nChoose the next single JSON tool action.",
                    system_prompt=sys_prompt,
                    use_cache=False,
                    allow_mock_fallback=False,
                )
                cmd = _extract_json_obj(str(res.get("raw_text") or ""))
            except Exception as exc:
                blocked_reason = f"llm_unavailable:{type(exc).__name__}"
                break

            if not cmd:
                blocked_reason = "no_tool_decision"
                break

            t_name = str(cmd.get("tool") or "").strip()
            t_args = dict(cmd.get("args") or {})
            if t_name == "finish_backlink" and t_args.get("profile_url"):
                profile_url = str(t_args["profile_url"]).strip()
                break
            if t_name == "blocked":
                blocked_reason = str(t_args.get("reason") or "site_blocked_manual_required")
                blocked_needs = str(t_args.get("needs") or "manual")
                break

            if t_name == "browser_open" and t_args.get("url"):
                snap = await worker.open(session_id=session_id, url=str(t_args["url"]), proxy=proxy)
                _add_bl_step(step_n, "goto", value_key="nav_url")
                await _emit(min(step_n, 7), "navigate", "ok", f"الانتقال إلى {snap.get('url')}", snap)
            elif t_name == "browser_click":
                snap = await worker.click(session_id=session_id, selector=t_args.get("selector"), text=t_args.get("text"))
                _add_bl_step(step_n, "click", selector_css=t_args.get("selector"))
                await _emit(min(step_n, 7), "click", "ok", f"نقر على {t_args.get('text') or t_args.get('selector')}", snap)
            elif t_name == "browser_type":
                raw_txt = str(t_args.get("text") or "")
                is_sec = bool(t_args.get("secret")) or ("__PASSWORD__" in raw_txt)
                vkey = "site_password_vault" if "__PASSWORD__" in raw_txt else ("email_alias" if "__EMAIL__" in raw_txt else None)
                eff_txt = raw_txt
                for k, v in substitutions.items():
                    eff_txt = eff_txt.replace(k, v)
                snap = await worker.type(
                    session_id=session_id,
                    selector=t_args.get("selector"),
                    label=t_args.get("label"),
                    text=eff_txt,
                    press_enter=bool(t_args.get("press_enter", False)),
                    submit_selector=t_args.get("submit_selector"),
                    secret=is_sec,
                )
                del eff_txt
                _add_bl_step(step_n, "type", selector_css=t_args.get("selector"), value_key=vkey)
                await _emit(min(step_n, 7), "type", "ok", f"تعبئة الحقل ({vkey or t_args.get('selector') or 'input'})", snap)
            elif t_name == "browser_scroll":
                snap = await worker.scroll(session_id=session_id, direction=str(t_args.get("direction") or "down"))
                _add_bl_step(step_n, "scroll")
            elif t_name == "gmail_check_activation":
                act = check_gmail_or_mailbox_activation(db, workspace_id=workspace_id, site_domain=clean_domain, target_email=email.strip())
                if act and act.get("activation_url"):
                    act_url = str(act["activation_url"])
                    try:
                        snap = await worker.open(session_id=session_id, url=act_url, proxy=proxy)
                        _add_bl_step(5, "activate_email_link", value_key="ephemeral_activation_url")
                        await _emit(5, "email_activation", "ok", "تم تفعيل الحساب عبر البريد في نفس الجلسة", snap)
                    finally:
                        discard_ephemeral_secret(act_url)
                        if act.get("email_code"):
                            discard_ephemeral_secret(str(act["email_code"]))
                elif act and act.get("email_code") and t_args.get("code_selector"):
                    code_val = str(act["email_code"])
                    try:
                        snap = await worker.type(session_id=session_id, selector=str(t_args["code_selector"]), text=code_val, press_enter=True, secret=True)
                        _add_bl_step(5, "enter_email_code", selector_css=str(t_args["code_selector"]), value_key="ephemeral_code")
                        await _emit(5, "email_activation", "ok", "تم إدخال كود التفعيل من البريد وحذفه من الذاكرة", snap)
                    finally:
                        discard_ephemeral_secret(code_val)
                else:
                    blocked_needs = "email_code"
                    blocked_reason = "يتطلب الموقع تفعيل البريد الإلكتروني ولا توجد رسالة تفعيل متاحة من Gmail/IMAP حالياً."
                    break
            else:
                break

            step_n += 1
            if snap.get("challenge"):
                blocked_needs = "captcha"
                blocked_reason = f"ظهر تحدي كابتشا ({snap['challenge'].get('challenge_type')}) أثناء التنفيذ."
                break

        record_account_in_spreadsheet(
            domain=clean_domain,
            email_username=email.strip(),
            password=raw_pw,
            profile_url=profile_url or "",
            notes="live" if profile_url else f"stopped:{blocked_needs or blocked_reason or 'incomplete'}",
        )
        save_backlink_session(db, workspace_id=workspace_id, domain=clean_domain, steps=recorded_steps, site_id=site.id)

        if not profile_url:
            if blocked_needs == "captcha":
                site.status = "needs_captcha_manual"
                site.blocked_reason = "captcha_manual"
                db.commit()
                return {
                    "status": "awaiting_captcha_manual",
                    "failed_step": 4,
                    "card_type": "captcha_manual",
                    "domain": clean_domain,
                    "is_mock": False,
                    "current_url": snap.get("url"),
                    "screenshots": screenshots,
                    "stages": stages,
                    "replay": replay_info,
                    "report_ar": f"توقف صادق في {clean_domain}: {blocked_reason}",
                }
            if blocked_needs == "email_code":
                site.status = "needs_manual"
                site.blocked_reason = "email_verification_required"
                db.commit()
                return {
                    "status": "awaiting_verification_code",
                    "failed_step": 5,
                    "card_type": "verification_code",
                    "domain": clean_domain,
                    "is_mock": False,
                    "current_url": snap.get("url"),
                    "screenshots": screenshots,
                    "stages": stages,
                    "replay": replay_info,
                    "report_ar": f"توقف صادق في خطوة 5 ({clean_domain}): {blocked_reason}",
                }
            site.status = "needs_manual"
            site.blocked_reason = (blocked_reason or "incomplete_live_signup")[:120]
            db.commit()
            return {
                "status": "blocked",
                "failed_step": len(stages),
                "domain": clean_domain,
                "is_mock": False,
                "current_url": snap.get("url"),
                "screenshots": screenshots,
                "stages": stages,
                "replay": replay_info,
                "report_ar": (
                    f"⛔ **توقف صادق أثناء التشغيل الحقيقي على {clean_domain}:**\n"
                    f"- آخر صفحة: {snap.get('url')}\n"
                    f"- السبب: {scrub_text(blocked_reason or 'الموقع يتطلب خطوة تسجيل أو تحقق يدوية إضافية')}\n"
                    "- لم يتم افتراض أو اختلاق أي رابط بروفايل."
                ),
            }

        # Step 8: Verify LIVE on public profile using Playwright + HTTP check
        prof_snap = await worker.open(session_id=session_id, url=profile_url, proxy=proxy)
        await _emit(8, "verify", "ok", f"فتح الصفحة العامة للتحقق: {profile_url}", prof_snap)
        chk = await verify_live_link_url(profile_url, eff_target_url)
        is_live = 1 if chk["found"] else 0
        link_type = chk["link_type"]
        check_note = chk["note"]
        if not is_live:
            dom_links = [str(l.get("href") or "").lower() for l in (prof_snap.get("links") or [])]
            norm_tgt = eff_target_url.lower().rstrip("/").replace("https://", "").replace("http://", "")
            if any(norm_tgt in href for href in dom_links):
                is_live = 1
                link_type = "dofollow"
                check_note = "verified_playwright_dom"

        account.profile_url = profile_url
        account.verified_at = now_iso
        if bio_row:
            bio_row.times_used += 1
        if anchor_hp:
            anchor_hp.times_used += 1
        if hp_target:
            db.add(
                BlLink(
                    workspace_id=workspace_id,
                    account_id=account.id,
                    target_id=hp_target.id,
                    anchor_id=anchor_hp.id if anchor_hp else None,
                    link_type=link_type,
                    live=is_live,
                    last_checked_at=now_iso,
                    check_note=check_note,
                )
            )
        site.status = "live" if is_live else "not_found_on_profile"
        db.commit()
        record_account_in_spreadsheet(
            domain=clean_domain,
            email_username=email.strip(),
            password=raw_pw,
            profile_url=profile_url,
            notes="live" if is_live else "not_found_on_profile",
        )

        if not is_live:
            return {
                "status": "not_found_on_profile",
                "failed_step": 8,
                "domain": clean_domain,
                "is_mock": False,
                "profile_url": profile_url,
                "screenshots": screenshots,
                "stages": stages,
                "replay": replay_info,
                "report_ar": f"فشل في خطوة 8: الحساب مسجل لكن الرابط غير ظاهر في الصفحة العامة ({profile_url}).",
            }

        report_ar = f"تم ✅ — موقع {clean_domain}: حساب مسجل، إيميل مفعل، الباك لينك live ({link_type}) — {profile_url}"
        await _emit(9, "report", "ok", report_ar, prof_snap)
        return {
            "status": "done",
            "domain": clean_domain,
            "account_id": account.id,
            "is_mock": False,
            "simulated": False,
            "profile_url": profile_url,
            "link_type": link_type,
            "report_ar": report_ar,
            "stages": stages,
            "screenshots": screenshots,
            "replay": replay_info,
        }
    except Exception as exc:
        safe_err = scrub_text(f"{type(exc).__name__}: {exc}")[:220]
        try:
            site.status = "needs_manual"
            site.blocked_reason = safe_err[:180]
            db.commit()
        except Exception:
            db.rollback()
        fail_step = max(1, len(stages) + 1)
        _stage(fail_step, "browser_error", "failed", safe_err)
        return {
            "status": "blocked",
            "failed_step": fail_step,
            "domain": clean_domain,
            "is_mock": False,
            "simulated": False,
            "current_url": last_url,
            "screenshots": screenshots,
            "stages": stages,
            "replay": replay_info,
            "report_ar": (
                f"⛔ **توقف صادق أثناء التصفح الحقيقي على {clean_domain}:**\n"
                f"- آخر رابط: `{last_url}`\n"
                f"- سبب التوقف: `{safe_err}`\n"
                "- لم يتم اختلاق أي حساب أو رابط باك لينك."
            ),
        }
    finally:
        discard_ephemeral_secret(raw_pw)
        raw_pw = ""
        substitutions.clear()
        await worker.close(session_id=session_id)
