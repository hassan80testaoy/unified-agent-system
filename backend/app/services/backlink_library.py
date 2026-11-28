"""Multi-Site Backlink Library + Target Site Projects + Target × Library × Email Placements + Playbooks (§8B, §8L.1)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.backlinks import BlSite, BlTarget
from app.models.phase8 import BacklinkLibrarySite, BacklinkPlacement, TargetSiteProject
from app.security.sanitizer import scrub_text
from app.services.accounts_sheet import record_account_in_spreadsheet

_DEFAULT_LIBRARY_SEEDS: list[dict[str, Any]] = [
    {
        "domain": "about.me",
        "signup_url": "https://about.me/signup",
        "category": "profile",
        "signup_flow": "email",
        "discovered_fields": ["name", "email", "password", "bio", "website_url", "location"],
        "notes": "ملف شخصي مجاني يدعم وضع رابط الموقع الرئيسي والوصف المهني.",
    },
    {
        "domain": "gravatar.com",
        "signup_url": "https://gravatar.com/connect/",
        "category": "profile",
        "signup_flow": "email",
        "discovered_fields": ["email", "display_name", "bio", "websites"],
        "notes": "ملف WordPress/Gravatar عام يسمح بإضافة عدة روابط موثوقة.",
    },
    {
        "domain": "medium.com",
        "signup_url": "https://medium.com/m/signin",
        "category": "editorial_profile",
        "signup_flow": "code_first",
        "discovered_fields": ["email", "display_name", "bio", "website_url"],
        "notes": "تسجيل عبر كود البريد أو Google ثم إضافة رابط الموقع في البايو.",
    },
]


def _playbooks_dir() -> Path:
    d = get_settings().data_dir / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _normalize_domain(raw: str) -> tuple[str, str]:
    s = (raw or "").strip()
    if not s:
        raise ValueError("Domain or URL is required.")
    url = s if s.startswith(("http://", "https://")) else f"https://{s}"
    host = (urlparse(url).hostname or "").lower().lstrip("www.")
    if not host or "." not in host:
        raise ValueError(f"Invalid domain: {raw}")
    return host, url


def _write_site_playbook(
    domain: str,
    *,
    signup_url: str,
    signup_flow: str,
    fields: list[str],
    notes: str,
) -> tuple[str, Path]:
    safe_dom = re.sub(r"[^a-zA-Z0-9._-]+", "_", domain)
    pdir = _playbooks_dir() / safe_dom
    pdir.mkdir(parents=True, exist_ok=True)
    pfile = pdir / "SITE.md"
    md = "\n".join(
        [
            f"# Playbook: {domain}",
            "",
            f"- **Signup URL:** `{signup_url}`",
            f"- **Signup Flow:** `{signup_flow}` (`email` | `google_only` | `code_first` | `form`)",
            f"- **Discovered Backlink Fields:** {', '.join(fields) if fields else 'email, website, bio'}",
            f"- **Notes:** {scrub_text(notes)}",
            "- **Verification:** Inspect public profile page after save and confirm target URL & `dofollow/nofollow`.",
        ]
    )
    pfile.write_text(md + "\n", encoding="utf-8")
    return md, pfile


def ensure_seeded_backlink_library(db: Session, workspace_id: str) -> list[BacklinkLibrarySite]:
    existing = {
        r.domain.lower(): r
        for r in db.query(BacklinkLibrarySite).filter(BacklinkLibrarySite.workspace_id == workspace_id).all()
    }
    for seed in _DEFAULT_LIBRARY_SEEDS:
        dom = seed["domain"]
        if dom not in existing:
            md, pfile = _write_site_playbook(
                dom,
                signup_url=seed["signup_url"],
                signup_flow=seed["signup_flow"],
                fields=list(seed["discovered_fields"]),
                notes=seed["notes"],
            )
            row = BacklinkLibrarySite(
                workspace_id=workspace_id,
                domain=dom,
                signup_url=seed["signup_url"],
                category=seed["category"],
                signup_flow=seed["signup_flow"],
                discovered_fields_json=list(seed["discovered_fields"]),
                playbook_md=md,
                playbook_path=str(pfile),
                notes=seed["notes"],
                status="ready",
            )
            db.add(row)
            existing[dom] = row
        # Ensure BlSite also stays aware if needed
        if not db.query(BlSite).filter(BlSite.workspace_id == workspace_id, BlSite.domain == dom).first():
            db.add(
                BlSite(
                    workspace_id=workspace_id,
                    domain=dom,
                    signup_url=seed["signup_url"],
                    category=seed["category"],
                    status="queued",
                )
            )
    db.flush()
    return sorted(existing.values(), key=lambda x: x.domain)


def serialize_target_project(t: TargetSiteProject) -> dict[str, Any]:
    return {
        "id": t.id,
        "workspace_id": t.workspace_id,
        "name": t.name,
        "domain": t.domain,
        "url": t.url,
        "maps_url": t.maps_url,
        "default_identity": t.default_identity_json or {},
        "notes": t.notes,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def serialize_library_site(s: BacklinkLibrarySite) -> dict[str, Any]:
    return {
        "id": s.id,
        "workspace_id": s.workspace_id,
        "domain": s.domain,
        "signup_url": s.signup_url,
        "category": s.category,
        "signup_flow": s.signup_flow,
        "discovered_fields": list(s.discovered_fields_json or []),
        "playbook_md": s.playbook_md,
        "playbook_path": s.playbook_path,
        "notes": s.notes,
        "past_results": list(s.past_results_json or [])[-10:],
        "success_count": int(s.success_count or 0),
        "fail_count": int(s.fail_count or 0),
        "status": s.status,
    }


def serialize_placement(p: BacklinkPlacement) -> dict[str, Any]:
    return {
        "id": p.id,
        "workspace_id": p.workspace_id,
        "target_project_id": p.target_project_id,
        "library_site_id": p.library_site_id,
        "email_used": p.email_used,
        "profile_url": p.profile_url,
        "link_type": p.link_type,
        "live": bool(p.live),
        "status": p.status,
        "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def create_target_project(
    db: Session,
    *,
    workspace_id: str,
    name: str,
    url: str,
    maps_url: str | None = None,
    notes: str = "",
) -> TargetSiteProject:
    dom, norm_url = _normalize_domain(url)
    row = TargetSiteProject(
        workspace_id=workspace_id,
        name=(name or dom).strip()[:160],
        domain=dom,
        url=norm_url,
        maps_url=(maps_url or "").strip() or None,
        notes=scrub_text(notes or ""),
        status="active",
    )
    db.add(row)
    # Mirror in legacy BlTarget so existing backlink loop stays synced
    if not db.query(BlTarget).filter(BlTarget.workspace_id == workspace_id, BlTarget.url == norm_url).first():
        db.add(BlTarget(workspace_id=workspace_id, kind="homepage", url=norm_url))
    db.flush()
    return row


async def discover_and_add_library_site(
    db: Session,
    *,
    workspace_id: str,
    domain_or_url: str,
    category: str = "profile",
    notes: str = "",
) -> dict[str, Any]:
    """Open/probe a website, inspect its signup flow & form fields, generate a SITE.md playbook, and store in Library (§8B.1)."""
    ensure_seeded_backlink_library(db, workspace_id)
    dom, url = _normalize_domain(domain_or_url)

    signup_flow = "email"
    fields: list[str] = ["email", "website", "bio"]
    discovered_signup = url
    probe_note = notes.strip()

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = await client.get(url)
        html = resp.text or ""
        low = html.lower()
        if "accounts.google.com" in low or "continue with google" in low:
            signup_flow = "google_only" if "<input" not in low else "email"
        elif "verification code" in low or "one-time code" in low or "كود التحقق" in low:
            signup_flow = "code_first"
        found_inputs = re.findall(r'<input[^>]+name=["\']([a-zA-Z0-9_-]{2,40})["\']', html, flags=re.I)
        if found_inputs:
            fields = list(dict.fromkeys(found_inputs[:12]))
        m_reg = re.search(r'href=["\']([^"\']*(?:signup|register|join|create-account)[^"\']*)["\']', html, flags=re.I)
        if m_reg:
            discovered_signup = urljoin(url, m_reg.group(1))
        if not probe_note:
            probe_note = f"تم فحص الموقع ({resp.status_code}) واكتشاف حقول التسجيل وتدفق `{signup_flow}`."
    except Exception as exc:
        if not probe_note:
            probe_note = f"تم حفظ الموقع بالمسار الافتراضي (تعذر الجلب التلقائي: {type(exc).__name__})."

    md, pfile = _write_site_playbook(
        dom,
        signup_url=discovered_signup,
        signup_flow=signup_flow,
        fields=fields,
        notes=probe_note,
    )
    existing = (
        db.query(BacklinkLibrarySite)
        .filter(BacklinkLibrarySite.workspace_id == workspace_id, BacklinkLibrarySite.domain == dom)
        .first()
    )
    if existing is None:
        existing = BacklinkLibrarySite(
            workspace_id=workspace_id,
            domain=dom,
            signup_url=discovered_signup,
            category=category,
            signup_flow=signup_flow,
            discovered_fields_json=fields,
            playbook_md=md,
            playbook_path=str(pfile),
            notes=probe_note,
            status="ready",
        )
        db.add(existing)
    else:
        existing.signup_url = discovered_signup
        existing.signup_flow = signup_flow
        existing.discovered_fields_json = fields
        existing.playbook_md = md
        existing.playbook_path = str(pfile)
        existing.notes = probe_note

    if not db.query(BlSite).filter(BlSite.workspace_id == workspace_id, BlSite.domain == dom).first():
        db.add(BlSite(workspace_id=workspace_id, domain=dom, signup_url=discovered_signup, category=category, status="queued"))
    db.flush()
    return serialize_library_site(existing)


def record_backlink_placement(
    db: Session,
    *,
    workspace_id: str,
    target_project_id: str,
    library_site_id: str,
    email_used: str,
    profile_url: str | None = None,
    link_type: str = "unknown",
    live: bool = False,
    status: str = "planned",
    notes: str = "",
) -> dict[str, Any]:
    """Bind one TargetProject × BacklinkLibrarySite × per-entry email and record in local accounts.xlsx (§8B.2, §8B.3)."""
    proj = db.get(TargetSiteProject, target_project_id)
    if not proj or proj.workspace_id != workspace_id:
        raise ValueError("Target site project not found.")
    lib = db.get(BacklinkLibrarySite, library_site_id)
    if not lib or lib.workspace_id != workspace_id:
        raise ValueError("Backlink library site not found.")

    clean_email = (email_used or "").strip()
    if "@" not in clean_email:
        raise ValueError("A valid distinct email address is required per (target, library_site) pair.")

    row = BacklinkPlacement(
        workspace_id=workspace_id,
        target_project_id=proj.id,
        library_site_id=lib.id,
        email_used=clean_email,
        profile_url=(profile_url or "").strip() or None,
        link_type=link_type,
        live=bool(live),
        status=status,
        notes=scrub_text(notes or f"Target: {proj.domain}"),
    )
    db.add(row)

    results = list(lib.past_results_json or [])
    results.append(
        {
            "target_domain": proj.domain,
            "email": clean_email,
            "status": status,
            "live": bool(live),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    lib.past_results_json = results[-25:]
    if live or status == "live":
        lib.success_count = int(lib.success_count or 0) + 1
    elif status in {"blocked", "failed"}:
        lib.fail_count = int(lib.fail_count or 0) + 1

    # Sync per-entry email record into local-only data/accounts.xlsx without mixing targets
    record_account_in_spreadsheet(
        domain=lib.domain,
        email_username=clean_email,
        password="",
        profile_url=profile_url or "",
        notes=f"target={proj.domain} | status={status}",
    )
    db.flush()
    return serialize_placement(row)
