"use client";

import React, { useEffect, useState } from "react";
import { RefreshCw, Download } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface LinkRow {
  id: string;
  profile_url: string;
  target_kind: string;
  target_url: string;
  anchor_text: string;
  link_type: string;
  live: boolean;
  last_checked_at: string | null;
  check_note: string | null;
}

export default function BacklinkLinksPage() {
  const { t, locale, formatDate } = useApp();
  const [links, setLinks] = useState<LinkRow[]>([]);
  const [checkingId, setCheckingId] = useState<string | null>(null);

  const load = () => {
    apiFetch<{ links: LinkRow[] }>("/api/backlinks/links").then((res) => setLinks(res.links)).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const checkNow = async (id: string) => {
    setCheckingId(id);
    try {
      await apiFetch(`/api/backlinks/links/${id}/check`, { method: "POST" });
      load();
    } finally {
      setCheckingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">{t("nav.backlink_links")}</h1>
        <a href="/api/backlinks/export?table=links" className="inline-flex min-h-[40px] items-center gap-2 rounded-lg border border-borderline bg-surface px-3 text-xs font-semibold">
          <Download className="h-4 w-4" />
          <span>{locale === "ar" ? "تصدير CSV" : "Export CSV"}</span>
        </a>
      </div>

      <div className="overflow-x-auto rounded-xl border border-borderline bg-surface">
        <table className="w-full text-start text-xs">
          <thead className="border-b border-borderline bg-canvas text-muted">
            <tr>
              <th className="p-3 text-start">{locale === "ar" ? "البروفايل" : "Profile"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "الهدف" : "Target"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "الأنكر" : "Anchor"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "النوع" : "Rel"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "حي؟" : "Live"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "آخر فحص" : "Checked"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "فحص" : "Check"}</th>
            </tr>
          </thead>
          <tbody>
            {links.map((l) => (
              <tr key={l.id} className="border-b border-borderline last:border-0">
                <td className="p-3 font-mono">
                  <bdi dir="ltr">{l.profile_url}</bdi>
                </td>
                <td className="p-3">{l.target_kind}</td>
                <td className="p-3 font-semibold">{l.anchor_text || "—"}</td>
                <td className="p-3">
                  <span className={`rounded px-2 py-0.5 font-mono text-[11px] ${l.link_type === "dofollow" ? "bg-success/15 text-success" : "bg-canvas text-muted"}`}>
                    {l.link_type}
                  </span>
                </td>
                <td className="p-3">
                  <StatusBadge status={l.live ? "live" : "failed"} />
                </td>
                <td className="p-3 text-muted">{formatDate(l.last_checked_at)}</td>
                <td className="p-3">
                  <button
                    onClick={() => checkNow(l.id)}
                    disabled={checkingId === l.id}
                    className="inline-flex items-center gap-1 rounded border border-borderline px-2.5 py-1 hover:border-primary"
                  >
                    <RefreshCw className={`h-3 w-3 ${checkingId === l.id ? "animate-spin" : ""}`} />
                    <span>{locale === "ar" ? "افحص دلوقتي" : "Check Now"}</span>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
