"use client";

import React, { useEffect, useState } from "react";
import { Download, RotateCcw, Ban } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface SiteRow {
  id: string;
  source_row: number | null;
  domain: string;
  signup_url: string | null;
  category: string | null;
  is_noindex: boolean;
  status: string;
  blocked_reason: string | null;
}

export default function BacklinkSitesPage() {
  const { t, locale, formatNum } = useApp();
  const [sites, setSites] = useState<SiteRow[]>([]);
  const [filterStatus, setFilterStatus] = useState("");
  const [search, setSearch] = useState("");

  const load = () => {
    const qs = filterStatus ? `?status_filter=${encodeURIComponent(filterStatus)}` : "";
    apiFetch<{ sites: SiteRow[] }>(`/api/backlinks/sites${qs}`).then((res) => setSites(res.sites)).catch(() => null);
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
