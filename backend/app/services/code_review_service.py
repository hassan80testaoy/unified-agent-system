"""Unified GitHub PRs & GitLab MRs Code Review Service (§7E.4)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.phase7 import CodeReviewItem
from app.models.providers import Connection
from app.security.auth import record_audit
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.sanitizer import scrub_text
from app.services.llm_router import LLMRouter


def serialize_review_item(item: CodeReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "provider": item.provider,
        "repo": item.repo,
        "external_number": item.external_number,
        "title": item.title,
        "author": item.author,
        "source_branch": item.source_branch,
        "target_branch": item.target_branch,
        "url": item.url,
        "status": item.status,
        "diff_text": item.diff_text,
        "checks": list(item.checks_json or []),
        "comments": list(item.comments_json or []),
        "meta": item.meta_json or {},
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def connect_git_provider(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    provider: str,
    repo: str,
    token: str,
    base_url: str = "",
) -> dict[str, Any]:
    clean_prov = provider.strip().lower()
    if clean_prov not in {"github", "gitlab"}:
        raise ValueError("provider must be 'github' or 'gitlab'")
    clean_repo = repo.strip()
    if not clean_repo or not token.strip():
        raise ValueError("repo and token are required")
    default_base = "https://api.github.com" if clean_prov == "github" else "https://gitlab.com/api/v4"
    payload = {
        "provider": clean_prov,
        "repo": clean_repo,
        "token": token.strip(),
        "base_url": (base_url or default_base).strip().rstrip("/"),
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    enc = encrypt_secret(json.dumps(payload))
    existing = (
        db.query(Connection)
        .filter(Connection.workspace_id == workspace_id, Connection.kind == clean_prov)
        .first()
    )
    if existing:
        existing.config_enc = enc
        existing.status = "active"
    else:
        db.add(Connection(workspace_id=workspace_id, kind=clean_prov, config_enc=enc, status="active"))
    record_audit(db, user_id=user_id, workspace_id=workspace_id, action=f"{clean_prov}_connected", target=clean_repo)
    db.flush()
    return {"connected": True, "provider": clean_prov, "repo": clean_repo, "base_url": payload["base_url"]}


def _get_git_conn_cfg(db: Session, workspace_id: str, provider: str) -> dict[str, Any] | None:
    conn = (
        db.query(Connection)
        .filter(Connection.workspace_id == workspace_id, Connection.kind == provider, Connection.status == "active")
        .first()
    )
    if not conn or not conn.config_enc:
        return None
    try:
        return json.loads(decrypt_secret(conn.config_enc))
    except Exception:
        return None


def sync_git_reviews(db: Session, *, workspace_id: str, provider: str | None = None, repo: str | None = None) -> dict[str, Any]:
    """Fetch open PRs/MRs + checks + diff from connected GitHub / GitLab APIs."""
    targets = [provider] if provider in {"github", "gitlab"} else ["github", "gitlab"]
    synced: list[dict[str, Any]] = []
    errors: list[str] = []

    for prov in targets:
        cfg = _get_git_conn_cfg(db, workspace_id, prov)
        if not cfg:
            continue
        target_repo = (repo or cfg.get("repo") or "").strip()
        tok = str(cfg.get("token") or "")
        base = str(cfg.get("base_url") or "")
        try:
            if prov == "github":
                headers = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
                resp = httpx.get(f"{base}/repos/{target_repo}/pulls?state=open&per_page=15", headers=headers, timeout=15.0)
                if resp.status_code >= 400:
                    errors.append(f"github_http_{resp.status_code}")
                    continue
                for pr in resp.json() or []:
                    num = int(pr.get("number") or 0)
                    head_sha = ((pr.get("head") or {}).get("sha")) or ""
                    checks: list[dict[str, Any]] = []
                    if head_sha:
                        cr = httpx.get(f"{base}/repos/{target_repo}/commits/{head_sha}/check-runs", headers=headers, timeout=10.0)
                        if cr.status_code == 200:
                            checks = [
                                {"name": x.get("name"), "status": x.get("conclusion") or x.get("status")}
                                for x in ((cr.json() or {}).get("check_runs") or [])[:10]
                            ]
                    diff_resp = httpx.get(
                        f"{base}/repos/{target_repo}/pulls/{num}",
                        headers={**headers, "Accept": "application/vnd.github.v3.diff"},
                        timeout=10.0,
                    )
                    diff_txt = diff_resp.text[:16000] if diff_resp.status_code == 200 else ""
                    item = _upsert_review(
                        db,
                        workspace_id=workspace_id,
                        provider="github",
                        repo=target_repo,
                        external_number=num,
                        title=str(pr.get("title") or f"PR #{num}"),
                        author=str(((pr.get("user") or {}).get("login")) or "github"),
                        source_branch=str(((pr.get("head") or {}).get("ref")) or "head"),
                        target_branch=str(((pr.get("base") or {}).get("ref")) or "main"),
                        url=str(pr.get("html_url") or ""),
                        diff_text=diff_txt,
                        checks=checks,
                    )
                    synced.append(serialize_review_item(item))
            elif prov == "gitlab":
                from urllib.parse import quote

                proj_enc = quote(target_repo, safe="")
                headers = {"PRIVATE-TOKEN": tok}
                resp = httpx.get(f"{base}/projects/{proj_enc}/merge_requests?state=opened&per_page=15", headers=headers, timeout=15.0)
                if resp.status_code >= 400:
                    errors.append(f"gitlab_http_{resp.status_code}")
                    continue
                for mr in resp.json() or []:
                    num = int(mr.get("iid") or 0)
                    checks = []
                    pip_status = ((mr.get("head_pipeline") or {}).get("status"))
                    if pip_status:
                        checks.append({"name": "gitlab_pipeline", "status": pip_status})
                    chg = httpx.get(f"{base}/projects/{proj_enc}/merge_requests/{num}/changes", headers=headers, timeout=10.0)
                    diff_parts = []
                    if chg.status_code == 200:
                        for c in ((chg.json() or {}).get("changes") or [])[:12]:
                            diff_parts.append(f"--- a/{c.get('old_path')}\n+++ b/{c.get('new_path')}\n{c.get('diff', '')}")
                    item = _upsert_review(
                        db,
                        workspace_id=workspace_id,
                        provider="gitlab",
                        repo=target_repo,
                        external_number=num,
                        title=str(mr.get("title") or f"MR !{num}"),
                        author=str(((mr.get("author") or {}).get("username")) or "gitlab"),
                        source_branch=str(mr.get("source_branch") or "feat"),
                        target_branch=str(mr.get("target_branch") or "main"),
                        url=str(mr.get("web_url") or ""),
                        diff_text="\n".join(diff_parts)[:16000],
                        checks=checks,
                    )
                    synced.append(serialize_review_item(item))
        except Exception as exc:
            errors.append(scrub_text(f"{prov}:{type(exc).__name__}")[:120])

    db.flush()
    return {"synced_count": len(synced), "items": synced, "errors": errors}


def _upsert_review(
    db: Session,
    *,
    workspace_id: str,
    provider: str,
    repo: str,
    external_number: int,
    title: str,
    author: str,
    source_branch: str,
    target_branch: str,
    url: str,
    diff_text: str,
    checks: list[dict[str, Any]],
) -> CodeReviewItem:
    item = (
        db.query(CodeReviewItem)
        .filter(
            CodeReviewItem.workspace_id == workspace_id,
            CodeReviewItem.provider == provider,
            CodeReviewItem.repo == repo,
            CodeReviewItem.external_number == external_number,
        )
        .first()
    )
    if item is None:
        item = CodeReviewItem(
            workspace_id=workspace_id,
            provider=provider,
            repo=repo,
            external_number=external_number,
            title=title[:250],
            author=author[:120],
            source_branch=source_branch[:120],
            target_branch=target_branch[:120],
            url=url[:500],
            status="open",
            diff_text=diff_text,
            checks_json=checks,
            comments_json=[],
        )
        db.add(item)
    else:
        item.title = title[:250]
        item.diff_text = diff_text or item.diff_text
        item.checks_json = checks
        item.url = url[:500] or item.url
    db.flush()
    return item


def comment_on_review(
    db: Session,
    *,
    item: CodeReviewItem,
    user_id: str,
    body: str,
    author_label: str = "owner",
) -> CodeReviewItem:
    clean_body = scrub_text(body).strip()
    if not clean_body:
        raise ValueError("Comment body cannot be empty")

    remote_synced = False
    cfg = _get_git_conn_cfg(db, item.workspace_id, item.provider) if item.provider in {"github", "gitlab"} else None
    if cfg and cfg.get("token"):
        try:
            tok = str(cfg["token"])
            base = str(cfg["base_url"])
            if item.provider == "github":
                r = httpx.post(
                    f"{base}/repos/{item.repo}/issues/{item.external_number}/comments",
                    headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                    json={"body": clean_body},
                    timeout=10.0,
                )
                remote_synced = r.status_code < 400
            elif item.provider == "gitlab":
                from urllib.parse import quote

                proj_enc = quote(item.repo, safe="")
                r = httpx.post(
                    f"{base}/projects/{proj_enc}/merge_requests/{item.external_number}/notes",
                    headers={"PRIVATE-TOKEN": tok},
                    json={"body": clean_body},
                    timeout=10.0,
                )
                remote_synced = r.status_code < 400
        except Exception:
            remote_synced = False

    comments = list(item.comments_json or [])
    comments.append(
        {
            "author": author_label,
            "body": clean_body,
            "remote_synced": remote_synced,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    item.comments_json = comments
    record_audit(
        db,
        user_id=user_id,
        workspace_id=item.workspace_id,
        action="code_review_commented",
        target=item.id,
        details={"provider": item.provider, "repo": item.repo, "number": item.external_number},
    )
    db.flush()
    return item


def approve_review(
    db: Session,
    *,
    item: CodeReviewItem,
    user_id: str,
    note: str = "",
    run_ai_second_opinion: bool = False,
) -> dict[str, Any]:
    ai_review: dict[str, Any] | None = None
    if run_ai_second_opinion:
        try:
            ai_review = LLMRouter(db, item.workspace_id).review_with_different_provider(
                producer_provider="local_author",
                content_to_review=f"{item.title}\n\n{item.diff_text[:2500]}",
                criteria="Security, correctness, and clean diff quality",
            )
        except Exception:
            ai_review = None

    remote_approved = False
    cfg = _get_git_conn_cfg(db, item.workspace_id, item.provider) if item.provider in {"github", "gitlab"} else None
    if cfg and cfg.get("token"):
        try:
            tok = str(cfg["token"])
            base = str(cfg["base_url"])
            if item.provider == "github":
                r = httpx.post(
                    f"{base}/repos/{item.repo}/pulls/{item.external_number}/reviews",
                    headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                    json={"event": "APPROVE", "body": scrub_text(note or "Approved via Unified Agent System")},
                    timeout=10.0,
                )
                remote_approved = r.status_code < 400
            elif item.provider == "gitlab":
                from urllib.parse import quote

                proj_enc = quote(item.repo, safe="")
                r = httpx.post(
                    f"{base}/projects/{proj_enc}/merge_requests/{item.external_number}/approve",
                    headers={"PRIVATE-TOKEN": tok},
                    timeout=10.0,
                )
                remote_approved = r.status_code < 400
        except Exception:
            remote_approved = False

    item.status = "approved"
    if note:
        comment_on_review(db, item=item, user_id=user_id, body=f"[Approved] {note}")
    record_audit(
        db,
        user_id=user_id,
        workspace_id=item.workspace_id,
        action="code_review_approved",
        target=item.id,
        details={"provider": item.provider, "repo": item.repo, "number": item.external_number, "remote_approved": remote_approved},
    )
    db.flush()
    return {"item": serialize_review_item(item), "remote_approved": remote_approved, "ai_review": ai_review}
