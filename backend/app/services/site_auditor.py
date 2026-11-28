"""Real browser & HTTP Site Auditor with line-level evidence, screenshots, broken link checks, and live backlink verify (§7A.3)."""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from app.agents.backlink.agent import verify_live_link_url
from app.config import get_settings
from app.db import new_uuid
from app.models.agents import Report
from app.models.phase7 import SiteAudit
from app.security.sanitizer import scrub_text
from app.services.browser_agent import BrowserWorkerClient
from app.services.browser_worker_manager import probe_browser_worker, resolve_browser_worker_url


class _HtmlEvidenceParser(HTMLParser):
    """HTML parser recording exact 1-based line numbers for SEO, indexability, speed, and link evidence."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_text: str = ""
        self.title_line: int | None = None
        self._in_title = False
        self.h1_items: list[tuple[str, int]] = []
        self._in_h1 = False
        self._h1_buf: list[str] = []
        self._h1_line: int = 1
        self.meta_description: str | None = None
        self.meta_desc_line: int | None = None
        self.meta_robots: str | None = None
        self.meta_robots_line: int | None = None
        self.canonical: str | None = None
        self.canonical_line: int | None = None
        self.blocking_scripts: list[tuple[str, int]] = []
        self.images_missing_alt: list[tuple[str, int]] = []
        self.links: list[tuple[str, str, int]] = []
        self._in_a = False
        self._a_href = ""
        self._a_line = 1
        self._a_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line, _ = self.getpos()
        attr = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        if t == "title" and self.title_line is None:
            self._in_title = True
            self.title_line = line
        elif t == "h1":
            self._in_h1 = True
            self._h1_line = line
            self._h1_buf = []
        elif t == "meta":
            name = attr.get("name", "").lower()
            content = attr.get("content", "").strip()
            if name == "description" and self.meta_description is None:
                self.meta_description = content
                self.meta_desc_line = line
            elif name == "robots":
                self.meta_robots = content
                self.meta_robots_line = line
        elif t == "link" and "canonical" in attr.get("rel", "").lower():
            if self.canonical is None:
                self.canonical = attr.get("href", "").strip()
                self.canonical_line = line
        elif t == "script" and attr.get("src"):
            if "async" not in attr and "defer" not in attr and attr.get("type", "").lower() != "module":
                self.blocking_scripts.append((attr["src"][:120], line))
        elif t == "img":
            if not attr.get("alt", "").strip():
                self.images_missing_alt.append((attr.get("src", "(img)")[:120], line))
        elif t == "a" and attr.get("href"):
            self._in_a = True
            self._a_href = attr["href"].strip()
            self._a_line = line
            self._a_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data
        if self._in_h1:
            self._h1_buf.append(data)
        if self._in_a:
            self._a_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = False
            self.title_text = self.title_text.strip()
        elif t == "h1" and self._in_h1:
            self._in_h1 = False
            self.h1_items.append(("".join(self._h1_buf).strip(), self._h1_line))
        elif t == "a" and self._in_a:
            self._in_a = False
            self.links.append(("".join(self._a_buf).strip(), self._a_href, self._a_line))


def _line_snippet(lines: list[str], line_no: int | None) -> str:
    if not line_no or line_no < 1 or line_no > len(lines):
        return ""
    return scrub_text(lines[line_no - 1].strip())[:180]


def _make_finding(
    *,
    fid: str,
    category: str,
    severity: str,
    title_ar: str,
    title_en: str,
    page: str,
    line: int | None,
    snippet: str,
    screenshot_url: str | None,
    fix_ar: str,
    fix_en: str,
) -> dict[str, Any]:
    return {
        "id": fid,
        "category": category,
        "severity": severity,
        "title_ar": title_ar,
        "title_en": title_en,
        "evidence": {
            "page": page,
            "line": line,
            "snippet": snippet,
            "screenshot_url": screenshot_url,
        },
        "fix_ar": fix_ar,
        "fix_en": fix_en,
    }


async def audit_site_url(
    db: Session,
    *,
    workspace_id: str,
    url: str,
    target_backlink_url: str | None = None,
    profile_url: str | None = None,
    max_links_check: int = 12,
) -> dict[str, Any]:
    """Perform real Playwright + HTTP technical SEO & backlink audit with evidence per finding."""
    clean_url = (url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        clean_url = f"https://{clean_url}"

    settings = get_settings()
    worker_url = resolve_browser_worker_url()
    screenshot_url: str | None = None
    network_path = "direct"
    proxy_fallback_reason: str | None = None

    if worker_url and probe_browser_worker(worker_url).get("ok"):
        client = BrowserWorkerClient(worker_url)
        sess_id = f"audit_{new_uuid()[:10]}"
        raw_proxy = (settings.residential_proxy or "").strip() or None
        try:
            snap = await client.open(
                session_id=sess_id,
                url=clean_url,
                proxy=None if clean_url.startswith(("http://127.0.0.1", "http://localhost")) else raw_proxy,
                allow_direct_fallback=True,
                db=db,
            )
            screenshot_url = snap.get("screenshot_url")
            network_path = str(snap.get("network_path") or network_path)
            proxy_fallback_reason = snap.get("proxy_fallback_reason")
        except Exception:
            pass
        finally:
            await client.close(session_id=sess_id)

    html = ""
    headers: dict[str, str] = {}
    status_code = 0
    ttfb_ms = 0
    fetch_error: str | None = None
    robots_txt = ""
    t0 = time.monotonic()

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as hclient:
            resp = await hclient.get(clean_url)
            ttfb_ms = int((time.monotonic() - t0) * 1000)
            status_code = resp.status_code
            headers = {k.lower(): v for k, v in resp.headers.items()}
            html = resp.text
            parsed_u = urlparse(str(resp.url))
            robots_url = f"{parsed_u.scheme}://{parsed_u.netloc}/robots.txt"
            try:
                r_resp = await hclient.get(robots_url, timeout=6.0)
                if r_resp.status_code == 200:
                    robots_txt = r_resp.text
            except Exception:
                pass
    except Exception as exc:
        ttfb_ms = int((time.monotonic() - t0) * 1000)
        fetch_error = scrub_text(f"{type(exc).__name__}: {exc}")[:200]

    findings: list[dict[str, Any]] = []
    if fetch_error:
        findings.append(
            _make_finding(
                fid="fetch_failed",
                category="availability",
                severity="critical",
                title_ar=f"تعذر جلب الصفحة عبر HTTP ({fetch_error})",
                title_en=f"HTTP fetch failed ({fetch_error})",
                page=clean_url,
                line=None,
                snippet=fetch_error,
                screenshot_url=screenshot_url,
                fix_ar="تأكد من صحة النطاق واتصال السيرفر أو شهادة SSL وأعد الفحص.",
                fix_en="Verify domain DNS, reachability, and SSL certificate then re-audit.",
            )
        )
        score = 10
        report_md = f"⛔ تعذر فحص `{clean_url}`: `{fetch_error}`"
        audit_row = SiteAudit(
            workspace_id=workspace_id,
            url=clean_url,
            target_backlink_url=target_backlink_url,
            profile_url=profile_url,
            network_path=network_path,
            screenshot_url=screenshot_url,
            score=score,
            summary_json={"status_code": 0, "ttfb_ms": ttfb_ms, "error": fetch_error},
            findings_json=findings,
            report_md=report_md,
        )
        db.add(audit_row)
        db.flush()
        return {
            "id": audit_row.id,
            "url": clean_url,
            "score": score,
            "network_path": network_path,
            "proxy_fallback_reason": proxy_fallback_reason,
            "screenshot_url": screenshot_url,
            "summary": audit_row.summary_json,
            "findings": findings,
            "report_md": report_md,
        }

    lines = html.splitlines()
    parser = _HtmlEvidenceParser()
    parser.feed(html)

    # 1. Title & Meta & Headers
    if not parser.title_text:
        findings.append(
            _make_finding(
                fid="missing_title",
                category="meta",
                severity="critical",
                title_ar="وسم <title> مفقود أو فارغ",
                title_en="Missing or empty <title> tag",
                page=clean_url,
                line=parser.title_line or 1,
                snippet=_line_snippet(lines, parser.title_line or 1),
                screenshot_url=screenshot_url,
                fix_ar="أضف وسم <title> واضح يحتوي على الكلمة المفتاحية بطول 30–60 حرفاً.",
                fix_en="Add a descriptive <title> containing the target keyword (30-60 chars).",
            )
        )
    elif len(parser.title_text) < 12 or len(parser.title_text) > 65:
        findings.append(
            _make_finding(
                fid="title_length",
                category="meta",
                severity="warning",
                title_ar=f"طول عنوان الصفحة ({len(parser.title_text)} حرف) خارج النطاق المثالي",
                title_en=f"Title length ({len(parser.title_text)} chars) outside ideal 12-65 range",
                page=clean_url,
                line=parser.title_line,
                snippet=_line_snippet(lines, parser.title_line),
                screenshot_url=screenshot_url,
                fix_ar="اضبط طول العنوان بين 30 و 60 حرفاً لتجنب الاقتطاع في نتائج البحث.",
                fix_en="Keep page title between 30 and 60 characters to avoid SERP truncation.",
            )
        )

    if not parser.meta_description:
        findings.append(
            _make_finding(
                fid="missing_meta_description",
                category="meta",
                severity="warning",
                title_ar="وصف الميتا <meta name='description'> مفقود",
                title_en="Missing meta description",
                page=clean_url,
                line=1,
                snippet=_line_snippet(lines, 1),
                screenshot_url=screenshot_url,
                fix_ar="أضف وسم meta description جذاب بطول 120–155 حرفاً لتحسين نسبة النقر CTR.",
                fix_en="Add a 120-155 char meta description inside <head> to improve CTR.",
            )
        )

    if not parser.h1_items:
        findings.append(
            _make_finding(
                fid="missing_h1",
                category="headers",
                severity="warning",
                title_ar="لا يوجد عنوان رئيسي <h1> في الصفحة",
                title_en="Missing <h1> heading",
                page=clean_url,
                line=1,
                snippet=_line_snippet(lines, 1),
                screenshot_url=screenshot_url,
                fix_ar="أضف وسم <h1> واحد في أعلى المحتوى الرئيسي يطابق نية البحث.",
                fix_en="Include one primary <h1> heading matching search intent.",
            )
        )
    elif len(parser.h1_items) > 1:
        _, h1_ln = parser.h1_items[1]
        findings.append(
            _make_finding(
                fid="multiple_h1",
                category="headers",
                severity="info",
                title_ar=f"تم رصد {len(parser.h1_items)} وسوم <h1> (يفضّل وسم واحد)",
                title_en=f"Multiple <h1> headings ({len(parser.h1_items)}) found",
                page=clean_url,
                line=h1_ln,
                snippet=_line_snippet(lines, h1_ln),
                screenshot_url=screenshot_url,
                fix_ar="استخدم <h1> واحداً وحوّل العناوين الفرعية إلى <h2>.",
                fix_en="Use a single <h1> and demote secondary headings to <h2>.",
            )
        )

    # 2. Indexability (noindex + X-Robots-Tag + robots.txt)
    x_robots = headers.get("x-robots-tag", "")
    meta_rob_low = (parser.meta_robots or "").lower()
    if "noindex" in meta_rob_low or "noindex" in x_robots.lower():
        ln = parser.meta_robots_line or 1
        findings.append(
            _make_finding(
                fid="noindex_detected",
                category="indexability",
                severity="critical",
                title_ar="الصفحة تمنع الأرشفة عبر noindex",
                title_en="Page blocks indexing via noindex directive",
                page=clean_url,
                line=ln,
                snippet=_line_snippet(lines, ln) or f"X-Robots-Tag: {x_robots}",
                screenshot_url=screenshot_url,
                fix_ar="أزل noindex من meta robots وترويسة X-Robots-Tag إذا كانت الصفحة مستهدفة للظهور.",
                fix_en="Remove noindex directive so search engines can index this page.",
            )
        )

    page_path = urlparse(clean_url).path or "/"
    for idx_r, rline in enumerate(robots_txt.splitlines(), start=1):
        low_rl = rline.strip().lower()
        if low_rl.startswith("disallow:"):
            dis = rline.split(":", 1)[1].strip()
            if dis == "/" or (dis and dis != "/" and page_path.startswith(dis)):
                findings.append(
                    _make_finding(
                        fid="robots_txt_disallow",
                        category="indexability",
                        severity="critical",
                        title_ar=f"ملف robots.txt يمنع الزحف للرابط ({dis})",
                        title_en=f"robots.txt Disallow matches this path ({dis})",
                        page=clean_url,
                        line=idx_r,
                        snippet=rline.strip(),
                        screenshot_url=screenshot_url,
                        fix_ar=f"عدّل السطر {idx_r} في robots.txt للسماح بالزحف لهذا المسار.",
                        fix_en="Update robots.txt Disallow rule to permit crawling.",
                    )
                )
                break

    # 3. Speed signals & accessibility
    html_bytes = len(html.encode("utf-8", errors="ignore"))
    if ttfb_ms > 1200:
        findings.append(
            _make_finding(
                fid="slow_ttfb",
                category="speed",
                severity="warning",
                title_ar=f"زمن استجابة الخادم مرتفع TTFB = {ttfb_ms}ms",
                title_en=f"Slow server TTFB ({ttfb_ms}ms)",
                page=clean_url,
                line=1,
                snippet=f"TTFB={ttfb_ms}ms, HTML={html_bytes} bytes",
                screenshot_url=screenshot_url,
                fix_ar="فعّل التخزين المؤقت (Page Cache / CDN) لتقليل TTFB تحت 500ms.",
                fix_en="Enable full-page caching or CDN to keep TTFB below 500ms.",
            )
        )
    if parser.blocking_scripts:
        src_s, sln = parser.blocking_scripts[0]
        findings.append(
            _make_finding(
                fid="render_blocking_scripts",
                category="speed",
                severity="warning",
                title_ar=f"{len(parser.blocking_scripts)} سكربت يحجب العرض بدون defer/async",
                title_en=f"{len(parser.blocking_scripts)} render-blocking script(s) without defer/async",
                page=clean_url,
                line=sln,
                snippet=_line_snippet(lines, sln) or src_s,
                screenshot_url=screenshot_url,
                fix_ar="أضف السمة defer أو async لوسوم <script src> لتسريع أول رسم للصفحة.",
                fix_en="Add `defer` or `async` to non-critical <script> tags.",
            )
        )

    # 4. Real Broken Links check on page
    base_url = clean_url
    candidate_links: list[tuple[str, int]] = []
    seen_hrefs: set[str] = set()
    for _, href, ln in parser.links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        if not abs_url.startswith(("http://", "https://")) or abs_url in seen_hrefs:
            continue
        seen_hrefs.add(abs_url)
        candidate_links.append((abs_url, ln))
        if len(candidate_links) >= max_links_check:
            break

    broken_links: list[dict[str, Any]] = []
    if candidate_links:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as lclient:

            async def _probe_link(u: str, ln: int) -> dict[str, Any] | None:
                try:
                    r = await lclient.head(u)
                    if r.status_code == 405:
                        r = await lclient.get(u)
                    if r.status_code >= 400:
                        return {"url": u, "status": r.status_code, "line": ln}
                    return None
                except Exception as exc:
                    return {"url": u, "status": f"error:{type(exc).__name__}", "line": ln}

            res_list = await asyncio.gather(*(_probe_link(u, ln) for u, ln in candidate_links))
            broken_links = [item for item in res_list if item is not None]

    for blk in broken_links:
        findings.append(
            _make_finding(
                fid=f"broken_link_{blk['line']}",
                category="broken_links",
                severity="critical",
                title_ar=f"رابط مكسور ({blk['status']}) -> {blk['url']}",
                title_en=f"Broken link ({blk['status']}) -> {blk['url']}",
                page=clean_url,
                line=blk["line"],
                snippet=_line_snippet(lines, blk["line"]) or blk["url"],
                screenshot_url=screenshot_url,
                fix_ar=f"استبدل أو أزل الرابط المعطّل في السطر {blk['line']} ({blk['url']}).",
                fix_en=f"Replace or remove broken anchor on line {blk['line']}.",
            )
        )

    # 5. Live Backlink check on public profile if supplied
    backlink_check: dict[str, Any] | None = None
    if profile_url and target_backlink_url:
        backlink_check = await verify_live_link_url(profile_url, target_backlink_url)
        if not backlink_check.get("found"):
            findings.append(
                _make_finding(
                    fid="backlink_missing_on_profile",
                    category="backlinks",
                    severity="critical",
                    title_ar=f"الباك لينك المستهدف غير موجود في البروفايل العام ({profile_url})",
                    title_en=f"Target backlink not found on public profile ({profile_url})",
                    page=profile_url,
                    line=None,
                    snippet=f"checked target={target_backlink_url} note={backlink_check.get('note')}",
                    screenshot_url=screenshot_url,
                    fix_ar="تأكد من حفظ الرابط في حقل Website/Bio في صفحة البروفايل العامة وأعد التحقق.",
                    fix_en="Ensure the backlink URL is saved in the public profile and re-verify.",
                )
            )
        else:
            findings.append(
                _make_finding(
                    fid="backlink_live_verified",
                    category="backlinks",
                    severity="ok",
                    title_ar=f"الباك لينك حي ومفحوص ({backlink_check.get('link_type')}) على {profile_url}",
                    title_en=f"Live backlink verified ({backlink_check.get('link_type')}) on {profile_url}",
                    page=profile_url,
                    line=None,
                    snippet=f"target={target_backlink_url} type={backlink_check.get('link_type')}",
                    screenshot_url=screenshot_url,
                    fix_ar="لا يلزم إجراء — الرابط ظاهر وقابل للزحف.",
                    fix_en="No fix required — link is live.",
                )
            )

    if not findings:
        findings.append(
            _make_finding(
                fid="all_clean",
                category="meta",
                severity="ok",
                title_ar="لا توجد أخطاء فنية حرجة في الصفحة المفحوصة",
                title_en="No critical technical SEO issues detected",
                page=clean_url,
                line=1,
                snippet=_line_snippet(lines, 1),
                screenshot_url=screenshot_url,
                fix_ar="استمر في مراقبة الأداء والروابط دورياً.",
                fix_en="Continue periodic monitoring.",
            )
        )

    score = 100
    for f in findings:
        if f["severity"] == "critical":
            score -= 20
        elif f["severity"] == "warning":
            score -= 8
        elif f["severity"] == "info":
            score -= 2
    score = max(0, min(100, score))

    summary = {
        "status_code": status_code,
        "ttfb_ms": ttfb_ms,
        "html_bytes": html_bytes,
        "title": parser.title_text,
        "meta_description_len": len(parser.meta_description or ""),
        "h1_count": len(parser.h1_items),
        "canonical": parser.canonical,
        "links_checked": len(candidate_links),
        "broken_links_count": len(broken_links),
        "backlink_check": backlink_check,
        "network_path": network_path,
    }

    md_lines = [
        f"🔍 **تقرير فحص الموقع (Site Auditor) — `{clean_url}`**",
        f"- الدرجة: **{score}/100** · HTTP: **{status_code}** · TTFB: **{ttfb_ms}ms** · المسار: `{network_path}`",
        f"- روابط مفحوصة: **{len(candidate_links)}** · مكسورة: **{len(broken_links)}**",
    ]
    for f in findings:
        ev = f["evidence"]
        loc = f"سطر {ev['line']}" if ev.get("line") else ev["page"]
        md_lines.append(f"- [{f['severity'].upper()}] {f['title_ar']} (`{loc}`) → الحل: {f['fix_ar']}")
    report_md = "\n".join(md_lines)

    audit_row = SiteAudit(
        workspace_id=workspace_id,
        url=clean_url,
        target_backlink_url=target_backlink_url,
        profile_url=profile_url,
        network_path=network_path,
        screenshot_url=screenshot_url,
        score=score,
        summary_json=summary,
        findings_json=findings,
        report_md=report_md,
    )
    db.add(audit_row)
    db.add(
        Report(
            workspace_id=workspace_id,
            period="site_audit",
            title=f"فحص موقع: {clean_url} ({score}/100)",
            body_md=report_md,
        )
    )
    db.flush()

    return {
        "id": audit_row.id,
        "url": clean_url,
        "score": score,
        "network_path": network_path,
        "proxy_fallback_reason": proxy_fallback_reason,
        "screenshot_url": screenshot_url,
        "summary": summary,
        "findings": findings,
        "report_md": report_md,
    }
