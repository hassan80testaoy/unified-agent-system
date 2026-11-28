"use client";

import React, { useEffect, useState } from "react";
import { Download, RotateCcw, Ban, Globe, Plus, Trash2, BookOpen } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface TargetProjectItem {
  id: string;
  name: string;
  domain: string;
  url: string;
  maps_url: string | null;
  status: string;
}

interface LibrarySiteItem {
  id: string;
  domain: string;
  signup_url: string;
  category: string;
  signup_flow: string;
  discovered_fields: string[];
  playbook_md: string;
  success_count: number;
  status: string;
}

interface PlacementItem {
  id: string;
  target_project_id: string;
  library_site_id: string;
  email_used: string;
  profile_url: string | null;
  live: boolean;
  status: string;
}

interface SiteRow {
  id: string;
  source_row: number | null;
  domain: string;
  signup_url: string | null;
  category: string | null;
  is_noindex: boolean;
  status: string;
  blocked_reason: string | null;
  replay_stats?: {
    latest_version: number;
    replay_count: number;
    saved_steps_total: number;
    saved_llm_calls_total: number;
  } | null;
}

export default function BacklinkSitesPage() {
  const { t, locale, formatNum } = useApp();
  const [sites, setSites] = useState<SiteRow[]>([]);
  const [filterStatus, setFilterStatus] = useState("");
  const [search, setSearch] = useState("");

  // §8B Multi-site Target Projects + Library + Placements
  const [projects, setProjects] = useState<TargetProjectItem[]>([]);
  const [library, setLibrary] = useState<LibrarySiteItem[]>([]);
  const [placements, setPlacements] = useState<PlacementItem[]>([]);
  const [projName, setProjName] = useState("");
  const [projUrl, setProjUrl] = useState("");
  const [projMaps, setProjMaps] = useState("");
  const [discoverUrl, setDiscoverUrl] = useState("");
  const [openPlaybookId, setOpenPlaybookId] = useState<string | null>(null);

  const load = () => {
    const qs = filterStatus ? `?status_filter=${encodeURIComponent(filterStatus)}` : "";
    apiFetch<{ sites: SiteRow[] }>(`/api/backlinks/sites${qs}`).then((res) => setSites(res.sites)).catch(() => null);
    apiFetch<{ projects: TargetProjectItem[] }>("/api/backlinks/targets/projects").then((res) => setProjects(res.projects || [])).catch(() => null);
    apiFetch<{ sites: LibrarySiteItem[] }>("/api/backlinks/library").then((res) => setLibrary(res.sites || [])).catch(() => null);
    apiFetch<{ placements: PlacementItem[] }>("/api/backlinks/placements").then((res) => setPlacements(res.placements || [])).catch(() => null);
  };

  const handleAddProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!projName.trim() || !projUrl.trim()) return;
    await apiFetch("/api/backlinks/targets/projects", {
      method: "POST",
      body: JSON.stringify({ name: projName.trim(), url: projUrl.trim(), maps_url: projMaps.trim() || null }),
    });
    setProjName("");
    setProjUrl("");
    setProjMaps("");
    load();
  };

  const handleDeleteProject = async (id: string) => {
    await apiFetch(`/api/backlinks/targets/projects/${id}`, { method: "DELETE" });
    load();
  };

  const handleDiscoverSite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!discoverUrl.trim()) return;
    await apiFetch("/api/backlinks/library/discover", {
      method: "POST",
      body: JSON.stringify({ domain_or_url: discoverUrl.trim() }),
    });
    setDiscoverUrl("");
    load();
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterStatus]);

  const updateStatus = async (id: string, status: string) => {
    await apiFetch(`/api/backlinks/sites/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    load();
  };

  const filtered = sites.filter((s) => !search.trim() || s.domain.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">{t("nav.backlink_sites")}</h1>
        <a
          href="/api/backlinks/export?table=sites"
          className="inline-flex min-h-[40px] items-center gap-2 rounded-lg border border-borderline bg-surface px-3 text-xs font-semibold hover:border-primary"
        >
          <Download className="h-4 w-4" />
          <span>{locale === "ar" ? "تصدير CSV" : "Export CSV"}</span>
        </a>
      </div>

      {/* §8B Target Site Projects + Multi-Site Library & Playbooks */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bento-card space-y-3 p-4 text-xs">
          <div className="flex items-center gap-2 text-sm font-bold">
            <Globe className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "مواقعي المستهدفة (Target Projects)" : "My Target Site Projects"}</span>
          </div>
          <form onSubmit={handleAddProject} className="grid gap-2 sm:grid-cols-3">
            <input value={projName} onChange={(e) => setProjName(e.target.value)} placeholder={locale === "ar" ? "اسم المشروع" : "Project name"} className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5" />
            <input dir="ltr" value={projUrl} onChange={(e) => setProjUrl(e.target.value)} placeholder="https://example.com" className="rounded-lg border border-borderline bg-canvas px-2.5 py-1.5 font-mono" />
            <div className="flex gap-1.5">
              <input dir="ltr" value={projMaps} onChange={(e) => setProjMaps(e.target.value)} placeholder="Maps URL (opt)" className="flex-1 rounded-lg border border-borderline bg-canvas px-2 py-1.5 font-mono" />
              <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
          </form>
          <div className="space-y-1.5">
            {projects.map((p) => (
              <div key={p.id} className="flex items-center justify-between rounded-lg border border-borderline bg-canvas px-3 py-2">
                <div>
                  <span className="font-bold">{p.name}</span>
                  <bdi dir="ltr" className="ms-2 font-mono text-[11px] text-primary">{p.url}</bdi>
                </div>
                <button type="button" onClick={() => handleDeleteProject(p.id)} className="text-danger hover:opacity-80">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="bento-card space-y-3 p-4 text-xs">
          <div className="flex items-center gap-2 text-sm font-bold">
            <BookOpen className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "مكتبة مواقع الباك لينك وملفات SITE.md" : "Backlink Site Library & Playbooks"}</span>
          </div>
          <form onSubmit={handleDiscoverSite} className="flex gap-2">
            <input dir="ltr" value={discoverUrl} onChange={(e) => setDiscoverUrl(e.target.value)} placeholder="https://new-directory.org" className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-1.5 font-mono" />
            <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">
              {locale === "ar" ? "اكتشف وأضف للمكتبة" : "Discover & Add"}
            </button>
          </form>
          <div className="max-h-52 space-y-1.5 overflow-y-auto">
            {library.map((lib) => (
              <div key={lib.id} className="rounded-lg border border-borderline bg-canvas p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <bdi dir="ltr" className="font-mono font-bold text-primary">{lib.domain}</bdi>
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-surface px-1.5 py-0.5 text-[10px]">{lib.signup_flow}</span>
                    <button type="button" onClick={() => setOpenPlaybookId(openPlaybookId === lib.id ? null : lib.id)} className="text-[11px] text-primary underline">
                      SITE.md
                    </button>
                  </div>
                </div>
                {openPlaybookId === lib.id && (
                  <pre dir="ltr" className="mt-2 whitespace-pre-wrap rounded border border-borderline bg-surface p-2 font-mono text-[10px]">
                    {lib.playbook_md}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {placements.length > 0 && (
        <div className="bento-card space-y-2 p-4 text-xs">
          <div className="text-sm font-bold">{locale === "ar" ? `توزيع الباك لينك (Target × Site × Email) — ${placements.length}` : `Placements Matrix (${placements.length})`}</div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {placements.slice(0, 12).map((pl) => (
              <div key={pl.id} className="rounded-lg border border-borderline bg-canvas p-2.5">
                <div className="font-mono text-[11px] text-primary">{pl.email_used}</div>
                <div className="text-[10px] text-muted">status: {pl.status} · live: {pl.live ? "yes" : "no"}</div>
                {pl.profile_url && <div className="truncate font-mono text-[10px]">{pl.profile_url}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={locale === "ar" ? "بحث بالدومين..." : "Filter domain..."}
          className="rounded-lg border border-borderline bg-surface px-3 py-2 text-xs outline-none focus:border-primary"
        />
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded-lg border border-borderline bg-surface px-3 py-2 text-xs"
        >
          <option value="">{locale === "ar" ? "كل الحالات" : "All statuses"}</option>
          <option value="queued">queued</option>
          <option value="live">live</option>
          <option value="excluded_noindex">excluded_noindex</option>
          <option value="needs_captcha_manual">needs_captcha_manual</option>
          <option value="needs_manual">needs_manual</option>
          <option value="blocked">blocked</option>
          <option value="failed">failed</option>
        </select>
      </div>

      <div className="overflow-x-auto rounded-xl border border-borderline bg-surface">
        <table className="w-full text-start text-xs">
          <thead className="border-b border-borderline bg-canvas text-muted">
            <tr>
              <th className="p-3 text-start">#</th>
              <th className="p-3 text-start">{locale === "ar" ? "الدومين" : "Domain"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "الفئة" : "Category"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "الحالة" : "Status"}</th>
              <th className="p-3 text-start">{t("replay.title")}</th>
              <th className="p-3 text-start">{locale === "ar" ? "سبب الحظر" : "Reason"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "إجراءات" : "Actions"}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <tr key={s.id} className="border-b border-borderline last:border-0">
                <td className="p-3">{s.source_row ? formatNum(s.source_row) : "—"}</td>
                <td className="p-3 font-mono font-semibold">
                  <bdi dir="ltr">{s.domain}</bdi>
                </td>
                <td className="p-3 text-muted">{s.category || "—"}</td>
                <td className="p-3">
                  <StatusBadge status={s.status} />
                </td>
                <td className="p-3 text-muted">
                  {s.replay_stats ? (
                    <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-[11px] text-primary">
                      v{s.replay_stats.latest_version} · {s.replay_stats.replay_count}x · -{s.replay_stats.saved_steps_total} steps / -{s.replay_stats.saved_llm_calls_total} LLM
                    </span>
                  ) : (
                    t("replay.no_session")
                  )}
                </td>
                <td className="p-3 text-muted">{s.blocked_reason || (s.is_noindex ? "noindex" : "—")}</td>
                <td className="p-3">
                  <div className="flex gap-1.5">
                    <button
                      onClick={() => updateStatus(s.id, "queued")}
                      className="rounded border border-borderline p-1.5 hover:border-primary"
                      title={locale === "ar" ? "إعادة للطابور" : "Requeue"}
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => updateStatus(s.id, "dropped_by_user")}
                      className="rounded border border-borderline p-1.5 text-danger"
                      title={locale === "ar" ? "استبعاد" : "Drop"}
                    >
                      <Ban className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
