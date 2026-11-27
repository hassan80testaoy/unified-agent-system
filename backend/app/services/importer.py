"""Backlinks Farm XLSX / CSV importer, JSON step-history importer, and password-free CSV/XLSX exporter."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import openpyxl
from sqlalchemy.orm import Session

from app.models.backlinks import BlAccount, BlLink, BlSite, BlStep, BlTarget
from app.security.auth import record_audit


def parse_tabular_upload(filename: str, content: bytes) -> tuple[list[str], list[list[Any]]]:
    lower = filename.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row:
            return [], []
        headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(header_row)]
        rows = [list(r) for r in rows_iter]
        return headers, rows

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header_row = next(reader, None)
    if not header_row:
        return [], []
    headers = [str(c).strip() for c in header_row]
    rows = [list(r) for r in reader]
    return headers, rows


def preview_import(filename: str, content: bytes) -> dict[str, Any]:
    headers, rows = parse_tabular_upload(filename, content)
    suggested_mapping: dict[str, str] = {}
    for h in headers:
        hl = h.lower()
        if any(k in hl for k in ("domain", "site", "website", "url", "الموقع", "الدومين")):
            suggested_mapping.setdefault("domain", h)
        if any(k in hl for k in ("signup", "register", "registration", "رابط التسجيل")):
            suggested_mapping.setdefault("signup_url", h)
        if any(k in hl for k in ("category", "type", "التصنيف", "الفئة")):
            suggested_mapping.setdefault("category", h)
        if any(k in hl for k in ("status", "الحالة")):
            suggested_mapping.setdefault("status", h)

    preview_rows = []
    for idx, r in enumerate(rows[:20], start=1):
        row_dict = {headers[i]: (r[i] if i < len(r) else None) for i in range(len(headers))}
        preview_rows.append({"row_num": idx, "data": row_dict})

    return {
        "headers": headers,
        "total_rows": len(rows),
        "suggested_mapping": suggested_mapping,
        "preview": preview_rows,
    }


def commit_sites_import(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    filename: str,
    content: bytes,
    mapping: dict[str, str],
) -> dict[str, int]:
    headers, rows = parse_tabular_upload(filename, content)
    header_idx = {h: i for i, h in enumerate(headers)}
    domain_col = mapping.get("domain")
    if not domain_col or domain_col not in header_idx:
        raise ValueError("Column mapping must include 'domain'.")

    existing_domains = {
        s.domain.lower() for s in db.query(BlSite).filter(BlSite.workspace_id == workspace_id).all()
    }

    imported = 0
    duplicate = 0
    missing = 0

    for row_num, r in enumerate(rows, start=1):
        raw_dom = r[header_idx[domain_col]] if header_idx[domain_col] < len(r) else None
        if not raw_dom or not str(raw_dom).strip():
            missing += 1
            continue
        dom = str(raw_dom).strip().lower()
        dom = dom.replace("https://", "").replace("http://", "").split("/")[0]
        if not dom:
            missing += 1
            continue
        if dom in existing_domains:
            duplicate += 1
            continue

        signup_url = None
        if mapping.get("signup_url") and mapping["signup_url"] in header_idx:
            val = r[header_idx[mapping["signup_url"]]]
            signup_url = str(val).strip() if val else None
        if not signup_url:
            signup_url = f"https://{dom}/signup"

        category = None
        if mapping.get("category") and mapping["category"] in header_idx:
            val = r[header_idx[mapping["category"]]]
            category = str(val).strip() if val else None

        status_val = "queued"
        if mapping.get("status") and mapping["status"] in header_idx:
            raw_st = str(r[header_idx[mapping["status"]]] or "").strip().lower()
            if raw_st in {"queued", "live", "excluded_noindex", "blocked", "dropped_by_user", "pending_review", "needs_captcha_manual", "failed"}:
                status_val = raw_st

        db.add(
            BlSite(
                workspace_id=workspace_id,
                source_row=row_num,
                domain=dom,
                signup_url=signup_url,
                category=category,
                status=status_val,
            )
        )
        existing_domains.add(dom)
        imported += 1

    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="backlinks.import",
        target=filename,
        details={"imported": imported, "duplicate": duplicate, "missing": missing},
    )
    db.commit()
    return {"imported": imported, "duplicate": duplicate, "missing": missing}


def import_steps_json(
    db: Session,
    *,
    workspace_id: str,
    domain: str,
    steps_payload: list[dict[str, Any]],
    profile_url: str | None = None,
) -> dict[str, Any]:
    dom = domain.strip().lower()
    site = db.query(BlSite).filter(BlSite.workspace_id == workspace_id, BlSite.domain == dom).first()
    if site is None:
        site = BlSite(workspace_id=workspace_id, domain=dom, signup_url=f"https://{dom}", status="pending_review")
        db.add(site)
        db.flush()

    acct = db.query(BlAccount).filter(BlAccount.workspace_id == workspace_id, BlAccount.site_id == site.id).first()
    if acct is None:
        acct = BlAccount(workspace_id=workspace_id, site_id=site.id, profile_url=profile_url)
        db.add(acct)
        db.flush()

    for idx, step in enumerate(steps_payload, start=1):
        db.add(
            BlStep(
                workspace_id=workspace_id,
                account_id=acct.id,
                n=int(step.get("n") or idx),
                action=str(step.get("action") or "click"),
                selector_css=step.get("selector_css") or step.get("selector"),
                el_id=step.get("el_id") or step.get("id"),
                el_class=step.get("el_class") or step.get("class"),
                el_text=step.get("el_text") or step.get("text"),
                value_key=step.get("value_key"),
                ok=int(step.get("ok", 1)),
                ts=str(step.get("ts") or ""),
            )
        )
    db.commit()
    return {"account_id": acct.id, "steps_imported": len(steps_payload)}


def export_backlinks_csv(db: Session, workspace_id: str, table: str) -> str:
    """Export sites/accounts/links CSV — STRICTLY EXCLUDES passwords and secrets."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    if table == "sites":
        writer.writerow(["source_row", "domain", "signup_url", "category", "is_noindex", "status", "blocked_reason"])
        for s in db.query(BlSite).filter(BlSite.workspace_id == workspace_id).all():
            writer.writerow([s.source_row, s.domain, s.signup_url, s.category, s.is_noindex, s.status, s.blocked_reason or ""])
    elif table == "accounts":
        # NEVER export password_enc or plaintext passwords
        writer.writerow(["site_id", "email_alias", "username", "profile_url", "verified_at", "notes"])
        for a in db.query(BlAccount).filter(BlAccount.workspace_id == workspace_id).all():
            writer.writerow([a.site_id, a.email_alias or "", a.username or "", a.profile_url or "", a.verified_at or "", a.notes or ""])
    else:
        writer.writerow(["account_id", "target_url", "link_type", "live", "last_checked_at", "check_note"])
        for l in db.query(BlLink).filter(BlLink.workspace_id == workspace_id).all():
            t = db.get(BlTarget, l.target_id)
            writer.writerow([l.account_id, t.url if t else "", l.link_type, l.live, l.last_checked_at or "", l.check_note or ""])

    return buf.getvalue()
