"use client";

import React, { useEffect, useState } from "react";
import { FileBarChart, Printer, ShieldCheck } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface ReportItem {
  id: string;
  period: string;
  title: string;
  body_md: string;
  created_at: string;
}

interface CaptchaStats {
  solver_mode: string;
  max_spend_usd: number;
  total: number;
  passed: number;
  skipped: number;
  timeout: number;
  pass_rate_pct: number;
  by_type: Record<string, { total: number; passed: number; skipped: number; timeout: number }>;
  by_site: Record<string, { total: number; passed: number; skipped: number; timeout: number }>;
}

interface GscReportData {
  connected: boolean;
  site_url: string | null;
  totals: { clicks: number; impressions: number; ctr: number; position: number; start_date: string; end_date: string } | null;
  top_queries: Array<{ query: string; clicks: number; impressions: number; ctr: number; position: number }>;
  report_ar: string;
}

interface UsageStatsData {
  days: number;
  total_calls: number;
  cache_hits: number;
  cache_hit_rate_pct: number;
  total_context_tokens: number;
  avg_latency_ms: number;
  total_cost_usd: number;
}

export default function ReportsPage() {
  const { t, locale, formatNum, formatDate } = useApp();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [captchaStats, setCaptchaStats] = useState<CaptchaStats | null>(null);
  const [gscReport, setGscReport] = useState<GscReportData | null>(null);
  const [usageStats, setUsageStats] = useState<UsageStatsData | null>(null);

  const load = () => {
    apiFetch<{ reports: ReportItem[] }>("/api/reports").then((r) => setReports(r.reports)).catch(() => null);
    apiFetch<CaptchaStats>("/api/captcha/stats").then(setCaptchaStats).catch(() => null);
    apiFetch<GscReportData>("/api/gsc/report").then(setGscReport).catch(() => null);
    apiFetch<UsageStatsData>("/api/stats/usage").then(setUsageStats).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const generate = async (period: "daily" | "weekly") => {
    await apiFetch("/api/reports/generate", {
      method: "POST",
      body: JSON.stringify({ period }),
    });
    load();
  };

  const runDaily0800 = async () => {
    await apiFetch("/api/reports/daily-0800/run", {
      method: "POST",
      body: JSON.stringify({ enforce_hour: false, post_to_chat: true }),
    });
    load();
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{t("nav.reports")}</h1>
          <p className="text-xs text-muted">{t("common.honesty_badge")}</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => generate("daily")} className="min-h-[40px] rounded-lg bg-primary px-4 text-xs font-semibold text-white">
            {locale === "ar" ? "توليد تقرير يومي الآن" : "Generate Daily Report"}
          </button>
          <button onClick={() => generate("weekly")} className="min-h-[40px] rounded-lg border border-borderline bg-surface px-4 text-xs font-semibold">
            {locale === "ar" ? "توليد تقرير أسبوعي" : "Generate Weekly"}
          </button>
          <button onClick={runDaily0800} className="min-h-[40px] rounded-lg border border-primary/50 bg-primary/10 px-4 text-xs font-semibold text-primary">
            {locale === "ar" ? "تقرير 08:00 الصباحي" : "Daily 08:00 Digest"}
          </button>
        </div>
      </div>

      {/* §7E.1 Usage, Context & Latency Stats + §7A.2 Live GSC Report */}
      <div className="grid gap-4 lg:grid-cols-2">
        {usageStats && (
          <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
            <h2 className="text-sm font-bold">{locale === "ar" ? "إحصائيات الاستهلاك والكاش والسرعة" : "Context, Cache & Latency Stats"}</h2>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded-lg bg-canvas p-2.5">
                <div className="text-muted">{locale === "ar" ? "النداءات" : "Calls"}</div>
                <div className="text-base font-bold">{formatNum(usageStats.total_calls)}</div>
              </div>
              <div className="rounded-lg bg-canvas p-2.5">
                <div className="text-muted">{locale === "ar" ? "نسبة الكاش" : "Cache Hit"}</div>
                <div className="text-base font-bold text-success">{usageStats.cache_hit_rate_pct}%</div>
              </div>
              <div className="rounded-lg bg-canvas p-2.5">
                <div className="text-muted">{locale === "ar" ? "متوسط الزمن" : "Avg Latency"}</div>
                <div className="text-base font-bold">{usageStats.avg_latency_ms}ms</div>
              </div>
              <div className="rounded-lg bg-canvas p-2.5">
                <div className="text-muted">{locale === "ar" ? "رموز السياق" : "Context Tokens"}</div>
                <div className="text-base font-bold">{formatNum(usageStats.total_context_tokens)}</div>
              </div>
            </div>
          </div>
        )}

        {gscReport && (
          <div className="rounded-xl border border-borderline bg-surface p-4 space-y-2 text-xs">
            <h2 className="text-sm font-bold">Google Search Console (Read-Only)</h2>
            {!gscReport.connected || !gscReport.totals ? (
              <div className="rounded-lg border border-dashed border-borderline bg-canvas p-3 space-y-1">
                <div className="whitespace-pre-wrap text-muted">{gscReport.report_ar}</div>
                <a href="/settings#connectors" className="inline-block font-semibold text-primary underline">
                  {locale === "ar" ? "الانتقال إلى الإعدادات ← الموصلات للربط" : "Open Settings -> Connectors"}
                </a>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex flex-wrap gap-3 font-mono text-[11px]">
                  <span>clicks={gscReport.totals.clicks}</span>
                  <span>impressions={gscReport.totals.impressions}</span>
                  <span>pos={gscReport.totals.position}</span>
                </div>
                {gscReport.top_queries.slice(0, 5).map((q, idx) => (
                  <div key={idx} className="flex items-center justify-between rounded bg-canvas px-2.5 py-1">
                    <span>{q.query}</span>
                    <span className="font-mono text-muted">c:{q.clicks} / i:{q.impressions} / p:{q.position}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Measured CAPTCHA Stats Panel */}
      {captchaStats && (
        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-sm font-bold">
              <ShieldCheck className="h-4 w-4 text-primary" />
              <span>{t("captcha.stats_title")}</span>
            </h2>
            <span className="rounded bg-canvas px-2 py-0.5 font-mono text-xs">
              solver={captchaStats.solver_mode} · max_spend=${captchaStats.max_spend_usd}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5 text-xs">
            <div className="rounded-lg bg-canvas p-3">
              <div className="text-muted">{locale === "ar" ? "الإجمالي" : "Total"}</div>
              <div className="text-lg font-bold">{formatNum(captchaStats.total)}</div>
            </div>
            <div className="rounded-lg bg-canvas p-3">
              <div className="text-muted">{t("captcha.passed")}</div>
              <div className="text-lg font-bold text-success">{formatNum(captchaStats.passed)}</div>
            </div>
            <div className="rounded-lg bg-canvas p-3">
              <div className="text-muted">{t("captcha.skipped")}</div>
              <div className="text-lg font-bold text-warning">{formatNum(captchaStats.skipped)}</div>
            </div>
            <div className="rounded-lg bg-canvas p-3">
              <div className="text-muted">{t("captcha.timeout")}</div>
              <div className="text-lg font-bold text-danger">{formatNum(captchaStats.timeout)}</div>
            </div>
            <div className="rounded-lg bg-canvas p-3">
              <div className="text-muted">{locale === "ar" ? "نسبة النجاح الفعلية" : "Measured Rate"}</div>
              <div className="text-lg font-bold text-primary">{captchaStats.pass_rate_pct}%</div>
            </div>
          </div>
        </div>
      )}

      {/* Saved Reports */}
      <div className="space-y-4">
        {reports.length === 0 ? (
          <div className="rounded-xl border border-borderline bg-surface p-8 text-center text-sm text-muted">{t("common.empty")}</div>
        ) : (
          reports.map((rep) => (
            <div key={rep.id} className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2">
                <div className="flex items-center gap-2 font-bold">
                  <FileBarChart className="h-4 w-4 text-primary" />
                  <span>{rep.title}</span>
                  <span className="text-xs font-normal text-muted">{formatDate(rep.created_at)}</span>
                </div>
                <a
                  href={`/api/reports/${rep.id}/pdf`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-lg border border-borderline px-3 py-1.5 text-xs font-semibold hover:border-primary"
                >
                  <Printer className="h-3.5 w-3.5" />
                  <span>{locale === "ar" ? "تصدير / طباعة PDF عربي" : "Printable Arabic PDF"}</span>
                </a>
              </div>
              <pre className="whitespace-pre-wrap font-sans text-xs text-ink">{rep.body_md}</pre>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
