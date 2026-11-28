"""Phase 5 Skills Store (skills.sh 1-click install), ~/.agent/skills + project discovery, and Progressive Disclosure (§5B)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

import jsonschema
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.models.phase5 import InstalledSkill

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

SKILLS_SH_STORE: list[dict[str, Any]] = [
    {
        "slug": "slug-formatter",
        "name": "URL Slug Formatter",
        "description": "تنسيق النصوص والروابط العربية والإنجليزية إلى Slugs نظيفة مع تحقّق JSON Schema.",
        "contract_md": (
            "---\n"
            "name: slug-formatter\n"
            "description: Format clean URL slugs and inspect canonical link lengths.\n"
            "---\n"
            "# URL Slug Formatter\n\n"
            "Tools: `format_slug(text)`\n"
            "See `references/guide.md` for SEO slug length recommendations.\n"
        ),
        "script_py": '''"""Typed skill module for slug-formatter."""
TOOLS = [
    {
        "name": "format_slug",
        "description": "Convert arbitrary title text into a clean lowercase slug.",
        "input_schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "minLength": 1},
                "max_len": {"type": "integer", "minimum": 4, "maximum": 120},
            },
        },
    }
]

import re

def run_tool(tool_name: str, arguments: dict):
    if tool_name != "format_slug":
        raise ValueError(f"Unknown tool: {tool_name}")
    raw = str(arguments.get("text") or "").strip().lower()
    max_len = int(arguments.get("max_len") or 64)
    slug = re.sub(r"[^\\w\\u0600-\\u06FF-]+", "-", raw).strip("-")[:max_len]
    return {"slug": slug, "length": len(slug)}
''',
        "references": {
            "guide.md": "# SEO Slug Guide\nKeep slugs short (3-5 words, <= 64 chars) with hyphen separators.\n",
        },
    },
    {
        "slug": "json-schema-validator",
        "name": "JSON Schema Inspector",
        "description": "فحص وتنسيق حزم JSON وتوليد ملخص الحقول بدقة قبل تمريرها للوكلاء.",
        "contract_md": (
            "---\n"
            "name: json-schema-validator\n"
            "description: Validate JSON payloads and summarize object keys.\n"
            "---\n"
            "# JSON Schema Inspector\n\n"
            "Tools: `inspect_payload(payload)`\n"
            "See `references/guide.md` on demand.\n"
        ),
        "script_py": '''"""Typed skill module for json-schema-validator."""
TOOLS = [
    {
        "name": "inspect_payload",
        "description": "Count top-level keys and verify JSON object validity.",
        "input_schema": {
            "type": "object",
            "required": ["payload"],
            "properties": {"payload": {"type": "object"}},
        },
    }
]

def run_tool(tool_name: str, arguments: dict):
    if tool_name != "inspect_payload":
        raise ValueError(f"Unknown tool: {tool_name}")
    p = arguments.get("payload") or {}
    return {"keys": sorted(p.keys()), "count": len(p)}
''',
        "references": {
            "guide.md": "# JSON Validation Reference\nValidate types strictly before downstream API or MCP calls.\n",
        },
    },
]


def _skills_root() -> Path:
    root = get_settings().data_dir / "installed_skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_skill_tools_from_py(skill_py: Path) -> list[dict[str, Any]]:
    if not skill_py.is_file():
        return []
    spec = importlib.util.spec_from_file_location(f"uas_skill_{skill_py.parent.name}", str(skill_py))
    if not spec or not spec.loader:
        return []
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(getattr(mod, "TOOLS", []) or [])


def _register_dir_as_skill(db: Session, workspace_id: str, skill_dir: Path, source: str) -> InstalledSkill | None:
    md_path = skill_dir / "SKILL.md"
    if not md_path.is_file():
        return None
    slug = re.sub(r"[^a-z0-9_-]+", "-", skill_dir.name.strip().lower()).strip("-") or "skill"
    contract_md = md_path.read_text(encoding="utf-8", errors="replace")
    py_path = skill_dir / "skill.py"
    tools = _read_skill_tools_from_py(py_path)
    refs_dir = skill_dir / "references"
    refs = sorted(p.name for p in refs_dir.iterdir() if p.is_file()) if refs_dir.is_dir() else []

    existing = (
        db.query(InstalledSkill)
        .filter(InstalledSkill.workspace_id == workspace_id, InstalledSkill.slug == slug)
        .first()
    )
    if existing is None:
        existing = InstalledSkill(
            workspace_id=workspace_id,
            slug=slug,
            name=slug,
            description=contract_md.splitlines()[0][:200] if contract_md else slug,
            source=source,
            install_path=str(skill_dir),
            contract_md=contract_md[:8000],
            has_script=py_path.is_file(),
            tools_json=tools,
            references_json=refs,
            enabled=True,
        )
        db.add(existing)
    else:
        existing.install_path = str(skill_dir)
        existing.contract_md = contract_md[:8000]
        existing.has_script = py_path.is_file()
        existing.tools_json = tools
        existing.references_json = refs
    db.flush()
    return existing


def discover_compatible_skills(db: Session, workspace_id: str) -> list[InstalledSkill]:
    """Scan ~/.agent/skills, project .agents/skills, ./skills, and data/installed_skills."""
    candidates: list[tuple[Path, str]] = [
        (Path.home() / ".agent" / "skills", "local"),
        (REPO_ROOT / ".agents" / "skills", "project"),
        (REPO_ROOT / "skills", "project"),
        (_skills_root(), "skills_sh"),
    ]
    found: list[InstalledSkill] = []
    seen_slugs: set[str] = set()
    for base_dir, src in candidates:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                row = _register_dir_as_skill(db, workspace_id, child, src)
                if row and row.slug not in seen_slugs:
                    seen_slugs.add(row.slug)
                    found.append(row)
    db.flush()
    return found


def install_skill_one_click(
    db: Session,
    *,
    workspace_id: str,
    slug: str,
    custom_contract_md: str | None = None,
    custom_script_py: str | None = None,
) -> InstalledSkill:
    clean_slug = slug.strip().lower()
    if not _SLUG_RE.match(clean_slug):
        raise ValueError(f"Invalid skill slug: {slug}")

    tpl = next((s for s in SKILLS_SH_STORE if s["slug"] == clean_slug), None)
    if tpl is None and not custom_contract_md:
        raise ValueError(f"Skill '{clean_slug}' not found in skills.sh catalog.")

    target_dir = _skills_root() / clean_slug
    refs_dir = target_dir / "references"
    target_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    contract_md = custom_contract_md or str(tpl["contract_md"])
    script_py = custom_script_py if custom_script_py is not None else str(tpl.get("script_py") or "")
    (target_dir / "SKILL.md").write_text(contract_md, encoding="utf-8")
    if script_py.strip():
        (target_dir / "skill.py").write_text(script_py, encoding="utf-8")
    if tpl:
        for ref_name, ref_body in (tpl.get("references") or {}).items():
            (refs_dir / ref_name).write_text(str(ref_body), encoding="utf-8")

    row = _register_dir_as_skill(db, workspace_id, target_dir, "skills_sh")
    assert row is not None
    if tpl:
        row.name = str(tpl["name"])
        row.description = str(tpl["description"])
    db.flush()
    return row


def read_skill_reference(db: Session, workspace_id: str, slug: str, ref_name: str) -> str:
    """Progressive disclosure: load references/<ref_name> only on explicit demand."""
    row = (
        db.query(InstalledSkill)
        .filter(InstalledSkill.workspace_id == workspace_id, InstalledSkill.slug == slug)
        .first()
    )
    if not row:
        raise ValueError("Skill not installed.")
    refs_root = (Path(row.install_path) / "references").resolve()
    target = (refs_root / ref_name).resolve()
    if refs_root not in target.parents or not target.is_file():
        raise ValueError("Reference file not found.")
    return target.read_text(encoding="utf-8", errors="replace")


def run_installed_skill(
    db: Session,
    *,
    workspace_id: str,
    slug: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = (
        db.query(InstalledSkill)
        .filter(InstalledSkill.workspace_id == workspace_id, InstalledSkill.slug == slug, InstalledSkill.enabled.is_(True))
        .first()
    )
    if not row:
        raise ValueError("Installed skill not found or disabled.")
    py_path = Path(row.install_path) / "skill.py"
    if not py_path.is_file():
        raise ValueError("Skill has no executable skill.py.")

    spec = importlib.util.spec_from_file_location(f"uas_skill_exec_{row.slug}", str(py_path))
    if not spec or not spec.loader:
        raise RuntimeError("Could not load skill.py.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tools = list(getattr(mod, "TOOLS", []) or [])
    t_def = next((t for t in tools if t.get("name") == tool_name), None)
    if t_def and t_def.get("input_schema"):
        jsonschema.validate(instance=dict(arguments or {}), schema=t_def["input_schema"])

    fn = getattr(mod, "run_tool", None)
    if not callable(fn):
        raise RuntimeError("skill.py missing run_tool(tool_name, arguments).")
    res = fn(tool_name, dict(arguments or {}))
    return {"ok": True, "slug": row.slug, "tool_name": tool_name, "result": res}


def serialize_skill(s: InstalledSkill) -> dict[str, Any]:
    return {
        "id": s.id,
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "source": s.source,
        "install_path": s.install_path,
        "contract_md": s.contract_md,
        "has_script": bool(s.has_script),
        "tools": list(s.tools_json or []),
        "references": list(s.references_json or []),
        "enabled": bool(s.enabled),
    }
