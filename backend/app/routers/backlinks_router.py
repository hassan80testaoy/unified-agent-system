"""Backlinks CRUD, audited password reveal, live link checker, identity/anchors settings, and XLSX/CSV/JSON import/export."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.backlink.agent import seed_backlink_defaults, verify_live_link_url
from app.db import get_db
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
from app.security.auth import AuthContext, get_current_auth, record_audit, require_roles, verify_totp_code
from app.security.crypto import decrypt_secret
from app.services.importer import (
    commit_sites_import,
    export_backlinks_csv,
    import_steps_json,
    preview_import,
)

router = APIRouter(tags=["backlinks"])


class SitePatchRequest(BaseModel):
    status: str | None = None
    signup_url: str | None = None
    category: str | None = None
    blocked_reason: str | None = None


class RevealPasswordRequest(BaseModel):
    totp_code: str | None = None


class IdentityUpdateRequest(BaseModel):
    display_name: str
    extended_name: str | None = None
    photo_url: str | None = None
    title: str | None = None


class BioCreateRequest(BaseModel):
    text: str
    angle: str = "general"


class AnchorCreateRequest(BaseModel):
    target_kind: str = "homepage"
    text: str
    weight: float = 1.0


class TargetCreateRequest(BaseModel):
    kind: str = "homepage"
    url: str


class StepsImportRequest(BaseModel):
    domain: str
    profile_url: str | None = None
    steps: list[dict[str, Any]]


@router.get("/api/backlinks/sites")
def list_sites(
    status_filter: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    seed_backlink_defaults(db, auth.workspace.id)
    db.commit()
    q = db.query(BlSite).filter(BlSite.workspace_id == auth.workspace.id)
    if status_filter:
        q = q.filter(BlSite.status == status_filter)
    rows = q.order_by(BlSite.source_row.asc(), BlSite.created_at.desc()).limit(1000).all()
    return {
        "sites": [
            {
                "id": s.id,
                "source_row": s.source_row,
                "domain": s.domain,
                "signup_url": s.signup_url,
                "category": s.category,
                "noindex_checked": bool(s.noindex_checked),
                "is_noindex": bool(s.is_noindex),
                "status": s.status,
                "blocked_reason": s.blocked_reason,
                "last_attempt_at": s.last_attempt_at,
            }
            for s in rows
        ]
    }


@router.patch("/api/backlinks/sites/{site_id}")
def patch_site(
    site_id: str,
    req: SitePatchRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    s = db.get(BlSite, site_id)
    if not s or s.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الموقع غير موجود", "message_en": "Site not found"})
    if req.status is not None:
        s.status = req.status
    if req.signup_url is not None:
        s.signup_url = req.signup_url
    if req.category is not None:
        s.category = req.category
    if req.blocked_reason is not None:
        s.blocked_reason = req.blocked_reason
    db.commit()
    return {"id": s.id, "status": s.status}


@router.get("/api/backlinks/accounts")
def list_accounts(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(BlAccount).filter(BlAccount.workspace_id == auth.workspace.id).order_by(BlAccount.created_at.desc()).all()
    sites_map = {s.id: s.domain for s in db.query(BlSite).filter(BlSite.workspace_id == auth.workspace.id).all()}
    return {
        "accounts": [
            {
                "id": a.id,
                "site_id": a.site_id,
                "domain": sites_map.get(a.site_id, ""),
                "email_alias": a.email_alias,
                "username": a.username,
                "has_password": bool(a.password_enc),
                "profile_url": a.profile_url,
                "created_at": a.created_at.isoformat(),
                "verified_at": a.verified_at,
                "notes": a.notes,
            }
            for a in rows
        ]
    }


@router.post("/api/backlinks/accounts/{account_id}/reveal")
def reveal_account_password(
    account_id: str,
    req: RevealPasswordRequest,
    request: Request,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Audited reveal/copy of per-site encrypted password. Never exported in CSV/reports."""
    a = db.get(BlAccount, account_id)
    if not a or a.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Account not found"})

    if auth.user.totp_enabled:
        if not req.totp_code or not verify_totp_code(auth.user.totp_secret_enc, req.totp_code):
            raise HTTPException(
                status_code=401,
                detail={"code": "totp_required", "message_ar": "أدخل رمز التحقق الثنائي لكشف كلمة المرور", "message_en": "2FA code required to reveal password"},
            )

    record_audit(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        action="backlinks.account_password_reveal",
        target=a.id,
        ip=request.client.host if request.client else None,
        details={"site_id": a.site_id, "username": a.username},
    )
    db.commit()
    return {"password": decrypt_secret(a.password_enc) if a.password_enc else ""}


