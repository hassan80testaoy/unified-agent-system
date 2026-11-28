"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Code2,
  Eye,
  FileCode2,
  GitBranch,
  GitMerge,
  Layers,
  Play,
  Plus,
  RotateCcw,
  Send,
  Shield,
  Sparkles,
  Terminal,
  Trash2,
} from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { useApp } from "@/i18n/context";
import { ApiError, apiFetch } from "@/lib/api";

interface TodoItem {
  id: string;
  text: string;
  status: string;
}

interface DesignOption {
  index: number;
  title: string;
  description: string;
  accent: string;
  preview_html: string;
}

interface ThreadMessage {
  seq: number;
  role: string;
  content: string;
  type: string;
  metadata?: Record<string, unknown>;
  created_at: string;
}

interface CodingThread {
  id: string;
  parent_thread_id?: string | null;
  title: string;
  goal: string;
  workspace_dir: string;
  mode_key: string;
  model_provider_id?: string | null;
  model_name?: string | null;
  worktree_branch?: string | null;
  worktree_path?: string | null;
  status: string;
  plan_md: string;
  plan_status: string;
  design_options: DesignOption[];
  selected_design_index: number;
  todo: TodoItem[];
  messages: ThreadMessage[];
  context_snapshot: Record<string, unknown>;
  system_prompt_snapshot: string;
  checkpoint: Record<string, unknown>;
  subagent_role?: string | null;
  updated_at: string;
}

interface ArtifactItem {
  id: string;
  kind: string;
  path: string;
  size: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface DiffItem {
  path: string;
  status: string;
  diff: string;
}

interface RaceCandidate {
  provider_id: string;
  model_name: string;
  branch: string;
  worktree_dir: string;
  passed: boolean;
  score: number;
  duration_ms: number;
}

interface RaceItem {
  id: string;
  prompt: string;
  test_command: string;
  status: string;
  winner_branch?: string | null;
  winner_provider?: string | null;
  merged: boolean;
  candidates: RaceCandidate[];
}

interface GoalItem {
  id: string;
  title: string;
  outcome: string;
  status: string;
  autopilot: boolean;
  checkpoint: Record<string, unknown>;
}

interface HookItem {
  id: string;
  name: string;
  event: string;
  tool_pattern: string;
  command: string;
  enabled: boolean;
  block_on_fail: boolean;
}

type WorkbenchTab = "live" | "changes" | "browser" | "context" | "prompt" | "agents";

export default function CodingAgentPage() {
  const { t, locale } = useApp();
  const isAr = locale === "ar";

  const [threads, setThreads] = useState<CodingThread[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [thread, setThread] = useState<CodingThread | null>(null);
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [races, setRaces] = useState<RaceItem[]>([]);
  const [subagents, setSubagents] = useState<CodingThread[]>([]);
  const [changes, setChanges] = useState<DiffItem[]>([]);
  const [goals, setGoals] = useState<GoalItem[]>([]);
  const [hooks, setHooks] = useState<HookItem[]>([]);
  const [subagentRoles, setSubagentRoles] = useState<string[]>([]);
  const [activeRunning, setActiveRunning] = useState(0);
  const [maxParallel, setMaxParallel] = useState(5);

  const [tab, setTab] = useState<WorkbenchTab>("live");
  const [newTitle, setNewTitle] = useState("");
  const [promptInput, setPromptInput] = useState("");
  const [selectedDesign, setSelectedDesign] = useState(0);
  const [previewWidth, setPreviewWidth] = useState<"375px" | "768px" | "100%">("100%");
  const [todoDraft, setTodoDraft] = useState<TodoItem[]>([]);
  const [newTodoText, setNewTodoText] = useState("");

  // Browser visual artifact object URLs & recording JSON
  const [screenshotUrls, setScreenshotUrls] = useState<Record<string, string>>({});
  const [recordingData, setRecordingData] = useState<Record<string, unknown> | null>(null);

  // Agents / Race / Hooks forms
  const [subagentRole, setSubagentRole] = useState("researcher");
  const [subagentPrompt, setSubagentPrompt] = useState("تحليل بنية الملفات وتلخيص النقاط المهمة");
  const [racePrompt, setRacePrompt] = useState("تنفيذ دالة حسابية مع اجتياز الاختبارات");
  const [raceTestCmd, setRaceTestCmd] = useState("python -c \"import solution; assert solution.solve(2, 3) == 5\"");
  const [hookName, setHookName] = useState("");
  const [hookEvent, setHookEvent] = useState<"pre_tool" | "post_tool" | "on_stop">("post_tool");
  const [hookCmd, setHookCmd] = useState("echo hook-ok");

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    const res = await apiFetch<{
      threads: CodingThread[];
      active_running: number;
      max_parallel: number;
      subagent_roles: string[];
    }>("/api/threads");
    setThreads(res.threads || []);
    setActiveRunning(res.active_running || 0);
    setMaxParallel(res.max_parallel || 5);
    setSubagentRoles(res.subagent_roles || []);
    if (!selectedId && res.threads?.length) {
      setSelectedId(res.threads[0].id);
    }
  }, [selectedId]);

