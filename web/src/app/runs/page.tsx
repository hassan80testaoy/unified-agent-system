"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Pause, Play, RotateCcw, X } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface RunRow {
  id: string;
  agent_key: string;
  agent_name_ar: string;
  status: string;
  progress_done: number;
  progress_total: number;
  cost_usd: number;
  created_at: string;
}

export default function RunsListPage() {
  const { t, locale, formatNum, formatDate } = useApp();
  const [runs, setRuns] = useState<RunRow[]>([]);

  const load = () => {
    apiFetch<{ runs: RunRow[] }>("/api/runs").then((res) => setRuns(res.runs)).catch(() => null);
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const act = async (id: string, action: string) => {
    await apiFetch(`/api/runs/${id}/${action}`, { method: "POST" });
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">{t("nav.runs")}</h1>
        <Link href="/tasks/new" className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white">
          {t("nav.new_task")}
        </Link>
      </div>

      <div className="overflow-x-auto rounded-xl border border-borderline bg-surface">
        <table className="w-full text-start text-sm">
          <thead className="border-b border-borderline bg-canvas text-xs text-muted">
            <tr>
              <th className="p-3 text-start">{locale === "ar" ? "الوكيل" : "Agent"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "الحالة" : "Status"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "التقدم" : "Progress"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "التكلفة" : "Cost"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "الوقت" : "Created"}</th>
              <th className="p-3 text-start">{locale === "ar" ? "إجراءات" : "Actions"}</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-6 text-center text-xs text-muted">
                  {t("common.empty")}
                </td>
              </tr>
            ) : (
              runs.map((r) => (
                <tr key={r.id} className="border-b border-borderline last:border-0 hover:bg-canvas/50">
                  <td className="p-3">
                    <Link href={`/runs/${r.id}`} className="font-semibold text-primary hover:underline">
                      {locale === "ar" ? r.agent_name_ar || r.agent_key : r.agent_key}
                    </Link>
                    <div className="font-mono text-[11px] text-muted">
                      <bdi dir="ltr">{r.id.slice(0, 8)}</bdi>
                    </div>
                  </td>
                  <td className="p-3">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="p-3">
                    {formatNum(r.progress_done)} / {formatNum(r.progress_total)}
                  </td>
                  <td className="p-3">
                    <bdi dir="ltr">${r.cost_usd.toFixed(4)}</bdi>
                  </td>
                  <td className="p-3 text-xs text-muted">{formatDate(r.created_at)}</td>
                  <td className="p-3">
                    <div className="flex items-center gap-1.5">
                      {r.status === "running" && (
                        <button onClick={() => act(r.id, "pause")} className="rounded border border-borderline p-1.5" title="Pause">
                          <Pause className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {r.status === "paused" && (
                        <button onClick={() => act(r.id, "resume")} className="rounded border border-borderline p-1.5" title="Resume">
                          <Play className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {(r.status === "failed" || r.status === "canceled") && (
                        <button onClick={() => act(r.id, "retry")} className="rounded border border-borderline p-1.5" title="Retry">
                          <RotateCcw className="h-3.5 w-3.5" />
                        </button>
                      )}
                      {r.status !== "done" && r.status !== "canceled" && (
                        <button onClick={() => act(r.id, "cancel")} className="rounded border border-borderline p-1.5 text-danger" title="Cancel">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
