"""Wide Research Parallel Sub-Agent Fan-Out (up to 20 isolated items + deliverable artifact — Manus pattern §8I.2)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, new_uuid
from app.models.agents import Artifact
from app.models.phase8 import WideResearchJob
from app.security.sanitizer import scrub_text
from app.services.llm_router import LLMRouter

MAX_WIDE_ITEMS = 20


def serialize_wide_job(job: WideResearchJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "workspace_id": job.workspace_id,
        "thread_id": job.thread_id,
        "query": job.query,
        "items": list(job.items_json or []),
        "results": list(job.results_json or []),
        "status": job.status,
        "progress_done": int(job.progress_done or 0),
        "progress_total": int(job.progress_total or 0),
        "artifact_id": job.artifact_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def _research_single_item(workspace_id: str, query: str, idx: int, item: str) -> dict[str, Any]:
    """Run one isolated fresh-context sub-agent lookup for a single Wide Research item."""
    sub_db = SessionLocal()
    try:
        res = LLMRouter(sub_db, workspace_id).route(
            task_type="research",
            tier="cheap",
            prompt=(
                f"Research item #{idx + 1} for query '{query}':\n"
                f"Target item: {item}\n\n"
                "Return concise structured findings in Arabic (key facts, recommendation, confidence)."
            ),
            system_prompt="You execute one isolated Wide Research sub-task with equal rigor for item #1 and item #N.",
            use_cache=False,
        )
        sub_db.commit()
        return {
            "index": idx + 1,
            "item": item,
            "status": "done",
            "provider_id": res.get("provider_id"),
            "model": res.get("model"),
            "is_mock": bool(res.get("is_mock", False)),
            "finding": scrub_text(str(res.get("raw_text") or "")).strip()[:800],
        }
    except Exception as exc:
        sub_db.rollback()
        return {
            "index": idx + 1,
            "item": item,
            "status": "error",
            "is_mock": False,
            "finding": f"تعذر الفحص: {type(exc).__name__}",
        }
    finally:
        sub_db.close()


def run_wide_research(
    db: Session,
    *,
    workspace_id: str,
    query: str,
    items: list[str],
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Execute up to 20 Wide Research items in parallel isolated contexts & export Markdown/CSV deliverable (§8I.2)."""
    clean_q = scrub_text(query or "").strip()
    clean_items = [scrub_text(str(x)).strip() for x in (items or []) if scrub_text(str(x)).strip()][:MAX_WIDE_ITEMS]
    if not clean_items:
        raise ValueError("At least one research item is required.")

    job_id = new_uuid()
    job = WideResearchJob(
        id=job_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        query=clean_q,
        items_json=clean_items,
        results_json=[],
        status="running",
        progress_done=0,
        progress_total=len(clean_items),
    )
    db.add(job)
    db.flush()

    workers = min(8, len(clean_items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda pair: _research_single_item(workspace_id, clean_q, pair[0], pair[1]), enumerate(clean_items)))

    out_dir = get_settings().artifacts_dir / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"wide_{job_id[:8]}.md"
    csv_path = out_dir / f"wide_{job_id[:8]}.csv"

    md_lines = [f"# تقرير البحث الموسّع: {clean_q}", ""]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["index", "item", "status", "model", "finding"])
    for r in results:
        md_lines.append(f"## {r['index']}. {r['item']} (`{r.get('model') or 'auto'}`)")
        md_lines.append(r["finding"])
        md_lines.append("")
        writer.writerow([r["index"], r["item"], r["status"], r.get("model") or "", r["finding"]])

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    csv_path.write_text(buf.getvalue(), encoding="utf-8")

    art = Artifact(
        workspace_id=workspace_id,
        thread_id=None,
        run_id=None,
        kind="report",
        path=str(md_path),
        size=md_path.stat().st_size,
        metadata_json={"title": f"Wide Research: {clean_q[:60]}", "csv_path": str(csv_path), "items_count": len(results)},
    )
    db.add(art)
    db.flush()

    job.results_json = results
    job.progress_done = len(results)
    job.status = "done"
    job.artifact_id = art.id
    db.flush()

    any_mock = any(bool(r.get("is_mock")) for r in results)
    prefix = "محاكاة — " if any_mock else ""
    out = serialize_wide_job(job)
    out["preview_url"] = f"/api/artifacts/{art.id}/preview"
    out["report_ar"] = (
        f"{prefix}📚 **اكتمل البحث المتوازي (`{len(results)}` عناصر بسياقات معزولة متكافئة):**\n"
        f"- **المخرَج:** `{out['preview_url']}`\n"
        + "\n".join(f"- **{r['index']}. {r['item']}:** {r['finding'][:120]}" for r in results[:5])
    )
    return out