  const loadGlobals = useCallback(async () => {
    const [gRes, hRes] = await Promise.all([
      apiFetch<{ goals: GoalItem[] }>("/api/goals"),
      apiFetch<{ hooks: HookItem[] }>("/api/hooks"),
    ]);
    setGoals(gRes.goals || []);
    setHooks(hRes.hooks || []);
  }, []);

  const loadThreadDetail = useCallback(async (id: string) => {
    if (!id) return;
    const [detail, chg] = await Promise.all([
      apiFetch<{
        thread: CodingThread;
        artifacts: ArtifactItem[];
        races: RaceItem[];
        subagents: CodingThread[];
      }>(`/api/threads/${id}`),
      apiFetch<{ changes: DiffItem[] }>(`/api/threads/${id}/changes`),
    ]);
    setThread(detail.thread);
    setTodoDraft(detail.thread.todo || []);
    setSelectedDesign(detail.thread.selected_design_index || 0);
    setArtifacts(detail.artifacts || []);
    setRaces(detail.races || []);
    setSubagents(detail.subagents || []);
    setChanges(chg.changes || []);
  }, []);

  useEffect(() => {
    loadList().catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
    loadGlobals().catch(() => {});
  }, [loadList, loadGlobals]);

  useEffect(() => {
    if (selectedId) {
      loadThreadDetail(selectedId).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
    }
  }, [selectedId, loadThreadDetail]);

