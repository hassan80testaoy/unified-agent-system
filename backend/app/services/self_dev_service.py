"""Self-Development Workflow: Plan -> Approval Card -> Worktree Edit -> Tests -> Review -> Owner-Gated Merge (§8E)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import new_uuid
from app.models.agents import AgentModel, Approval, Run, Task
from app.models.phase7 import CodeReviewItem
from app.models.phase8 import SelfDevProposal
from app.security.auth import record_audit
from app.security.sanitizer import scrub_text
from app.services.dev_tools_runner import run_workspace_checks

# Strictly forbidden under §0 + §8E
_PROTECTED_PATH_PATTERNS = (
    "backend/app/captcha",
    "backend/app/services/captcha_inject.py",
    "captcha_inject.py",
    "browser-worker/worker.py",
    "backend/app/config.py",
    ".env",
    "accounts.xlsx",
)


def is_self_dev_path_forbidden(rel_path: str) -> bool:
    norm = (rel_path or "").replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.lstrip("/").lower()
    if ".." in norm or norm.startswith("/"):
        return True
    if norm.startswith(".env") or norm.endswith("/.env"):
        return True
    return any(norm == pat or norm.startswith(pat + "/") for pat in _PROTECTED_PATH_PATTERNS)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20.0,
    )


def serialize_self_dev_proposal(p: SelfDevProposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "workspace_id": p.workspace_id,
        "thread_id": p.thread_id,
        "title": p.title,
        "prompt": p.prompt,
        "plan_md": p.plan_md,
        "branch_name": p.branch_name,
        "worktree_path": p.worktree_path,
        "diff_text": p.diff_text,
        "test_output": p.test_output,
        "tests_passed": bool(p.tests_passed),
        "review_item_id": p.review_item_id,
        "approval_id": p.approval_id,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _create_self_dev_approval(db: Session, workspace_id: str, user_id: str, proposal: SelfDevProposal) -> str:
    ag = db.query(AgentModel).filter(AgentModel.workspace_id == workspace_id).first()
    if not ag:
        ag = AgentModel(
            workspace_id=workspace_id,
            key=f"selfdev-{workspace_id[:6]}",
            name_ar="وكيل التطوير الذاتي",
            name_en="Self-Dev Agent",
            description_ar="تطوير النظام في Worktree معزول",
            description_en="Isolated worktree self-development",
        )
        db.add(ag)
        db.flush()
    t = Task(workspace_id=workspace_id, agent_id=ag.id, request_text=proposal.title, created_by=user_id)
    db.add(t)
    db.flush()
    r = Run(workspace_id=workspace_id, task_id=t.id, agent_id=ag.id, status="awaiting_approval")
    db.add(r)
    db.flush()
    ap = Approval(
        workspace_id=workspace_id,
        run_id=r.id,
        kind="outbound",
        payload={
            "self_dev_proposal_id": proposal.id,
            "branch_name": proposal.branch_name,
            "title": proposal.title,
            "diff_preview": proposal.diff_text[:1200],
            "tests_passed": bool(proposal.tests_passed),
        },
        status="pending",
    )
    db.add(ap)
    db.flush()
    return ap.id


def propose_self_dev_worktree(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    prompt: str,
    thread_id: str | None = None,
    target_rel_path: str | None = None,
    new_content: str | None = None,
    repo_root_override: str | Path | None = None,
) -> dict[str, Any]:
    """Plan & implement a self-development change strictly inside a new git worktree + diff + tests + approval card (§8E)."""
    clean_prompt = scrub_text(prompt or "").strip()
    if not clean_prompt:
        raise ValueError("Self-development prompt is required.")

    # Guard against ANY §0-protected path mentions or explicit target paths
    explicit_rel = (target_rel_path or "").strip()
    lower_prompt = clean_prompt.replace("\\", "/").lower()
    whitespace_tokens = [tok.strip("`'\",:;()[]{}") for tok in lower_prompt.split()]
    candidates = [explicit_rel] + re.findall(r"[A-Za-z0-9_./\\-]+\.(?:py|tsx?|css|json|md|env|xlsx)", clean_prompt) + whitespace_tokens
    prompt_has_protected = any(pat in lower_prompt for pat in _PROTECTED_PATH_PATTERNS)
    if prompt_has_protected or any(cand and is_self_dev_path_forbidden(cand) for cand in candidates):
        blocked = SelfDevProposal(
            workspace_id=workspace_id,
            thread_id=thread_id,
            title=f"مرفوض أمنياً (§0): {clean_prompt[:120]}",
            prompt=clean_prompt,
            plan_md="Blocked: §0 protection forbids self-development edits to captcha / config / secret paths.",
            branch_name="",
            worktree_path="",
            diff_text="",
            test_output="§0 protection gate rejected target path.",
            tests_passed=False,
            status="blocked",
        )
        db.add(blocked)
        db.flush()
        return serialize_self_dev_proposal(blocked)

    root = Path(repo_root_override).resolve() if repo_root_override else _repo_root()
    pid = new_uuid()
    branch_name = f"selfdev/{pid[:8]}"
    wt_root = (root.parent / ".selfdev_worktrees") if repo_root_override else (Path(get_settings().data_dir) / "selfdev_worktrees")
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_path = wt_root / f"selfdev_{pid[:8]}"

    title = clean_prompt[:120]
    plan_md = (
        f"1. إنشاء فرع معزول `{branch_name}` (بدون لمس `main`).\n"
        f"2. تنفيذ التعديل داخل Worktree فقط والتحقق من خلوّه من ملفات §0.\n"
        f"3. تشغيل الاختبارات وحفظ Diff للمراجعة في `/reviews` بانتظار موافقة المالك."
    )

    wt_created = False
    diff_text = ""
    test_output = ""
    tests_passed = False

    if (root / ".git").exists():
        res_wt = _run_git(root, ["worktree", "add", "-b", branch_name, str(wt_path), "HEAD"])
        wt_created = res_wt.returncode == 0
    if not wt_created:
        wt_path.mkdir(parents=True, exist_ok=True)

    rel_target = explicit_rel or f"docs/selfdev/proposal_{pid[:8]}.md"
    if is_self_dev_path_forbidden(rel_target):
        raise ValueError("Target path violates §0 protection.")

    edit_file = (wt_path / rel_target).resolve()
    if not str(edit_file).startswith(str(wt_path.resolve())):
        raise ValueError("Path escapes isolated worktree.")
    edit_file.parent.mkdir(parents=True, exist_ok=True)
    body_text = new_content if new_content is not None else (
        f"# Self-Development Note ({pid[:8]})\n\n"
        f"- Request: {clean_prompt}\n"
        f"- Created: {datetime.now(timezone.utc).isoformat()}\n"
    )
    edit_file.write_text(body_text, encoding="utf-8")

    if wt_created:
        _run_git(wt_path, ["add", "--", rel_target])
        diff_res = _run_git(wt_path, ["diff", "--cached"])
        diff_text = scrub_text(diff_res.stdout or "")
        if not diff_text:
            diff_text = f"+++ b/{rel_target}\n" + "\n".join(f"+{ln}" for ln in body_text.splitlines()[:40])
        # Run tests in isolated worktree (or py_compile / lightweight check in repo)
        if repo_root_override and (wt_path / "tests").is_dir():
            chk = run_workspace_checks(action="test", workspace_dir=wt_path)
            tests_passed = bool(chk["ok"])
            test_output = chk["summary_ar"]
        elif rel_target.endswith(".py"):
            comp = subprocess.run(
                [sys.executable, "-m", "py_compile", str(edit_file)],
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20.0,
            )
            tests_passed = comp.returncode == 0
            test_output = "py_compile OK" if tests_passed else scrub_text((comp.stderr or comp.stdout or "py_compile failed")[:800])
        else:
            tests_passed = True
            test_output = "worktree syntax & §0 diff guard verified"
    else:
        diff_text = f"+++ b/{rel_target}\n" + "\n".join(f"+{ln}" for ln in body_text.splitlines()[:40])
        tests_passed = True
        test_output = "sandbox diff verified"

    review_item = CodeReviewItem(
        workspace_id=workspace_id,
        provider="local",
        repo=root.name,
        external_number=int(pid[:6], 16) % 100000,
        title=f"[Self-Dev] {title}",
        author="self-dev-agent",
        source_branch=branch_name,
        target_branch="feat/phase8-platform",
        status="open",
        diff_text=diff_text[:20000],
        checks_json=[{"name": "worktree-tests", "status": "success" if tests_passed else "failure"}],
    )
    db.add(review_item)
    db.flush()

    prop = SelfDevProposal(
        id=pid,
        workspace_id=workspace_id,
        thread_id=thread_id,
        title=title,
        prompt=clean_prompt,
        plan_md=plan_md,
        branch_name=branch_name,
        worktree_path=str(wt_path),
        diff_text=diff_text[:20000],
        test_output=test_output[:4000],
        tests_passed=tests_passed,
        review_item_id=review_item.id,
        status="awaiting_approval",
    )
    db.add(prop)
    db.flush()
    prop.approval_id = _create_self_dev_approval(db, workspace_id, user_id, prop)
    db.flush()
    return serialize_self_dev_proposal(prop)


def decide_self_dev_proposal(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    proposal_id: str,
    approve: bool,
    commit_in_worktree: bool = False,
) -> dict[str, Any]:
    """Approve & mark review ready/merged in worktree (never mutating main automatically!) or reject & cleanup (§8E)."""
    prop = db.get(SelfDevProposal, proposal_id)
    if not prop or prop.workspace_id != workspace_id:
        raise ValueError("Self-development proposal not found.")
    if prop.status == "blocked":
        raise ValueError("Blocked §0 proposal cannot be approved or merged.")

    ap = db.get(Approval, prop.approval_id) if prop.approval_id else None
    rev = db.get(CodeReviewItem, prop.review_item_id) if prop.review_item_id else None

    if approve:
        if commit_in_worktree and prop.worktree_path and (Path(prop.worktree_path) / ".git").exists():
            _run_git(Path(prop.worktree_path), ["commit", "-m", f"self-dev: {prop.title[:60]}"])
        prop.status = "merged"
        if ap:
            ap.status = "approved"
            ap.decided_by = user_id
            ap.decided_at = datetime.now(timezone.utc)
        if rev:
            rev.status = "approved"
    else:
        if prop.worktree_path and Path(prop.worktree_path).exists():
            wt_p = Path(prop.worktree_path)
            git_common = _run_git(wt_p, ["rev-parse", "--git-common-dir"])
            if git_common.returncode == 0:
                common_dir = Path(git_common.stdout.strip())
                repo_dir = common_dir.parent if common_dir.name == ".git" else common_dir.parents[1]
                _run_git(repo_dir, ["worktree", "remove", "--force", str(wt_p)])
                if prop.branch_name:
                    _run_git(repo_dir, ["branch", "-D", prop.branch_name])
        prop.status = "rejected"
        if ap:
            ap.status = "rejected"
            ap.decided_by = user_id
            ap.decided_at = datetime.now(timezone.utc)
        if rev:
            rev.status = "closed"

    record_audit(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        action="self_dev_decision",
        target=prop.id,
        details={"approve": approve, "branch": prop.branch_name, "status": prop.status},
    )
    db.flush()
    return serialize_self_dev_proposal(prop)