@router.get("/api/backlinks/accounts/{account_id}/steps")
def get_account_steps(account_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    a = db.get(BlAccount, account_id)
    if not a or a.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الحساب غير موجود", "message_en": "Account not found"})
    steps = db.query(BlStep).filter(BlStep.account_id == a.id).order_by(BlStep.n.asc()).all()
    return {
        "account_id": a.id,
        "steps": [
            {
                "n": s.n,
                "action": s.action,
                "selector_css": s.selector_css,
                "el_id": s.el_id,
                "el_class": s.el_class,
                "el_text": s.el_text,
                "value_key": s.value_key,
                "ok": bool(s.ok),
                "ts": s.ts,
            }
            for s in steps
        ],
    }


@router.get("/api/backlinks/accounts/{account_id}/steps/export")
def export_account_steps(account_id: str, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    data = get_account_steps(account_id, auth, db)
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=steps_{account_id}.json"},
    )


@router.get("/api/backlinks/links")
def list_links(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    rows = db.query(BlLink).filter(BlLink.workspace_id == auth.workspace.id).order_by(BlLink.created_at.desc()).all()
    accounts_map = {a.id: a for a in db.query(BlAccount).filter(BlAccount.workspace_id == auth.workspace.id).all()}
    targets_map = {t.id: t for t in db.query(BlTarget).filter(BlTarget.workspace_id == auth.workspace.id).all()}
    anchors_map = {an.id: an for an in db.query(BlAnchor).filter(BlAnchor.workspace_id == auth.workspace.id).all()}
    return {
        "links": [
            {
                "id": l.id,
                "account_id": l.account_id,
                "profile_url": accounts_map[l.account_id].profile_url if l.account_id in accounts_map else "",
                "target_kind": targets_map[l.target_id].kind if l.target_id in targets_map else "",
                "target_url": targets_map[l.target_id].url if l.target_id in targets_map else "",
                "anchor_text": anchors_map[l.anchor_id].text if l.anchor_id and l.anchor_id in anchors_map else "",
                "link_type": l.link_type,
                "live": bool(l.live),
                "last_checked_at": l.last_checked_at,
                "check_note": l.check_note,
            }
            for l in rows
        ]
    }


@router.post("/api/backlinks/links/{link_id}/check")
async def check_single_link(
    link_id: str,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    l = db.get(BlLink, link_id)
    if not l or l.workspace_id != auth.workspace.id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message_ar": "الرابط غير موجود", "message_en": "Link not found"})
    acct = db.get(BlAccount, l.account_id)
    target = db.get(BlTarget, l.target_id)
    if not acct or not acct.profile_url or not target:
        return {"live": bool(l.live), "link_type": l.link_type, "note": "missing_profile_or_target"}

    if acct.profile_url.startswith("mock://"):
        l.last_checked_at = datetime.now(timezone.utc).isoformat()
        l.check_note = "checked_mock_now"
        db.commit()
        return {"live": bool(l.live), "link_type": l.link_type, "note": l.check_note}

    res = await verify_live_link_url(acct.profile_url, target.url)
    l.live = 1 if res["found"] else 0
    l.link_type = res["link_type"]
    l.last_checked_at = datetime.now(timezone.utc).isoformat()
    l.check_note = res["note"]
    db.commit()
    return {"live": bool(l.live), "link_type": l.link_type, "note": l.check_note}


@router.get("/api/backlinks/settings")
def get_backlinks_settings(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    seed_backlink_defaults(db, auth.workspace.id)
    db.commit()
    ident = db.query(BlIdentity).filter(BlIdentity.workspace_id == auth.workspace.id).first()
    targets = db.query(BlTarget).filter(BlTarget.workspace_id == auth.workspace.id).all()
    bios = db.query(BlBio).filter(BlBio.workspace_id == auth.workspace.id).all()
    anchors = db.query(BlAnchor).filter(BlAnchor.workspace_id == auth.workspace.id).all()
    return {
        "identity": {
            "id": ident.id if ident else None,
            "display_name": ident.display_name if ident else "",
            "extended_name": ident.extended_name if ident else "",
            "photo_url": ident.photo_url if ident else "",
            "title": ident.title if ident else "",
        },
        "targets": [{"id": t.id, "kind": t.kind, "url": t.url} for t in targets],
        "bios": [{"id": b.id, "text": b.text, "angle": b.angle, "times_used": b.times_used} for b in bios],
        "anchors": [
            {"id": a.id, "target_kind": a.target_kind, "text": a.text, "weight": a.weight, "times_used": a.times_used}
            for a in anchors
        ],
    }


@router.put("/api/backlinks/identity")
def update_identity(
    req: IdentityUpdateRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin")),
    db: Session = Depends(get_db),
):
    ident = db.query(BlIdentity).filter(BlIdentity.workspace_id == auth.workspace.id).first()
    if ident is None:
        ident = BlIdentity(workspace_id=auth.workspace.id, display_name=req.display_name)
        db.add(ident)
    ident.display_name = req.display_name
    ident.extended_name = req.extended_name
    ident.photo_url = req.photo_url
    ident.title = req.title
    db.commit()
    return {"ok": True}


@router.post("/api/backlinks/bios")
def add_bio(req: BioCreateRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    b = BlBio(workspace_id=auth.workspace.id, text=req.text, angle=req.angle)
    db.add(b)
    db.commit()
    return {"id": b.id}


@router.delete("/api/backlinks/bios/{bio_id}")
def delete_bio(bio_id: str, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    b = db.get(BlBio, bio_id)
    if b and b.workspace_id == auth.workspace.id:
        db.delete(b)
        db.commit()
    return {"ok": True}


@router.post("/api/backlinks/anchors")
def add_anchor(req: AnchorCreateRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    a = BlAnchor(workspace_id=auth.workspace.id, target_kind=req.target_kind, text=req.text, weight=req.weight)
    db.add(a)
    db.commit()
    return {"id": a.id}


@router.delete("/api/backlinks/anchors/{anchor_id}")
def delete_anchor(anchor_id: str, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    a = db.get(BlAnchor, anchor_id)
    if a and a.workspace_id == auth.workspace.id:
        db.delete(a)
        db.commit()
    return {"ok": True}


@router.post("/api/backlinks/targets")
def upsert_target(req: TargetCreateRequest, auth: AuthContext = Depends(require_roles("owner", "admin")), db: Session = Depends(get_db)):
    t = db.query(BlTarget).filter(BlTarget.workspace_id == auth.workspace.id, BlTarget.kind == req.kind).first()
    if t is None:
        t = BlTarget(workspace_id=auth.workspace.id, kind=req.kind, url=req.url)
        db.add(t)
    else:
        t.url = req.url
    db.commit()
    return {"id": t.id, "kind": t.kind, "url": t.url}


@router.post("/api/backlinks/import/preview")
async def import_preview_endpoint(
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
):
    raw = await file.read()
    return preview_import(file.filename or "upload.csv", raw)


@router.post("/api/backlinks/import/commit")
async def import_commit_endpoint(
    file: UploadFile = File(...),
    mapping_json: str = Form(...),
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    mapping = json.loads(mapping_json)
    return commit_sites_import(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        filename=file.filename or "upload.csv",
        content=raw,
        mapping=mapping,
    )


@router.post("/api/backlinks/import")
async def import_alias_endpoint(
    file: UploadFile = File(...),
    mapping_json: str | None = Form(default=None),
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if mapping_json:
        mapping = json.loads(mapping_json)
    else:
        prev = preview_import(file.filename or "upload.csv", raw)
        mapping = prev["suggested_mapping"]
        if "domain" not in mapping and prev["headers"]:
            mapping["domain"] = prev["headers"][0]
    return commit_sites_import(
        db,
        workspace_id=auth.workspace.id,
        user_id=auth.user.id,
        filename=file.filename or "upload.csv",
        content=raw,
        mapping=mapping,
    )


@router.post("/api/backlinks/import/steps")
def import_steps_endpoint(
    req: StepsImportRequest,
    auth: AuthContext = Depends(require_roles("owner", "admin", "member")),
    db: Session = Depends(get_db),
):
    return import_steps_json(
        db,
        workspace_id=auth.workspace.id,
        domain=req.domain,
        steps_payload=req.steps,
        profile_url=req.profile_url,
    )


@router.get("/api/backlinks/export")
def export_csv_endpoint(
    table: str = "sites",
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    csv_text = export_backlinks_csv(db, auth.workspace.id, table)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=backlinks_{table}.csv"},
    )