  // Fetch authenticated PNG screenshots & latest recording bundle
  useEffect(() => {
    if (!thread) return;
    const token = typeof window !== "undefined" ? localStorage.getItem("uas_token") : null;
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const shots = artifacts.filter((a) => a.kind === "screenshot").slice(0, 3);
    const urls: Record<string, string> = {};
    let cancelled = false;

    Promise.all(
      shots.map(async (art) => {
        const res = await fetch(`/api/threads/${thread.id}/artifacts/${art.id}/file`, {
          credentials: "include",
          headers,
        });
        if (!res.ok) return;
        const blob = await res.blob();
        if (!cancelled) urls[art.id] = URL.createObjectURL(blob);
      }),
    )
      .then(() => {
        if (!cancelled) setScreenshotUrls(urls);
      })
      .catch(() => {});

    const rec = artifacts.find((a) => a.kind === "recording");
    if (rec) {
      fetch(`/api/threads/${thread.id}/artifacts/${rec.id}/file`, { credentials: "include", headers })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!cancelled && data) setRecordingData(data);
        })
        .catch(() => {});
    } else {
      setRecordingData(null);
    }

    return () => {
      cancelled = true;
      Object.values(urls).forEach((u) => URL.revokeObjectURL(u));
    };
  }, [thread, artifacts]);

  async function handleCreateThread(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch<{ thread: CodingThread }>("/api/threads", {
        method: "POST",
        body: JSON.stringify({
          title: newTitle.trim() || (isAr ? "مهمة برمجة جديدة" : "New coding task"),
          mode_key: "delegate",
        }),
      });
      setNewTitle("");
      await loadList();
      setSelectedId(res.thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSendPrompt(e: React.FormEvent) {
    e.preventDefault();
    if (!thread || !promptInput.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiFetch(`/api/threads/${thread.id}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: promptInput.trim() }),
      });
      setPromptInput("");
      await Promise.all([loadThreadDetail(thread.id), loadList(), loadGlobals()]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleApprovePlan() {
    if (!thread) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/threads/${thread.id}/approve-plan`, {
        method: "POST",
        body: JSON.stringify({ design_index: selectedDesign }),
      });
      setNotice(isAr ? "تمت الموافقة وتنفيذ الخطة والتحقق في المتصفح الحقيقي" : "Approved & executed with real browser verification");
      await Promise.all([loadThreadDetail(thread.id), loadList()]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveTodos() {
    if (!thread) return;
    setBusy(true);
    try {
      await apiFetch(`/api/threads/${thread.id}/todo`, {
        method: "PUT",
        body: JSON.stringify({ items: todoDraft }),
      });
      setNotice(isAr ? "تم تحديث قائمة المهام وحقنها في سياق الوكيل" : "Todo list saved to live context");
      await loadThreadDetail(thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleUndo(toSeq: number) {
    if (!thread) return;
    setBusy(true);
    try {
      await apiFetch(`/api/threads/${thread.id}/undo`, {
        method: "POST",
        body: JSON.stringify({ to_seq: toSeq }),
      });
      setNotice(isAr ? `تم التراجع إلى الخطوة ${toSeq} واسترجاع الملفات على القرص` : `Rewound thread & disk files to #${toSeq}`);
      await loadThreadDetail(thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleFork(fromSeq: number) {
    if (!thread) return;
    setBusy(true);
    try {
      const res = await apiFetch<{ thread: CodingThread }>(`/api/threads/${thread.id}/fork`, {
        method: "POST",
        body: JSON.stringify({ from_seq: fromSeq, use_worktree: true }),
      });
      setNotice(isAr ? `تم إنشاء فرع مستقل: ${res.thread.worktree_branch}` : `Forked onto ${res.thread.worktree_branch}`);
      await loadList();
      setSelectedId(res.thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateWorktree() {
    if (!thread) return;
    setBusy(true);
    try {
      await apiFetch(`/api/threads/${thread.id}/worktree`, { method: "POST", body: JSON.stringify({}) });
      await loadThreadDetail(thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeWorktree() {
    if (!thread) return;
    setBusy(true);
    try {
      await apiFetch(`/api/threads/${thread.id}/worktree/merge`, { method: "POST", body: "{}" });
      setNotice(isAr ? "تم دمج Worktree في المسار الرئيسي" : "Merged worktree into main branch");
      await loadThreadDetail(thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSpawnSubagent(e: React.FormEvent) {
    e.preventDefault();
    if (!thread || !subagentPrompt.trim()) return;
    setBusy(true);
    try {
      await apiFetch(`/api/threads/${thread.id}/subagents`, {
        method: "POST",
        body: JSON.stringify({ role: subagentRole, prompt: subagentPrompt.trim(), use_worktree: true }),
      });
      await Promise.all([loadThreadDetail(thread.id), loadList()]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleStartRace(e: React.FormEvent) {
    e.preventDefault();
    if (!thread) return;
    setBusy(true);
    try {
      await apiFetch(`/api/threads/${thread.id}/race`, {
        method: "POST",
        body: JSON.stringify({
          prompt: racePrompt.trim(),
          test_command: raceTestCmd.trim(),
          candidates: [
            {
              provider_id: "candidate-a",
              model_name: "model-alpha",
              files: { "solution.py": "def solve(a, b):\n    return a - b\n" },
            },
            {
              provider_id: "candidate-b",
              model_name: "model-beta",
              files: { "solution.py": "def solve(a, b):\n    return a + b\n" },
            },
          ],
        }),
      });
      setNotice(isAr ? "اكتمل سباق النماذج ودمج الفائز تلقائياً" : "Model Race finished and merged passing winner");
      await loadThreadDetail(thread.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleAddHook(e: React.FormEvent) {
    e.preventDefault();
    if (!hookName.trim() || !hookCmd.trim()) return;
    setBusy(true);
    try {
      await apiFetch("/api/hooks", {
        method: "POST",
        body: JSON.stringify({
          name: hookName.trim(),
          event: hookEvent,
          tool_pattern: "*",
          command: hookCmd.trim(),
          enabled: true,
          block_on_fail: hookEvent === "pre_tool",
        }),
      });
      setHookName("");
      await loadGlobals();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteHook(hookId: string) {
    setBusy(true);
    try {
      await apiFetch(`/api/hooks/${hookId}`, { method: "DELETE" });
      await loadGlobals();
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="space-y-4">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex items-center gap-3">
            <Code2 className="h-6 w-6 text-emerald-400" />
            <div>
              <h1 className="text-lg font-bold text-slate-100">{t("coding.title")}</h1>
              <p className="text-xs text-slate-400">{t("coding.subtitle")}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-emerald-300">
              {t("coding.parallel_cap")}: {activeRunning}/{maxParallel}
            </span>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-500/40 bg-rose-950/30 p-3 text-sm text-rose-200">
            {error}
          </div>
        )}
        {notice && (
          <div className="rounded-xl border border-emerald-500/40 bg-emerald-950/30 p-3 text-sm text-emerald-200">
            {notice}
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          {/* Agent Manager Sidebar */}
          <aside className="space-y-3 rounded-xl border border-slate-800 bg-slate-900/60 p-3 lg:col-span-3">
            <form onSubmit={handleCreateThread} className="space-y-2">
              <label className="block text-xs font-semibold text-slate-300">
                {t("coding.new_thread")}
              </label>
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder={t("coding.thread_title_ph")}
                className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
              />
              <button
                type="submit"
                disabled={busy}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" />
                {t("coding.create_thread")}
              </button>
            </form>

            <div className="max-h-[480px] space-y-1.5 overflow-y-auto pt-2">
              {threads.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                  className={`w-full rounded-lg border p-2.5 text-start transition ${
                    selectedId === item.id
                      ? "border-emerald-500/60 bg-emerald-500/10"
                      : "border-slate-800 bg-slate-950/60 hover:border-slate-700"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-semibold text-slate-100">
                      {item.title}
                    </span>
                    <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                      {item.status}
                    </span>
                  </div>
                  {item.worktree_branch && (
                    <div className="mt-1 flex items-center gap-1 text-[10px] text-emerald-400">
                      <GitBranch className="h-3 w-3" />
                      <span className="truncate">{item.worktree_branch}</span>
                    </div>
                  )}
                </button>
              ))}
            </div>
          </aside>

          {/* Main Workbench */}
          <section className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4 lg:col-span-9">
            {!thread ? (
              <div className="py-12 text-center text-sm text-slate-400">
                {isAr
                  ? "أنشئ أو اختر مهمة برمجة لبدء التخطيط والتنفيذ."
                  : "Create or select a coding thread to start."}
              </div>
            ) : (
              <>
                {/* Tab Bar */}
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
                  <div className="flex flex-wrap gap-1.5">
                    {(
                      [
                        ["live", t("coding.tab_live"), Sparkles],
                        ["changes", t("coding.tab_changes"), FileCode2],
                        ["browser", t("coding.tab_browser"), Eye],
                        ["context", t("coding.tab_context"), Layers],
                        ["prompt", t("coding.tab_prompt"), Terminal],
                        ["agents", t("coding.tab_agents"), Bot],
                      ] as const
                    ).map(([key, label, Icon]) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setTab(key)}
                        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                          tab === key
                            ? "bg-emerald-600 text-white"
                            : "bg-slate-800/80 text-slate-300 hover:bg-slate-800"
                        }`}
                      >
                        <Icon className="h-3.5 w-3.5" />
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    {!thread.worktree_branch ? (
                      <button
                        type="button"
                        onClick={handleCreateWorktree}
                        disabled={busy}
                        className="flex items-center gap-1 rounded-lg border border-slate-700 px-2.5 py-1 text-slate-200 hover:bg-slate-800"
                      >
                        <GitBranch className="h-3.5 w-3.5 text-emerald-400" />
                        {t("coding.create_worktree")}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={handleMergeWorktree}
                        disabled={busy}
                        className="flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-500"
                      >
                        <GitMerge className="h-3.5 w-3.5" />
                        {t("coding.merge_worktree")} ({thread.worktree_branch})
                      </button>
                    )}
                  </div>
                </div>

                {/* TAB 1: LIVE + PLAN REVIEW + TODO */}
                {tab === "live" && (
                  <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
                    <div className="space-y-4 xl:col-span-8">
                      {/* Plan Review Gate Card */}
                      {thread.status === "awaiting_plan_approval" && (
                        <div className="space-y-3 rounded-xl border border-amber-500/40 bg-amber-950/20 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <h3 className="text-sm font-bold text-amber-300">
                              {t("coding.plan_review")}
                            </h3>
                            <div className="flex gap-1 text-[11px]">
                              {(["375px", "768px", "100%"] as const).map((w) => (
                                <button
                                  key={w}
                                  type="button"
                                  onClick={() => setPreviewWidth(w)}
                                  className={`rounded px-2 py-1 font-mono ${
                                    previewWidth === w
                                      ? "bg-amber-500 text-slate-950"
                                      : "bg-slate-800 text-slate-300"
                                  }`}
                                >
                                  {w === "100%" ? "1280px" : w}
                                </button>
                              ))}
                            </div>
                          </div>

                          <pre className="whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-200">
                            {thread.plan_md}
                          </pre>

                          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                            {(thread.design_options || []).map((opt) => (
                              <button
                                key={opt.index}
                                type="button"
                                onClick={() => setSelectedDesign(opt.index)}
                                className={`rounded-lg border p-2.5 text-start text-xs ${
                                  selectedDesign === opt.index
                                    ? "border-emerald-400 bg-emerald-500/10"
                                    : "border-slate-700 bg-slate-950/70"
                                }`}
                              >
                                <div className="font-semibold text-slate-100">{opt.title}</div>
                                <div className="mt-1 text-[11px] text-slate-400">
                                  {opt.description}
                                </div>
                              </button>
                            ))}
                          </div>

                          {thread.design_options?.[selectedDesign] && (
                            <div className="overflow-x-auto rounded-lg border border-slate-700 bg-slate-950 p-2">
                              <iframe
                                title="design-preview"
                                style={{ width: previewWidth, height: "190px" }}
                                className="mx-auto rounded border border-slate-800"
                                srcDoc={thread.design_options[selectedDesign].preview_html}
                              />
                            </div>
                          )}

                          <button
                            type="button"
                            onClick={handleApprovePlan}
                            disabled={busy}
                            className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-bold text-white hover:bg-emerald-500"
                          >
                            <CheckCircle2 className="h-4 w-4" />
                            {t("coding.approve_execute")}
                          </button>
                        </div>
                      )}

                      {/* Messages Timeline with Undo & Fork */}
                      <div className="max-h-[380px] space-y-2.5 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                        {thread.messages.length === 0 ? (
                          <p className="text-xs text-slate-500">{t("coding.bypass_hint")}</p>
                        ) : (
                          thread.messages.map((m) => (
                            <div
                              key={m.seq}
                              className={`rounded-lg border p-3 text-xs ${
                                m.role === "user"
                                  ? "border-slate-700 bg-slate-900"
                                  : "border-emerald-500/20 bg-slate-900/50"
                              }`}
                            >
                              <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                                <span className="font-mono font-semibold text-emerald-400">
                                  #{m.seq} · {m.role} ({m.type})
                                </span>
                                <div className="flex items-center gap-2">
                                  <button
                                    type="button"
                                    onClick={() => handleUndo(Math.max(0, m.seq - 1))}
                                    className="flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-[10px] hover:bg-slate-800"
                                  >
                                    <RotateCcw className="h-3 w-3" />
                                    {t("coding.undo_here")}
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleFork(m.seq)}
                                    className="flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-[10px] hover:bg-slate-800"
                                  >
                                    <GitBranch className="h-3 w-3" />
                                    {t("coding.fork_here")}
                                  </button>
                                </div>
                              </div>
                              <p className="whitespace-pre-wrap text-slate-200">{m.content}</p>
                            </div>
                          ))
                        )}
                      </div>

                      {/* Prompt Input */}
                      <form onSubmit={handleSendPrompt} className="flex gap-2">
                        <input
                          value={promptInput}
                          onChange={(e) => setPromptInput(e.target.value)}
                          placeholder={t("coding.prompt_ph")}
                          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100"
                        />
                        <button
                          type="submit"
                          disabled={busy || !promptInput.trim()}
                          className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                        >
                          <Send className="h-3.5 w-3.5" />
                          {t("coding.send_prompt")}
                        </button>
                      </form>
                    </div>

                    {/* Live Editable Todo Sidebar */}
                    <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/70 p-3 xl:col-span-4">
                      <h3 className="text-xs font-bold text-slate-200">{t("coding.todo_title")}</h3>
                      <div className="space-y-2">
                        {todoDraft.map((item, idx) => (
                          <div key={item.id || idx} className="flex items-center gap-2">
                            <select
                              value={item.status}
                              onChange={(e) => {
                                const copy = [...todoDraft];
                                copy[idx] = { ...copy[idx], status: e.target.value };
                                setTodoDraft(copy);
                              }}
                              className="rounded border border-slate-700 bg-slate-900 px-1.5 py-1 text-[11px] text-slate-200"
                            >
                              <option value="pending">pending</option>
                              <option value="in_progress">running</option>
                              <option value="done">done</option>
                            </select>
                            <input
                              value={item.text}
                              onChange={(e) => {
                                const copy = [...todoDraft];
                                copy[idx] = { ...copy[idx], text: e.target.value };
                                setTodoDraft(copy);
                              }}
                              className="flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100"
                            />
                          </div>
                        ))}
                      </div>
                      <div className="flex gap-1.5">
                        <input
                          value={newTodoText}
                          onChange={(e) => setNewTodoText(e.target.value)}
                          placeholder={isAr ? "مهمة إضافية..." : "Add step..."}
                          className="flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100"
                        />
                        <button
                          type="button"
                          onClick={() => {
                            if (!newTodoText.trim()) return;
                            setTodoDraft([
                              ...todoDraft,
                              { id: `todo-${Date.now()}`, text: newTodoText.trim(), status: "pending" },
                            ]);
                            setNewTodoText("");
                          }}
                          className="rounded bg-slate-800 px-2 py-1 text-xs text-slate-200"
                        >
                          +
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={handleSaveTodos}
                        disabled={busy}
                        className="w-full rounded-lg bg-slate-800 py-1.5 text-xs font-semibold text-emerald-300 hover:bg-slate-700"
                      >
                        {t("coding.save_todo")}
                      </button>
                    </div>
                  </div>
                )}

                {/* TAB 2: CHANGES & DIFFS */}
                {tab === "changes" && (
                  <div className="space-y-3">
                    <div className="text-xs text-slate-400">
                      Workspace: <span className="font-mono text-slate-200">{thread.workspace_dir}</span>
                    </div>
                    {changes.length === 0 ? (
                      <div className="rounded-lg border border-slate-800 bg-slate-950 p-4 text-xs text-slate-400">
                        {isAr ? "لا توجد تعديلات غير معتمدة في Git حالياً." : "Working tree clean — all edits committed."}
                      </div>
                    ) : (
                      changes.map((c) => (
                        <div key={c.path} className="rounded-lg border border-slate-800 bg-slate-950 p-3">
                          <div className="mb-1 flex items-center justify-between text-xs font-mono text-emerald-300">
                            <span>{c.path}</span>
                            <span>{c.status}</span>
                          </div>
                          <pre className="max-h-60 overflow-auto text-[11px] text-slate-300">
                            {c.diff || "(binary or empty diff)"}
                          </pre>
                        </div>
                      ))
                    )}
                  </div>
                )}

                {/* TAB 3: REAL BROWSER VERIFICATION ARTIFACTS */}
                {tab === "browser" && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                      {artifacts
                        .filter((a) => a.kind === "screenshot")
                        .map((shot) => (
                          <div key={shot.id} className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                            <div className="mb-2 flex items-center justify-between text-xs text-slate-300">
                              <span className="font-mono">{shot.id.slice(0, 8)}</span>
                              <span className="text-emerald-400">PNG ({shot.size} B)</span>
                            </div>
                            {screenshotUrls[shot.id] && (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={screenshotUrls[shot.id]}
                                alt="Browser verification screenshot"
                                className="w-full rounded-lg border border-slate-800"
                              />
                            )}
                          </div>
                        ))}
                    </div>
                    {recordingData && (
                      <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                        <h4 className="mb-2 text-xs font-bold text-slate-200">
                          {isAr ? "سجل خطوات المتصفح الحقيقي" : "Real Browser Verification Recording"}
                        </h4>
                        <pre className="max-h-64 overflow-auto font-mono text-[11px] text-slate-300">
                          {JSON.stringify(recordingData, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}

                {/* TAB 4: CONTEXT */}
                {tab === "context" && (
                  <pre className="max-h-[460px] overflow-auto rounded-xl border border-slate-800 bg-slate-950 p-4 font-mono text-xs text-slate-200">
                    {JSON.stringify(thread.context_snapshot, null, 2)}
                  </pre>
                )}

                {/* TAB 5: SYSTEM PROMPT */}
                {tab === "prompt" && (
                  <pre className="whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950 p-4 text-xs leading-relaxed text-slate-200">
                    {thread.system_prompt_snapshot}
                  </pre>
                )}

                {/* TAB 6: MODEL RACES, SUBAGENTS, GOALS & HOOKS */}
                {tab === "agents" && (
                  <div className="space-y-5">
                    {/* Model Race */}
                    <form onSubmit={handleStartRace} className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                      <h3 className="flex items-center gap-1.5 text-xs font-bold text-emerald-300">
                        <Play className="h-3.5 w-3.5" />
                        {t("coding.run_race")}
                      </h3>
                      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                        <input
                          value={racePrompt}
                          onChange={(e) => setRacePrompt(e.target.value)}
                          className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                        />
                        <input
                          value={raceTestCmd}
                          onChange={(e) => setRaceTestCmd(e.target.value)}
                          className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 font-mono text-xs text-slate-100"
                        />
                      </div>
                      <button
                        type="submit"
                        disabled={busy}
                        className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
                      >
                        {t("coding.run_race")}
                      </button>
                      {races.map((r) => (
                        <div key={r.id} className="mt-2 rounded border border-slate-800 bg-slate-900 p-2 text-xs">
                          <div className="font-semibold text-slate-100">
                            Winner: {r.winner_provider || "none"} · {r.winner_branch || "-"} · Merged:{" "}
                            {r.merged ? "Yes" : "No"}
                          </div>
                          <div className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
                            {r.candidates.map((c) => (
                              <div key={c.branch} className="rounded bg-slate-950 px-2 py-1 font-mono text-[11px]">
                                {c.provider_id} ({c.branch}) — {c.passed ? "PASS" : "FAIL"}
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </form>

                    {/* Specialized Subagents */}
                    <form onSubmit={handleSpawnSubagent} className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                      <h3 className="flex items-center gap-1.5 text-xs font-bold text-indigo-300">
                        <Bot className="h-3.5 w-3.5" />
                        {t("coding.spawn_subagent")}
                      </h3>
                      <div className="flex flex-wrap gap-2">
                        <select
                          value={subagentRole}
                          onChange={(e) => setSubagentRole(e.target.value)}
                          className="rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                        >
                          {(subagentRoles.length
                            ? subagentRoles
                            : ["research", "browser", "self", "explorer", "researcher", "coder", "tester", "reviewer", "writer"]
                          ).map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                        <input
                          value={subagentPrompt}
                          onChange={(e) => setSubagentPrompt(e.target.value)}
                          className="flex-1 rounded border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-100"
                        />
                        <button
                          type="submit"
                          disabled={busy}
                          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500"
                        >
                          {t("coding.spawn_subagent")}
                        </button>
                      </div>
                      {subagents.length > 0 && (
                        <div className="space-y-1 pt-1">
                          {subagents.map((s) => (
                            <div key={s.id} className="rounded bg-slate-900 px-2.5 py-1.5 text-xs text-slate-300">
                              <span className="font-mono text-indigo-300">[{s.subagent_role}]</span> {s.title} ({s.status})
                            </div>
                          ))}
                        </div>
                      )}
                    </form>

                    {/* Goals & Hooks */}
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                      <div className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                        <h3 className="text-xs font-bold text-slate-200">{t("coding.goals_title")}</h3>
                        {goals.length === 0 ? (
                          <p className="text-xs text-slate-500">/goal ...</p>
                        ) : (
                          goals.map((g) => (
                            <div key={g.id} className="rounded border border-slate-800 bg-slate-900 p-2 text-xs">
                              <div className="font-semibold text-slate-100">{g.title}</div>
                              <div className="text-[11px] text-emerald-400">{g.status}</div>
                            </div>
                          ))
                        )}
                      </div>

                      <form onSubmit={handleAddHook} className="space-y-2 rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                        <h3 className="flex items-center gap-1.5 text-xs font-bold text-slate-200">
                          <Shield className="h-3.5 w-3.5 text-amber-400" />
                          {t("coding.hooks_title")}
                        </h3>
                        <div className="flex gap-1.5">
                          <input
                            value={hookName}
                            onChange={(e) => setHookName(e.target.value)}
                            placeholder="name"
                            className="w-28 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
                          />
                          <select
                            value={hookEvent}
                            onChange={(e) => setHookEvent(e.target.value as "pre_tool" | "post_tool" | "on_stop")}
                            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
                          >
                            <option value="pre_tool">pre_tool</option>
                            <option value="post_tool">post_tool</option>
                            <option value="on_stop">on_stop</option>
                          </select>
                          <input
                            value={hookCmd}
                            onChange={(e) => setHookCmd(e.target.value)}
                            className="flex-1 rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs"
                          />
                          <button type="submit" className="rounded bg-amber-600 px-2.5 py-1 text-xs text-white">
                            +
                          </button>
                        </div>
                        <div className="space-y-1">
                          {hooks.map((h) => (
                            <div key={h.id} className="flex items-center justify-between rounded bg-slate-900 px-2 py-1 text-xs">
                              <span>
                                [{h.event}] {h.name}
                              </span>
                              <button
                                type="button"
                                onClick={() => handleDeleteHook(h.id)}
                                className="text-rose-400 hover:text-rose-300"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                      </form>
                    </div>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
}
