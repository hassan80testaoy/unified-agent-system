"use client";

import React, { useEffect, useState } from "react";
import { BellRing, Check, Clock, Globe, Play, SearchCheck, Sparkles, Target, X } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface GoalStep {
  id: string;
  title: string;
  owner: string;
  deadline: string;
  success_metric: string;
  tool: string;
  status: string;
  result_summary?: string;
}

interface GoalPlanItem {
  id: string;
  title: string;
  status: string;
  deadline?: string | null;
  target_url?: string | null;
  keyword?: string | null;
  steps?: GoalStep[];
  progress?: {
    completed_steps: number;
    total_steps: number;
    progress_pct: number;
  };
}

interface SiteAuditItem {
  id: string;
  url: string;
  score: number;
  network_path: string;
  screenshot_url?: string | null;
  report_md: string;
  findings: Array<{
    id: string;
    category: string;
    severity: string;
    title_ar: string;
    title_en: string;
    fix_ar: string;
    fix_en: string;
    evidence: { page: string; line?: number | null; snippet?: string; screenshot_url?: string | null };
  }>;
  created_at?: string | null;
}

interface WakeSubItem {
  id: string;
  source: string;
  trigger_prompt: string;
  action_type: string;
  enabled: boolean;
  last_fired_at?: string | null;
}

interface WakeLogItem {
  id: string;
  source: string;
  external_event_id: string;
  status: string;
  summary: string;
  fired_at?: string | null;
}

interface ProposalItem {
  id: string;
  loop_name: string;
  title_ar: string;
  title_en: string;
  detail_md: string;
  status: string;
  created_at?: string | null;
}

