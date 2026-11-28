"""@-mention context expansion (@file:path, @diff, @Git-Log, @tree, @problems, @search:term — Hermes pattern §8H.5)."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

from app.security.sanitizer import scrub_text

_MENTION_RE = re.compile(
    r"(@file:[^\s]+|@search:[^\s]+|@diff|@Git-Log|@git-log|@tree|@problems)",
    re.IGNORECASE,
)

_FORBIDDEN_FILE_PATTERNS = (
    ".env",
    ".key",
    ".db",
    ".sqlite",
    "captcha_inject.py",
    "browser-worker/worker.py",
    "accounts.xlsx",
)


def _repo_root(workspace_dir: str | None = None) -> Path:
    if workspace_dir:
        p = Path(workspace_dir).resolve()
        if p.exists() and p.is_dir():
            return p
    return Path(__file__).resolve().parents[3]


def _run_git(cwd: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6.0,
        )
        return scrub_text((proc.stdout or proc.stderr or "").strip())[:3500]
    except Exception as exc:
        return f"(git unavailable: {exc})"


def expand_at_mentions(text: str, *, workspace_dir: str | None = None, max_total_chars: int = 8000) -> tuple[str, list[dict[str, Any]]]:
    """Expand inline @-mentions into bounded context blocks and return `(context_block, metadata)`."""
    root = _repo_root(workspace_dir)
    mentions = _MENTION_RE.findall(text or "")
    if not mentions:
        return "", []

    blocks: list[str] = []
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()

    for token in mentions:
        low = token.lower()
        if low in seen:
            continue
        seen.add(low)

        if low.startswith("@file:"):
            rel = token[6:].strip().lstrip("/\\")
            norm_rel = rel.replace("\\", "/")
            if ".." in norm_rel or any(pat in norm_rel.lower() for pat in _FORBIDDEN_FILE_PATTERNS):
                blocks.append(f"### @file:{rel}\n[blocked: sensitive or out-of-root path]")
                expanded.append({"mention": token, "status": "blocked"})
                continue
            target = (root / rel).resolve()
            if not str(target).startswith(str(root)) or not target.is_file():
                blocks.append(f"### @file:{rel}\n[not found]")
                expanded.append({"mention": token, "status": "missing"})
                continue
            snippet = scrub_text(target.read_text(encoding="utf-8", errors="replace"))[:2400]
            blocks.append(f"### @file:{norm_rel}\n```\n{snippet}\n```")
            expanded.append({"mention": token, "status": "ok", "chars": len(snippet)})

        elif low == "@diff":
            diff_txt = _run_git(root, ["diff", "--stat"]) + "\n" + _run_git(root, ["diff", "-U2"])
            blocks.append(f"### @diff\n```diff\n{diff_txt[:2400]}\n```")
            expanded.append({"mention": token, "status": "ok"})

        elif low in {"@git-log", "@Git-Log"}:
            log_txt = _run_git(root, ["log", "--oneline", "-n", "12"])
            blocks.append(f"### @Git-Log\n```\n{log_txt}\n```")
            expanded.append({"mention": token, "status": "ok"})

        elif low == "@tree":
            files = sorted(
                str(p.relative_to(root)).replace("\\", "/")
                for p in root.rglob("*")
                if p.is_file()
                and not any(part in {".git", "node_modules", ".next", ".venv", "__pycache__", "references"} for part in p.parts)
            )[:60]
            blocks.append("### @tree\n```\n" + "\n".join(files) + "\n```")
            expanded.append({"mention": token, "status": "ok", "count": len(files)})

        elif low == "@problems":
            st = _run_git(root, ["status", "--short"])
            blocks.append(f"### @problems (git workspace state)\n```\n{st or 'clean'}\n```")
            expanded.append({"mention": token, "status": "ok"})

        elif low.startswith("@search:"):
            term = token[8:].strip()
            hits: list[str] = []
            if term:
                for p in root.rglob("*"):
                    if not p.is_file() or p.suffix not in {".py", ".ts", ".tsx", ".md", ".yaml", ".yml"}:
                        continue
                    if any(part in {".git", "node_modules", ".next", ".venv", "references"} for part in p.parts):
                        continue
                    try:
                        txt = p.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for idx, ln in enumerate(txt.splitlines(), start=1):
                        if term.lower() in ln.lower():
                            hits.append(f"{p.relative_to(root)}:{idx}: {ln.strip()[:120]}")
                            if len(hits) >= 15:
                                break
                    if len(hits) >= 15:
                        break
            blocks.append(f"### @search:{term}\n```\n" + ("\n".join(hits) if hits else "(no matches)") + "\n```")
            expanded.append({"mention": token, "status": "ok", "hits": len(hits)})

    joined = "\n\n".join(blocks)[:max_total_chars]
    return joined, expanded
