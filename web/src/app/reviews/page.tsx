"use client";

import React, { useEffect, useState } from "react";
import { Check, GitMerge, MessageSquare, RefreshCw } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface ReviewItem {
  id: string;
  provider: string;
  repo: string;
  external_number: number;
  title: string;
  author: string;
  source_branch: string;
  target_branch: string;
  url: string;
  status: string;
  diff_text: string;
  checks: Array<{ name: string; status: string }>;
  comments: Array<{ author: string; body: string; created_at?: string }>;
}

export default function UnifiedReviewsPage() {
  const { t, locale } = useApp();
  const isAr = locale === "ar";
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [selected, setSelected] = useState<ReviewItem | null>(null);
  const [filterProv, setFilterProv] = useState("");
  const [commentBody, setCommentBody] = useState("");
  const [connProv, setConnProv] = useState("github");
  const [connRepo, setConnRepo] = useState("");
  const [connToken, setConnToken] = useState("");
  const [notice, setNotice] = useState("");

  const load = async () => {
    const qs = filterProv ? `?provider=${encodeURIComponent(filterProv)}` : "";
    const res = await apiFetch<{ reviews: ReviewItem[] }>(`/api/reviews${qs}`).catch(() => ({ reviews: [] }));
    const list = res.reviews || [];
    setReviews(list);
    if (selected) {
      const updated = list.find((r) => r.id === selected.id) || list[0] || null;
      setSelected(updated);
    } else if (list.length > 0) {
      setSelected(list[0]);
    }
  };

  useEffect(() => {
    load();
  }, [filterProv]);

  const connectRepo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!connRepo.trim() || !connToken.trim()) return;
    await apiFetch("/api/reviews/connect", {
      method: "POST",
      body: JSON.stringify({ provider: connProv, repo: connRepo.trim(), token: connToken.trim() }),
    });
    setConnToken("");
    setNotice(isAr ? "تم حفظ ربط المستودع بنجاح." : "Repository connected securely.");
  };

  const syncNow = async () => {
    const res = await apiFetch<{ synced_count: number }>("/api/reviews/sync", { method: "POST" });
    setNotice(isAr ? `تمت مزامنة ${res.synced_count} طلب سحب/دمج.` : `Synced ${res.synced_count} PRs/MRs.`);
    await load();
  };

  const postComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected || !commentBody.trim()) return;
    await apiFetch(`/api/reviews/${selected.id}/comment`, {
      method: "POST",
      body: JSON.stringify({ body: commentBody.trim() }),
    });
    setCommentBody("");
    await load();
  };

  const approveNow = async () => {
    if (!selected) return;
    await apiFetch(`/api/reviews/${selected.id}/approve`, {
      method: "POST",
      body: JSON.stringify({ note: "Approved from Unified Review screen" }),
    });
    setNotice(isAr ? "تم اعتماد المراجعة." : "Review approved.");
    await load();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <GitMerge className="h-5 w-5 text-primary" />
            <span>{t("phase7.reviews_title")}</span>
          </h1>
          <p className="text-xs text-muted">{t("phase7.reviews_sub")}</p>
        </div>
        <div className="flex gap-2 text-xs">
          <select value={filterProv} onChange={(e) => setFilterProv(e.target.value)} className="rounded-lg border border-borderline bg-surface px-2.5 py-1.5">
            <option value="">{isAr ? "الكل (GitHub / GitLab)" : "All Providers"}</option>
            <option value="github">GitHub</option>
            <option value="gitlab">GitLab</option>
            <option value="local">Local</option>
          </select>
          <button type="button" onClick={syncNow} className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">
            <RefreshCw className="h-3.5 w-3.5" />
            <span>{t("phase7.sync_btn")}</span>
          </button>
        </div>
      </div>

      {notice && <div className="rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-xs text-primary">{notice}</div>}

      <form onSubmit={connectRepo} className="flex flex-wrap gap-2 rounded-xl border border-borderline bg-surface p-3 text-xs">
        <select value={connProv} onChange={(e) => setConnProv(e.target.value)} className="rounded border border-borderline bg-canvas px-2.5 py-1.5">
          <option value="github">GitHub</option>
          <option value="gitlab">GitLab</option>
        </select>
        <input dir="ltr" value={connRepo} onChange={(e) => setConnRepo(e.target.value)} placeholder="owner/repo" className="flex-1 rounded border border-borderline bg-canvas px-2.5 py-1.5 text-end font-mono" />
        <input dir="ltr" type="password" value={connToken} onChange={(e) => setConnToken(e.target.value)} placeholder="Token" className="flex-1 rounded border border-borderline bg-canvas px-2.5 py-1.5 text-end font-mono" />
        <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">{t("common.save")}</button>
      </form>

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <div className="space-y-2 rounded-xl border border-borderline bg-surface p-3">
          {reviews.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted">{t("common.empty")}</div>
          ) : (
            reviews.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => setSelected(r)}
                className={`w-full rounded-lg border p-2.5 text-start text-xs ${
                  selected?.id === r.id ? "border-primary bg-primary/10" : "border-borderline bg-canvas"
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono text-[10px] uppercase text-primary">{r.provider} #{r.external_number}</span>
                  <StatusBadge status={r.status} />
                </div>
                <div className="mt-1 font-bold">{r.title}</div>
                <div className="text-[10px] text-muted">{r.repo} · {r.source_branch} → {r.target_branch}</div>
              </button>
            ))
          )}
        </div>

        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
          {!selected ? (
            <div className="p-8 text-center text-muted">{t("common.empty")}</div>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2">
                <div>
                  <div className="text-sm font-bold">{selected.title}</div>
                  <div className="font-mono text-[11px] text-muted">
                    <bdi dir="ltr">{selected.repo} · {selected.source_branch} → {selected.target_branch}</bdi>
                  </div>
                </div>
                <button type="button" onClick={approveNow} className="inline-flex items-center gap-1 rounded-lg bg-success px-3 py-1.5 font-semibold text-white">
                  <Check className="h-3.5 w-3.5" />
                  <span>{t("common.approve")}</span>
                </button>
              </div>

              {selected.checks.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {selected.checks.map((c, idx) => (
                    <span key={idx} className="rounded border border-borderline bg-canvas px-2 py-0.5 font-mono text-[11px]">
                      {c.name}: {c.status}
                    </span>
                  ))}
                </div>
              )}

              <pre dir="ltr" className="max-h-72 overflow-auto rounded-lg border border-borderline bg-canvas p-3 text-start font-mono text-[11px]">
                {selected.diff_text || "(No diff available)"}
              </pre>

              <div className="space-y-2 border-t border-borderline pt-2">
                <div className="flex items-center gap-1.5 font-bold">
                  <MessageSquare className="h-3.5 w-3.5 text-primary" />
                  <span>{isAr ? "التعليقات" : "Comments"}</span>
                </div>
                {selected.comments.map((cm, idx) => (
                  <div key={idx} className="rounded bg-canvas p-2">
                    <span className="font-mono font-bold text-primary">{cm.author}: </span>
                    <span>{cm.body}</span>
                  </div>
                ))}
                <form onSubmit={postComment} className="flex gap-2">
                  <input value={commentBody} onChange={(e) => setCommentBody(e.target.value)} placeholder={isAr ? "اكتب ملاحظة مراجعة..." : "Add review comment..."} className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-1.5" />
                  <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">{t("chat.send")}</button>
                </form>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
