"""Auto-detecting Test/Lint Runner (/test, /lint) & Opt-In Git Commit / Undo (§8H.2, §8H.3)."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from app.security.sanitizer import scrub_text


def _repo_root(workspace_dir: str | Path | None = None) -> Path:
    if workspace_dir:
        p = Path(workspace_dir).resolve()
        if p.exists():
            return p
    return Path(__file__).resolve().parents[3]


def _detect_commands(root: Path, action: str) -> list[tuple[str, list[str], Path]]:
    cmds: list[tuple[str, list[str], Path]] = []
    backend = root / "backend"
    venv_py = backend / ".venv" / "Scripts" / "python.exe"
    py_exe = str(venv_py) if venv_py.exists() else (sys.executable or shutil.which("python") or "python")

    if action == "test":
        if (root / "tests").is_dir() or (root / "pyproject.toml").is_file():
            cmds.append(("pytest", [py_exe, "-m", "pytest", "-q"], root))
        elif (backend / "tests").is_dir():
            cmds.append(("pytest-phase8", [py_exe, "-m", "pytest", "tests/test_security_killswitch.py", "-q"], backend))
        if (root / "package.json").is_file() and shutil.which("npm"):
            cmds.append(("npm-test", ["npm", "test", "--", "--watchAll=false"], root))
        if (root / "Cargo.toml").is_file() and shutil.which("cargo"):
            cmds.append(("cargo-test", ["cargo", "test"], root))
        if (root / "go.mod").is_file() and shutil.which("go"):
            cmds.append(("go-test", ["go", "test", "./..."], root))
    elif action == "lint":
        if shutil.which("ruff"):
            cmds.append(("ruff", ["ruff", "check", "."], root))
        elif (backend / "app").is_dir():
            cmds.append(("py_compile", [py_exe, "-m", "compileall", "-q", "app"], backend))
        if (root / "web" / "node_modules" / ".bin" / "eslint").exists() or shutil.which("eslint"):
            cmds.append(("eslint", ["npx", "eslint", "."], root / "web" if (root / "web").is_dir() else root))
    return cmds


def run_workspace_checks(
    *,
    action: str = "test",
    workspace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run auto-detected /test, /lint, or /git-undo safely and return structured summary (§8H.2-3)."""
    root = _repo_root(workspace_dir)
    if action == "git_undo":
        proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
        )
        return {
            "action": "git_undo",
            "ok": True,
            "note": "للتراجع الدقيق دون خسارة غير مقصودة استخدم `/api/threads/{id}/undo` في Worktree البرمجة أو راجع git status.",
            "summary_ar": f"↩️ **فحص التراجع (`/git-undo`):** حالة العمل الحالية:\n```\n{scrub_text(proc.stdout or 'clean')[:800]}\n```",
        }

    detected = _detect_commands(root, action)
    if not detected:
        return {
            "action": action,
            "ok": False,
            "results": [],
            "summary_ar": f"⚠️ لم يتم العثور على أداة `{action}` قياسية في `{root}`.",
        }

    results: list[dict[str, Any]] = []
    all_ok = True
    for label, cmd, cwd in detected[:2]:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45.0,
        )
        ok = proc.returncode == 0
        if not ok:
            all_ok = False
        out = scrub_text((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-1500:]
        results.append({"runner": label, "command": " ".join(cmd), "exit_code": proc.returncode, "ok": ok, "output": out})

    badge = "✅" if all_ok else "❌"
    summary_ar = f"{badge} **نتيجة فحص `/{action}`:** " + " · ".join(f"`{r['runner']}`={'OK' if r['ok'] else 'FAIL'}" for r in results)
    return {"action": action, "ok": all_ok, "results": results, "summary_ar": summary_ar}


def opt_in_auto_git_commit(*, repo_dir: str | Path, commit_message: str, allow: bool = False) -> dict[str, Any]:
    """Commit staged/worktree changes ONLY inside explicit non-main branch when opt-in `allow=True` (§8H.3)."""
    if not allow:
        return {"committed": False, "reason": "opt_in_required"}
    cwd = Path(repo_dir).resolve()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=str(cwd), capture_output=True, text=True).stdout.strip()
    if branch in {"", "main", "master"}:
        return {"committed": False, "reason": f"refused_on_branch:{branch or 'detached'}"}
    clean_msg = scrub_text(commit_message or "chore(agent): update worktree")[:120]
    add_p = subprocess.run(["git", "add", "-u"], cwd=str(cwd), capture_output=True, text=True)
    if add_p.returncode != 0:
        return {"committed": False, "reason": add_p.stderr[:200]}
    c_p = subprocess.run(["git", "commit", "-m", clean_msg], cwd=str(cwd), capture_output=True, text=True)
    return {
        "committed": c_p.returncode == 0,
        "branch": branch,
        "message": clean_msg,
        "output": scrub_text((c_p.stdout or c_p.stderr or "")[:400]),
    }
