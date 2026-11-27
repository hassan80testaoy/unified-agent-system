"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { Check, X } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface ApprovalRow {
  id: string;
  run_id: string;
  kind: string;
  payload: Record<string, unknown>;
  status: string;
  created_at: string;
}

export default function ApprovalsPage() {
  const { t, locale, formatDate } = useApp();
  const [approvals, setApprovals] = useState<ApprovalRow[]>([]);

  const load = () => {
    apiFetch<{ approvals: ApprovalRow[] }>("/api/approvals").then((res) => setApprovals(res.approvals)).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const decide = async (id: string, decision: "approve" | "reject") => {
    await apiFetch(`/api/approvals/${id}/${decision}`, { method: "POST" });
    load();
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">{t("nav.approvals")}</h1>

      {approvals.length === 0 ? (
        <div className="rounded-xl border border-borderline bg-surface p-8 text-center text-sm text-muted">{t("common.empty")}</div>
      ) : (
        <div className="space-y-3">
          {approvals.map((a) => (
            <div key={a.id} className="rounded-xl border border-borderline bg-surface p-4 flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-1 text-xs">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-canvas px-2 py-0.5 font-mono font-bold">{a.kind}</span>
                  <StatusBadge status={a.status} />
                  <span className="text-muted">{formatDate(a.created_at)}</span>
                </div>
                <div className="text-muted">
                  <bdi dir="ltr">{JSON.stringify(a.payload)}</bdi>
                </div>
                <Link href={`/runs/${a.run_id}`} className="inline-block text-primary underline">
                  {locale === "ar" ? "فتح شاشة التشغيل" : "Open Run"}
                </Link>
              </div>

              {a.status === "pending" && (a.kind === "bulk" || a.kind === "spend" || a.kind === "mailbox" || a.kind === "outbound") && (
                <div className="flex gap-2">
                  <button
                    onClick={() => decide(a.id, "approve")}
                    className="inline-flex min-h-[44px] items-center gap-1 rounded-lg bg-success px-4 text-xs font-semibold text-white"
                  >
                    <Check className="h-4 w-4" />
                    <span>{t("common.approve")}</span>
                  </button>
                  <button
                    onClick={() => decide(a.id, "reject")}
                    className="inline-flex min-h-[44px] items-center gap-1 rounded-lg bg-danger px-4 text-xs font-semibold text-white"
                  >
                    <X className="h-4 w-4" />
                    <span>{t("common.reject")}</span>
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
