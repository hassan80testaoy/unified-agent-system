"""Git worktrees, real diff inspection, and pre-message disk file snapshots for Undo (§1E, §1F)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.coding import ThreadFileSnapshot

_SAFE_BRANCH_RE = re.compile(r"[^a-zA-Z0-9._/-]+")


def _run_git(cwd: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return res


def ensure_git_repo(repo_dir: Path) -> Path:
    """Initialize a local git repo on `main` if not already initialized, with an initial commit."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        res = _run_git(repo_dir, ["init", "-b", "main"], check=False)
        if res.returncode != 0:
            _run_git(repo_dir, ["init"])
            _run_git(repo_dir, ["branch", "-M", "main"], check=False)
        _run_git(repo_dir, ["config", "user.name", "Unified Agent"])
        _run_git(repo_dir, ["config", "user.email", "agent@local.invalid"])

    head_chk = _run_git(repo_dir, ["rev-parse", "--verify", "HEAD"], check=False)
    if head_chk.returncode != 0:
        if not any(p.name != ".git" for p in repo_dir.iterdir()):
            (repo_dir / ".gitkeep").write_text("", encoding="utf-8")
        _run_git(repo_dir, ["add", "-A"])
        _run_git(repo_dir, ["commit", "-m", "chore: initial coding workspace"])
    return repo_dir


def sanitize_branch(branch_name: str) -> str:
    cleaned = _SAFE_BRANCH_RE.sub("-", (branch_name or "work").strip()).strip("/-")
    return cleaned or "work"


def create_worktree(repo_dir: Path, branch_name: str, worktree_dir: Path) -> tuple[str, Path]:
    """Create an isolated branch + working tree folder under `worktree_dir`."""
    ensure_git_repo(repo_dir)
    branch = sanitize_branch(branch_name)
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    if worktree_dir.exists():
        _run_git(repo_dir, ["worktree", "remove", "--force", str(worktree_dir)], check=False)
    # Delete stale branch only if recreating an unmerged sandbox scratch branch fails? Keep existing or suffix
    chk = _run_git(repo_dir, ["rev-parse", "--verify", branch], check=False)
    if chk.returncode == 0:
        _run_git(repo_dir, ["worktree", "add", str(worktree_dir), branch])
    else:
        _run_git(repo_dir, ["worktree", "add", "-b", branch, str(worktree_dir)])
    _run_git(worktree_dir, ["config", "user.name", "Unified Agent"], check=False)
    _run_git(worktree_dir, ["config", "user.email", "agent@local.invalid"], check=False)
    return branch, worktree_dir


def commit_workspace(repo_dir: Path, message: str) -> str | None:
    """Commit all staged/untracked changes in `repo_dir` and return HEAD sha if changed."""
    ensure_git_repo(repo_dir)
    _run_git(repo_dir, ["add", "-A"])
    status = _run_git(repo_dir, ["status", "--porcelain"]).stdout.strip()
    if not status:
        return None
    _run_git(repo_dir, ["commit", "-m", message])
    return _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()


def merge_worktree(repo_dir: Path, branch_name: str, *, worktree_dir: Path | None = None) -> dict[str, Any]:
    """Commit any clean worktree changes and merge `branch_name` into `repo_dir`'s active branch."""
    if worktree_dir and worktree_dir.exists():
        commit_workspace(worktree_dir, f"feat: changes from {branch_name}")
    ensure_git_repo(repo_dir)
    target_branch = _run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    res = _run_git(repo_dir, ["merge", "--no-ff", branch_name, "-m", f"merge: {branch_name}"], check=False)
    if res.returncode != 0:
        return {
            "merged": False,
            "target_branch": target_branch,
            "source_branch": branch_name,
            "error": (res.stderr or res.stdout).strip(),
        }
    sha = _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip()
    return {
        "merged": True,
        "target_branch": target_branch,
        "source_branch": branch_name,
        "head_sha": sha,
    }


def compute_workspace_diffs(workspace_dir: Path) -> list[dict[str, Any]]:
    """Return modified & untracked files in `workspace_dir` with unified diffs."""
    if not workspace_dir.exists():
        return []
    ensure_git_repo(workspace_dir)
    status_out = _run_git(workspace_dir, ["status", "--porcelain"]).stdout.splitlines()
    items: list[dict[str, Any]] = []
    for raw in status_out:
        if len(raw) < 4:
            continue
        code = raw[:2].strip()
        rel = raw[3:].strip().strip('"')
        full = (workspace_dir / rel).resolve()
        if code == "??" and full.is_file():
            content = full.read_text(encoding="utf-8", errors="replace")
            diff_txt = "\n".join([f"+++ {rel}"] + [f"+{line}" for line in content.splitlines()])
        else:
            diff_txt = _run_git(workspace_dir, ["diff", "--", rel], check=False).stdout
            if not diff_txt and full.is_file():
                diff_txt = _run_git(workspace_dir, ["diff", "HEAD", "--", rel], check=False).stdout
        items.append({"path": rel, "status": code or "M", "diff": diff_txt})
    return items


def _safe_rel(workspace_dir: Path, rel_path: str) -> tuple[str, Path]:
    clean_rel = rel_path.replace("\\", "/").lstrip("/")
    root = workspace_dir.resolve()
    full = (root / clean_rel).resolve()
    if root not in full.parents and full != root:
        raise ValueError(f"Unsafe path outside workspace: {rel_path}")
    return clean_rel, full


def snapshot_files_before_edit(
    db: Session,
    *,
    workspace_id: str,
    thread_id: str,
    message_seq: int,
    workspace_dir: Path,
    rel_paths: list[str],
) -> list[ThreadFileSnapshot]:
    """Capture exact on-disk file state before message `message_seq` modifies files."""
    created: list[ThreadFileSnapshot] = []
    seen: set[str] = set()
    for raw_rel in rel_paths:
        clean_rel, full = _safe_rel(workspace_dir, raw_rel)
        if clean_rel in seen:
            continue
        seen.add(clean_rel)
        existed = full.exists() and full.is_file()
        content = full.read_text(encoding="utf-8", errors="replace") if existed else None
        row = ThreadFileSnapshot(
            workspace_id=workspace_id,
            thread_id=thread_id,
            message_seq=message_seq,
            rel_path=clean_rel,
            existed=existed,
            content_text=content,
        )
        db.add(row)
        created.append(row)
    db.flush()
    return created


def rewind_files_to_seq(
    db: Session,
    *,
    thread_id: str,
    to_seq: int,
    workspace_dir: Path,
) -> dict[str, Any]:
    """Rewind files on disk to their exact state prior to `to_seq + 1`."""
    snaps = (
        db.query(ThreadFileSnapshot)
        .filter(ThreadFileSnapshot.thread_id == thread_id, ThreadFileSnapshot.message_seq > to_seq)
        .order_by(ThreadFileSnapshot.message_seq.asc(), ThreadFileSnapshot.created_at.asc())
        .all()
    )
    earliest_by_path: dict[str, ThreadFileSnapshot] = {}
    for s in snaps:
        if s.rel_path not in earliest_by_path:
            earliest_by_path[s.rel_path] = s

    rewound_paths: list[str] = []
    for rel, snap in earliest_by_path.items():
        _, full = _safe_rel(workspace_dir, rel)
        if snap.existed:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(snap.content_text or "", encoding="utf-8")
        else:
            if full.exists() and full.is_file():
                full.unlink()
        rewound_paths.append(rel)

    for s in snaps:
        db.delete(s)
    db.flush()
    return {"to_seq": to_seq, "rewound_files": rewound_paths}
