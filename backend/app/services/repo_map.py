"""Aider-style Relevance-Ranked, Token-Budgeted Repository Map with In-Memory Cache (§8H.1)."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import time
from typing import Any

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_SEC = 45.0
_SKIP_PARTS = {".git", "node_modules", ".next", ".venv", "__pycache__", "references", "worktrees", "data"}
_TS_SYMBOL_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z0-9_]+)")


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _extract_symbols(path: Path) -> list[str]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    syms: list[str] = []
    if path.suffix == ".py":
        try:
            tree = ast.parse(src)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))][:5]
                    m_txt = f"({','.join(methods)})" if methods else ""
                    syms.append(f"class {node.name}{m_txt}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    syms.append(f"def {node.name}")
        except SyntaxError:
            pass
    else:
        for ln in src.splitlines():
            m = _TS_SYMBOL_RE.match(ln)
            if m:
                syms.append(m.group(1))
            if len(syms) >= 12:
                break
    return syms[:16]


def build_repo_map(
    *,
    repo_root: str | Path | None = None,
    query: str = "",
    token_budget: int = 1800,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Index codebase symbols, rank by relevance to `query`, and format within `token_budget` (~4 chars/token)."""
    root = Path(repo_root).resolve() if repo_root else _default_repo_root()
    cache_key = str(root)
    now = time.monotonic()

    entries: list[dict[str, Any]] | None = None
    if use_cache and cache_key in _CACHE:
        ts, cached_entries = _CACHE[cache_key]
        if (now - ts) < _CACHE_TTL_SEC:
            entries = cached_entries

    cached_hit = entries is not None
    if entries is None:
        entries = []
        for fp in sorted(root.rglob("*")):
            if not fp.is_file() or fp.suffix not in {".py", ".ts", ".tsx"}:
                continue
            if any(part in _SKIP_PARTS for part in fp.parts):
                continue
            rel = str(fp.relative_to(root)).replace("\\", "/")
            syms = _extract_symbols(fp)
            entries.append({"path": rel, "symbols": syms, "size": fp.stat().st_size})
        _CACHE[cache_key] = (now, entries)

    q_tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]{2,}", query or "")]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in entries:
        hay = f"{item['path']} {' '.join(item['symbols'])}".lower()
        score = sum(3 if q in item["path"].lower() else (2 if q in hay else 0) for q in q_tokens)
        if not item["symbols"] and not q_tokens:
            score -= 1
        ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], len(pair[1]["symbols"])), reverse=True)

    char_budget = max(400, int(token_budget) * 4)
    lines: list[str] = []
    selected: list[dict[str, Any]] = []
    used_chars = 0
    for score, item in ranked:
        sym_txt = ", ".join(item["symbols"][:8]) if item["symbols"] else "(module)"
        line = f"{item['path']}: {sym_txt}"
        if used_chars + len(line) + 1 > char_budget and selected:
            break
        lines.append(line)
        selected.append({**item, "score": score})
        used_chars += len(line) + 1

    return {
        "repo_root": str(root),
        "total_files_indexed": len(entries),
        "included_files": len(selected),
        "token_budget": token_budget,
        "estimated_tokens": max(1, used_chars // 4),
        "cached": cached_hit,
        "files": selected,
        "map_text": "\n".join(lines),
    }
