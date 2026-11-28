"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { PlusCircle, ShieldAlert, TrendingUp } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface SummaryData {
  kpis: { registered: number; live: number; pending: number; needs_you: number; failed: number };
  spend_today_usd: number;
  active_runs: { id: string; status: string; progress_done: number; progress_total: number; cost_usd: number }[];
  pending_approvals: { id: string; run_id: string; kind: string; payload: Record<string, unknown>; created_at: string }[];
  recent_events: { id: string; run_id: string; ts: string; level: string; kind: string; message: string }[];
  chart_14d: { date: string; registrations: number; live_links: number }[];
}

export default function DashboardPage() {
  const { t, formatNum, formatDate, locale } = useApp();
  const [data, setData] = useState<SummaryData | null>(null);

  useEffect(() => {
    apiFetch<SummaryData>("/api/dashboard/summary").then(setData).catch(() => null);
  }, []);

  if (!data) {
    return <div className="p-6 text-sm text-muted">{t("common.loading")}</div>;
  }

  const kpis = [
    { label: t("dashboard.kpi_registered"), value: data.kpis.registered, href: "/backlinks/accounts", tone: "text-primary" },
    { label: t("dashboard.kpi_live"), value: data.kpis.live, href: "/backlinks/links", tone: "text-success" },
    { label: t("dashboard.kpi_pending"), value: data.kpis.pending, href: "/backlinks/sites", tone: "text-info" },
    { label: t("dashboard.kpi_needs_you"), value: data.kpis.needs_you, href: "/approvals", tone: "text-warning" },
    { label: t("dashboard.kpi_failed"), value: data.kpis.failed, href: "/backlinks/sites", tone: "text-danger" },
  ];

  const maxBar = Math.max(1, ...data.chart_14d.map((d) => Math.max(d.registrations, d.live_links)));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{t("dashboard.title")}</h1>
          <p className="text-xs text-muted">{t("common.honesty_badge")}</p>
        </div>
        <Link
          href="/tasks/new"
          className="inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-white"
        >
          <PlusCircle className="h-4 w-4" />
          <span>{t("dashboard.quick_new_task")}</span>
        </Link>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {kpis.map((k) => (
          <Link
            key={k.label}
            href={k.href}
            className="rounded-xl border border-borderline bg-surface p-4 transition hover:border-primary"
          >
            <div className="text-xs text-muted">{k.label}</div>
            <div className={`mt-1 text-2xl font-bold ${k.tone}`}>{formatNum(k.value)}</div>
          </Link>
        ))}
      </div>

      {/* Active Runs & Needs your attention */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-borderline bg-surface p-4">
          <h2 className="mb-3 text-sm font-bold">{t("dashboard.active_runs")}</h2>
          {data.active_runs.length === 0 ? (
            <div className="rounded-lg border border-dashed border-borderline p-4 text-center text-xs text-muted">
              {locale === "ar" ? "لسه مفيش وكلاء شغالة — ابدأ مهمة باك لينك الآن." : "No active runs right now."}
            </div>
          ) : (
            <div className="space-y-3">
              {data.active_runs.map((r) => (
                <Link
                  key={r.id}
                  href={`/runs/${r.id}`}
                  className="block rounded-lg border border-borderline bg-canvas p-3 hover:border-primary"
                >
                  <div className="flex items-center justify-between">
                    <bdi dir="ltr" className="font-mono text-xs">
                      {r.id.slice(0, 8)}
                    </bdi>
                    <StatusBadge status={r.status} />
                  </div>
                  <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-borderline">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${r.progress_total ? Math.min(100, (r.progress_done / r.progress_total) * 100) : 0}%` }}
                    />
                  </div>
                  <div className="mt-1 flex justify-between text-[11px] text-muted">
                    <span>
                      {formatNum(r.progress_done)} / {formatNum(r.progress_total)}
                    </span>
                    <bdi dir="ltr">${r.cost_usd.toFixed(4)}</bdi>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-borderline bg-surface p-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-bold">
            <ShieldAlert className="h-4 w-4 text-warning" />
            <span>{t("dashboard.needs_attention")}</span>
          </h2>
          {data.pending_approvals.length === 0 ? (
            <div className="rounded-lg border border-dashed border-borderline p-4 text-center text-xs text-muted">
              {t("common.empty")}
            </div>
          ) : (
            <div className="space-y-2">
              {data.pending_approvals.map((a) => (
                <Link
                  key={a.id}
                  href={`/runs/${a.run_id}`}
                  className="flex items-center justify-between rounded-lg border border-warning/30 bg-amber-500/5 p-3 text-xs hover:border-warning"
                >
                  <div>
                    <div className="font-semibold">{a.kind}</div>
                    <div className="text-muted">{String(a.payload?.site || a.run_id.slice(0, 8))}</div>
                  </div>
                  <span className="font-semibold text-warning">{locale === "ar" ? "اتخذ قراراً" : "Review"}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 14-day Chart & Spend Today */}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-borderline bg-surface p-4 lg:col-span-2">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-bold">
            <TrendingUp className="h-4 w-4 text-primary" />
            <span>{t("dashboard.chart_14d")}</span>
          </h2>
          <div className="flex h-36 items-end gap-1.5">
            {data.chart_14d.map((d) => (
              <div key={d.date} className="flex flex-1 flex-col items-center gap-1" title={`${d.date}: ${d.registrations} / ${d.live_links}`}>
                <div className="flex h-28 w-full items-end justify-center gap-0.5">
                  <div
                    className="w-1/2 rounded-t bg-primary/70"
                    style={{ height: `${Math.max(4, (d.registrations / maxBar) * 100)}%` }}
                  />
                  <div
                    className="w-1/2 rounded-t bg-success"
                    style={{ height: `${Math.max(4, (d.live_links / maxBar) * 100)}%` }}
                  />
                </div>
                <span className="text-[9px] text-muted">{d.date.slice(5)}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-xl border border-borderline bg-surface p-4">
          <h2 className="mb-2 text-sm font-bold">{t("dashboard.spend_today")}</h2>
          <div className="text-3xl font-bold text-primary">
            <bdi dir="ltr">${data.spend_today_usd.toFixed(4)}</bdi>
          </div>
          <p className="mt-2 text-xs text-muted">
            {locale === "ar"
              ? "يتم إيقاف أي مزود تلقائياً عند بلوغ سقف الميزانية اليومي."
              : "Every provider stops automatically at its configured daily budget cap."}
          </p>
        </div>
      </div>

      {/* Recent Events */}
      <div className="rounded-xl border border-borderline bg-surface p-4">
        <h2 className="mb-3 text-sm font-bold">{t("dashboard.recent_events")}</h2>
        {data.recent_events.length === 0 ? (
          <div className="text-xs text-muted">{t("common.empty")}</div>
        ) : (
          <div className="space-y-2">
            {data.recent_events.map((ev) => (
              <div key={ev.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2 text-xs last:border-0">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[11px]">{ev.kind}</span>
                  <span>{ev.message}</span>
                </div>
                <span className="text-[11px] text-muted">{formatDate(ev.ts)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