export default function GoalsWakesPage() {
  const { t, locale, formatDate } = useApp();
  const isAr = locale === "ar";
  const [tab, setTab] = useState<"goals" | "auditor" | "wakes" | "proactive">("goals");
  const [notice, setNotice] = useState("");

  // §7A.1 Goal Planner
  const [goalSentence, setGoalSentence] = useState("");
  const [goals, setGoals] = useState<GoalPlanItem[]>([]);

  // §7A.3 Site Auditor
  const [auditUrl, setAuditUrl] = useState("https://example.com");
  const [auditProfileUrl, setAuditProfileUrl] = useState("");
  const [auditTargetUrl, setAuditTargetUrl] = useState("");
  const [audits, setAudits] = useState<SiteAuditItem[]>([]);

  // §7B Wakes & 08:00
  const [subs, setSubs] = useState<WakeSubItem[]>([]);
  const [wakeLogs, setWakeLogs] = useState<WakeLogItem[]>([]);
  const [wakePrompt, setWakePrompt] = useState("");
  const [wakeSubjectFilter, setWakeSubjectFilter] = useState("");
  const [tz, setTz] = useState("Africa/Cairo");
  const [dailyRes, setDailyRes] = useState<{ sent?: boolean; skipped?: boolean; reason?: string; report_md?: string } | null>(null);

  // §7B.3 Proactive Loops
  const [proposals, setProposals] = useState<ProposalItem[]>([]);
  const [busy, setBusy] = useState(false);

  const loadAll = async () => {
    const [gRes, aRes, sRes, lRes, tzRes, pRes] = await Promise.all([
      apiFetch<{ goals: GoalPlanItem[] }>("/api/goals").catch(() => ({ goals: [] })),
      apiFetch<{ audits: SiteAuditItem[] }>("/api/audit/sites").catch(() => ({ audits: [] })),
      apiFetch<{ subscriptions: WakeSubItem[] }>("/api/wakes/subscriptions").catch(() => ({ subscriptions: [] })),
      apiFetch<{ logs: WakeLogItem[] }>("/api/wakes/logs").catch(() => ({ logs: [] })),
      apiFetch<{ timezone: string }>("/api/reports/daily-0800/timezone").catch(() => ({ timezone: "Africa/Cairo" })),
      apiFetch<{ proposals: ProposalItem[] }>("/api/proactive/proposals").catch(() => ({ proposals: [] })),
    ]);
    setGoals(gRes.goals || []);
    setAudits(aRes.audits || []);
    setSubs(sRes.subscriptions || []);
    setWakeLogs(lRes.logs || []);
    setTz(tzRes.timezone || "Africa/Cairo");
    setProposals(pRes.proposals || []);
  };

  useEffect(() => {
    loadAll();
  }, []);

  const planGoal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!goalSentence.trim()) return;
    setBusy(true);
    try {
      await apiFetch("/api/goals/plan-from-text", {
        method: "POST",
        body: JSON.stringify({ sentence: goalSentence.trim(), execute_auto_steps: true }),
      });
      setGoalSentence("");
      setNotice(isAr ? "تم إنشاء الخطة وتنفيذ الخطوات التلقائية." : "Goal plan created & agent steps executed.");
      await loadAll();
    } finally {
      setBusy(false);
    }
  };

  const toggleGoalStep = async (goalId: string, stepId: string) => {
    await apiFetch(`/api/goals/${goalId}/steps/${stepId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "done", note: "Approved by owner in UI" }),
    });
    await loadAll();
  };

  const runAudit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!auditUrl.trim()) return;
    setBusy(true);
    try {
      await apiFetch("/api/audit/site", {
        method: "POST",
        body: JSON.stringify({
          url: auditUrl.trim(),
          profile_url: auditProfileUrl.trim() || null,
          target_backlink_url: auditTargetUrl.trim() || null,
        }),
      });
      setNotice(isAr ? "اكتمل فحص الموقع وحفظ الأدلة واللقطة." : "Site audit complete with line & screenshot evidence.");
      await loadAll();
    } finally {
      setBusy(false);
    }
  };

  const addWakeSub = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!wakePrompt.trim()) return;
    await apiFetch("/api/wakes/subscriptions", {
      method: "POST",
      body: JSON.stringify({
        source: "gmail",
        trigger_prompt: wakePrompt.trim(),
        action_type: "activation",
        filter: wakeSubjectFilter.trim() ? { subject_contains: wakeSubjectFilter.trim() } : {},
      }),
    });
    setWakePrompt("");
    setWakeSubjectFilter("");
    await loadAll();
  };

  const runDailyReport = async () => {
    const res = await apiFetch<{ sent?: boolean; skipped?: boolean; reason?: string; report_md?: string }>(
      "/api/reports/daily-0800/run",
      { method: "POST", body: JSON.stringify({ enforce_hour: false, post_to_chat: true }) }
    );
    setDailyRes(res);
    setNotice(
      res.sent
        ? isAr ? "تم إرسال تقرير 08:00 الصباحي." : "08:00 Daily Report generated."
        : isAr ? `تم تخطي التقرير (${res.reason}).` : `Report skipped (${res.reason}).`
    );
  };

  const saveTz = async () => {
    await apiFetch("/api/reports/daily-0800/timezone", {
      method: "PUT",
      body: JSON.stringify({ timezone: tz }),
    });
    setNotice(isAr ? "تم حفظ المنطقة الزمنية." : "Timezone saved.");
  };

  const scanProactive = async () => {
    setBusy(true);
    try {
      await apiFetch("/api/proactive/scan", { method: "POST" });
      await loadAll();
    } finally {
      setBusy(false);
    }
  };

  const decideProposal = async (id: string, dec: "approve" | "reject") => {
    await apiFetch(`/api/proactive/proposals/${id}/${dec}`, { method: "POST" });
    await loadAll();
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{t("phase7.goals_hub_title")}</h1>
          <p className="text-xs text-muted">{t("phase7.goals_hub_sub")}</p>
        </div>
        <div className="flex flex-wrap gap-1.5 text-xs">
          {(
            [
              ["goals", t("phase7.tab_goals"), Target],
              ["auditor", t("phase7.tab_auditor"), SearchCheck],
              ["wakes", t("phase7.tab_wakes"), BellRing],
              ["proactive", t("phase7.tab_proactive"), Sparkles],
            ] as const
          ).map(([k, lbl, Icon]) => (
            <button
              key={k}
              type="button"
              onClick={() => setTab(k)}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 font-semibold ${
                tab === k ? "border-primary bg-primary text-white" : "border-borderline bg-surface text-ink"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{lbl}</span>
            </button>
          ))}
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-xs text-primary">{notice}</div>
      )}

      {tab === "goals" && (
        <div className="space-y-4">
          <form onSubmit={planGoal} className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
            <h2 className="text-sm font-bold">{t("phase7.goal_sentence_title")}</h2>
            <div className="flex flex-wrap gap-2">
              <input
                value={goalSentence}
                onChange={(e) => setGoalSentence(e.target.value)}
                placeholder={isAr ? "عايز موقعي example.com يطلع الأول على كلمة سيو..." : "Rank example.com #1 for technical SEO..."}
                className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
              />
              <button
                type="submit"
                disabled={busy}
                className="inline-flex items-center gap-1 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white"
              >
                <Play className="h-3.5 w-3.5" />
                <span>{t("phase7.plan_btn")}</span>
              </button>
            </div>
          </form>

          <div className="space-y-3">
            {goals.length === 0 ? (
              <div className="rounded-xl border border-borderline bg-surface p-6 text-center text-xs text-muted">{t("common.empty")}</div>
            ) : (
              goals.map((g) => (
                <div key={g.id} className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-bold text-sm">{g.title}</div>
                    <div className="flex items-center gap-2 text-xs">
                      <StatusBadge status={g.status} />
                      {g.progress && (
                        <span className="rounded bg-canvas px-2 py-0.5 font-mono">
                          {g.progress.completed_steps}/{g.progress.total_steps} ({g.progress.progress_pct}%)
                        </span>
                      )}
                    </div>
                  </div>
                  {g.steps && g.steps.length > 0 && (
                    <div className="space-y-1.5 text-xs">
                      {g.steps.map((st) => (
                        <div key={st.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-canvas px-3 py-2">
                          <div>
                            <span className="font-mono text-[10px] text-primary">[{st.owner}] </span>
                            <span className="font-medium">{st.title}</span>
                            {st.result_summary && <div className="text-[11px] text-muted">{st.result_summary}</div>}
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-[10px] text-muted">{st.deadline}</span>
                            <StatusBadge status={st.status} />
                            {st.status !== "done" && (
                              <button
                                type="button"
                                onClick={() => toggleGoalStep(g.id, st.id)}
                                className="rounded border border-success/40 bg-success/10 px-2 py-0.5 text-[11px] text-success"
                              >
                                {isAr ? "اعتماد / تم" : "Mark Done"}
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {tab === "auditor" && (
        <div className="space-y-4">
          <form onSubmit={runAudit} className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
            <h2 className="flex items-center gap-2 text-sm font-bold">
              <Globe className="h-4 w-4 text-primary" />
              <span>{t("phase7.auditor_title")}</span>
            </h2>
            <div className="grid gap-2 md:grid-cols-3">
              <input
                dir="ltr"
                value={auditUrl}
                onChange={(e) => setAuditUrl(e.target.value)}
                placeholder="https://example.com"
                className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
              />
              <input
                dir="ltr"
                value={auditProfileUrl}
                onChange={(e) => setAuditProfileUrl(e.target.value)}
                placeholder="Profile URL (optional backlink verify)"
                className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
              />
              <input
                dir="ltr"
                value={auditTargetUrl}
                onChange={(e) => setAuditTargetUrl(e.target.value)}
                placeholder="Target Backlink URL (optional)"
                className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
              />
            </div>
            <button type="submit" disabled={busy} className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
              {t("phase7.audit_run_btn")}
            </button>
          </form>

          {audits.map((a) => (
            <div key={a.id} className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2">
                <bdi dir="ltr" className="font-mono font-bold text-primary">{a.url}</bdi>
                <div className="flex items-center gap-2">
                  <span className="rounded bg-canvas px-2 py-0.5 font-mono">score={a.score}/100</span>
                  <span className="rounded bg-canvas px-2 py-0.5 font-mono">path={a.network_path}</span>
                </div>
              </div>
              {a.screenshot_url && (
                <img src={a.screenshot_url} alt="Site audit screenshot" className="max-h-48 rounded-lg border border-borderline" />
              )}
              <div className="space-y-2">
                {a.findings.map((f, idx) => (
                  <div key={`${a.id}-${idx}`} className="rounded-lg bg-canvas p-2.5 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-bold">{isAr ? f.title_ar : f.title_en}</span>
                      <StatusBadge status={f.severity} />
                    </div>
                    <div className="font-mono text-[11px] text-muted">
                      <bdi dir="ltr">
                        {f.evidence.page}
                        {f.evidence.line ? ` (line ${f.evidence.line})` : ""}
                        {f.evidence.snippet ? ` — ${f.evidence.snippet}` : ""}
                      </bdi>
                    </div>
                    <div className="text-primary">{isAr ? f.fix_ar : f.fix_en}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === "wakes" && (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <form onSubmit={addWakeSub} className="rounded-xl border border-borderline bg-surface p-4 space-y-2 text-xs">
              <h2 className="text-sm font-bold">{t("phase7.wakes_sub_title")}</h2>
              <input
                value={wakePrompt}
                onChange={(e) => setWakePrompt(e.target.value)}
                placeholder={isAr ? "مثال: فعّل الحساب تلقائياً فور وصول إيميل التفعيل" : "Activate account when verification Gmail arrives"}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
              />
              <input
                value={wakeSubjectFilter}
                onChange={(e) => setWakeSubjectFilter(e.target.value)}
                placeholder={isAr ? "فلتر العنوان (اختياري): Verify" : "Subject filter (optional): Verify"}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
              />
              <button type="submit" className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
                {t("common.save")}
              </button>
              <div className="space-y-1 pt-2">
                {subs.map((s) => (
                  <div key={s.id} className="flex items-center justify-between rounded bg-canvas p-2">
                    <span>[{s.source}] {s.trigger_prompt}</span>
                    <span className="font-mono text-[10px] text-muted">{s.last_fired_at ? formatDate(s.last_fired_at) : "idle"}</span>
                  </div>
                ))}
              </div>
            </form>

            <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
              <h2 className="flex items-center gap-2 text-sm font-bold">
                <Clock className="h-4 w-4 text-primary" />
                <span>{t("phase7.daily_0800_title")}</span>
              </h2>
              <div className="flex gap-2">
                <input dir="ltr" value={tz} onChange={(e) => setTz(e.target.value)} className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono" />
                <button type="button" onClick={saveTz} className="rounded-lg border border-borderline px-3 py-2 font-semibold">
                  {t("common.save")}
                </button>
                <button type="button" onClick={runDailyReport} className="rounded-lg bg-primary px-3 py-2 font-semibold text-white">
                  {t("phase7.daily_0800_run")}
                </button>
              </div>
              {dailyRes && (
                <pre className="whitespace-pre-wrap rounded-lg bg-canvas p-3 text-[11px]">
                  {dailyRes.sent ? dailyRes.report_md : `Skipped: ${dailyRes.reason}`}
                </pre>
              )}
              <div className="space-y-1">
                <div className="font-semibold text-muted">{t("phase7.wakes_logs_title")}</div>
                {wakeLogs.map((l) => (
                  <div key={l.id} className="rounded bg-canvas p-2 text-[11px]">
                    [{l.source}] {l.external_event_id} — {l.summary}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === "proactive" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between rounded-xl border border-borderline bg-surface p-4">
            <div className="text-xs text-muted">{t("phase7.proactive_hint")}</div>
            <button
              type="button"
              onClick={scanProactive}
              disabled={busy}
              className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white"
            >
              {t("phase7.proactive_scan_btn")}
            </button>
          </div>
          {proposals.length === 0 ? (
            <div className="rounded-xl border border-borderline bg-surface p-6 text-center text-xs text-muted">{t("common.empty")}</div>
          ) : (
            proposals.map((p) => (
              <div key={p.id} className="rounded-xl border border-borderline bg-surface p-4 flex flex-wrap items-center justify-between gap-3 text-xs">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-canvas px-2 py-0.5 font-mono text-[10px]">{p.loop_name}</span>
                    <StatusBadge status={p.status} />
                  </div>
                  <div className="font-bold">{isAr ? p.title_ar : p.title_en || p.title_ar}</div>
                  <div className="text-muted">{p.detail_md}</div>
                </div>
                {p.status === "pending" && (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => decideProposal(p.id, "approve")}
                      className="inline-flex items-center gap-1 rounded-lg bg-success px-3 py-1.5 font-semibold text-white"
                    >
                      <Check className="h-3.5 w-3.5" />
                      <span>{t("common.approve")}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => decideProposal(p.id, "reject")}
                      className="inline-flex items-center gap-1 rounded-lg bg-danger px-3 py-1.5 font-semibold text-white"
                    >
                      <X className="h-3.5 w-3.5" />
                      <span>{t("common.reject")}</span>
                    </button>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
