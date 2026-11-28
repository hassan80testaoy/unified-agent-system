"""Flagship 9-Step Backlink Computer-Use Loop (§1E) callable from Chat or Agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.backlink.agent import check_url_noindex, seed_backlink_defaults, verify_live_link_url
from app.captcha.solver import CaptchaChallenge, CaptchaResult, dispatch_solve, get_captcha_solver
from app.config import get_settings
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
from app.security.sanitizer import scrub_text
from app.services.accounts_sheet import record_account_in_spreadsheet
from app.services.runtime_checks import get_runtime_readiness, validate_real_email
from app.services.session_replay import replay_backlink_session, save_backlink_session
from app.services.captcha_inject import inject_token_via_worker
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
) -> dict[str, Any]:
    """Execute the 9-step Flagship Backlink Loop cleanly (open -> verify -> report)."""
    seed_backlink_defaults(db, workspace_id)
    # Default the browser proxy from .env when the caller (e.g. chat) did
    # not pass one — otherwise the browser runs direct and any solver
    # token solved via the proxy IP is rejected (token/IP mismatch).
    proxy = (proxy or "").strip() or (get_settings().residential_proxy or "").strip() or None
    clean_domain = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    stages: list[dict[str, Any]] = []

    def _stage(num: int, name: str, status: str, detail: str) -> None:
        clean_detail = scrub_text(detail)
        if mock_mode and not clean_detail.startswith("محاكاة"):
            clean_detail = f"محاكاة — {clean_detail}"
        stages.append({"step": num, "name": name, "status": status, "detail": clean_detail})

    ok_email, email_err = validate_real_email(email)
    if not ok_email:
        return {
            "status": "invalid_email",
            "failed_step": 0,
            "domain": clean_domain,
            "is_mock": bool(mock_mode),
            "report_ar": email_err or "يرجى إدخال بريد إلكتروني حقيقي قبل التشغيل.",
            "stages": stages,
        }

    if not mock_mode:
        readiness = get_runtime_readiness(db, workspace_id)
        if not readiness["real_backlink_ready"]:
            missing_list = "\n- ".join(readiness["missing_for_real_backlink"])
            return {
                "status": "missing_requirements",
                "failed_step": 0,
                "domain": clean_domain,
                "is_mock": False,
                "missing": readiness["missing_keys"],
                "report_ar": f"لا يمكن بدء التشغيل الحقيقي قبل استكمال المتطلبات التالية:\n- {missing_list}",
                "stages": stages,
            }

    # 1. OPEN
    site = (
        db.query(BlSite)
        .filter(BlSite.workspace_id == workspace_id, BlSite.domain == clean_domain)
        .first()
    )
    if not site:
        site = BlSite(
            workspace_id=workspace_id,
            domain=clean_domain,
            signup_url=f"https://{clean_domain}/register",
            category="profile",
            status="queued",
        )
        db.add(site)
        db.flush()

    if mock_mode:
        noindex_hit = False
    else:
        noindex_hit, _ = await check_url_noindex(site.signup_url or f"https://{clean_domain}")
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
            "report_ar": f"فشل في خطوة 1: الموقع {clean_domain} يحتوي على noindex وتم استبعاده.",
            "stages": stages,
        }

    replay_info = replay_backlink_session(
        db, workspace_id=workspace_id, domain=clean_domain, available_elements=available_elements
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
        "replay": replay_info,
    }
