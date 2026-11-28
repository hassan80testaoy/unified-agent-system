"use client";

import React, { useEffect, useState } from "react";
import { KeyRound, Shield, Cpu, Brain, Globe2, Trash2, CheckCircle2, Plus, RefreshCw, Sliders } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

interface ProviderAccountItem {
  id: string;
  label: string;
  auth_type: string;
  last4: string;
  status: string;
  priority: number;
  quota_used: number;
  quota_limit: number;
  last_failover_at: string | null;
}

interface ProviderItem {
  id: string;
  name: string;
  kind: string;
  connect_type?: string;
  protocol?: string;
  path_id?: string | null;
  api_base: string;
  local_only?: boolean;
  budget_usd_daily: number;
  budget_usd_monthly: number;
  enabled: boolean;
  has_key: boolean;
  key_last4: string;
  accounts?: ProviderAccountItem[];
}

interface ModeItem {
  id: string;
  key: string;
  name_ar: string;
  name_en: string;
  description_ar: string;
  description_en: string;
  sandbox_mode: string;
  approval_policy: string;
  is_default: boolean;
  is_builtin: boolean;
}

interface GoogleAccountItem {
  id: string;
  label: string;
  email_masked: string;
  oauth_scopes: string;
  imap_ok: boolean;
  oauth_ok: boolean;
  status: string;
  active_profile_id: string | null;
}

interface MemoryItem {
  id: string;
  category: string;
  content: string;
  allow_training: boolean;
}

export default function SettingsPage() {
  const { t, locale, numeralStyle, setNumeralStyle, calendarType, setCalendarType } = useApp();

  const [providers, setProviders] = useState<ProviderItem[]>([]);
  const [modes, setModes] = useState<ModeItem[]>([]);
  const [googleAccounts, setGoogleAccounts] = useState<GoogleAccountItem[]>([]);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [tunnelCmd, setTunnelCmd] = useState("");
  const [keyEdits, setKeyEdits] = useState<Record<string, string>>({});
  const [acctLabels, setAcctLabels] = useState<Record<string, string>>({});
  const [acctKeys, setAcctKeys] = useState<Record<string, string>>({});
  const [statusMsg, setStatusMsg] = useState("");

  // Zadloop 3-Way Add Provider Modal
  const [showAddProv, setShowAddProv] = useState(false);
  const [provConnect, setProvConnect] = useState<"signin" | "api_key" | "custom_endpoint">("api_key");
  const [provId, setProvId] = useState("");
  const [provName, setProvName] = useState("");
  const [provBase, setProvBase] = useState("https://api.openai.com/v1");
  const [provKey, setProvKey] = useState("");
  const [provProtocol, setProvProtocol] = useState("openai-completions");
  const [provPathId, setProvPathId] = useState("");
  const [provModelId, setProvModelId] = useState("");
  const [fetchedModels, setFetchedModels] = useState<{ id: string; name: string }[]>([]);

  // Custom Mode Creator
  const [modeKey, setModeKey] = useState("");
  const [modeNameAr, setModeNameAr] = useState("");
  const [modePrompt, setModePrompt] = useState("");

  // Google Account Vault form
  const [gLabel, setGLabel] = useState("");
  const [gEmail, setGEmail] = useState("");
  const [gPassword, setGPassword] = useState("");
  const [gAllowSend, setGAllowSend] = useState(false);

  // Memory add
  const [memText, setMemText] = useState("");

  const load = () => {
    apiFetch<{ providers: ProviderItem[] }>("/api/providers").then((r) => setProviders(r.providers)).catch(() => null);
    apiFetch<{ modes: ModeItem[] }>("/api/modes").then((r) => setModes(r.modes)).catch(() => null);
    apiFetch<{ google_accounts: GoogleAccountItem[] }>("/api/google-accounts").then((r) => setGoogleAccounts(r.google_accounts)).catch(() => null);
    apiFetch<{ memory: MemoryItem[] }>("/api/memory").then((r) => setMemories(r.memory)).catch(() => null);
    apiFetch<{ tunnel: { ssh_command: string } }>("/api/settings").then((r) => setTunnelCmd(r.tunnel?.ssh_command || "")).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const saveProviderKey = async (providerId: string) => {
    const val = (keyEdits[providerId] || "").trim();
    if (!val) return;
    await apiFetch(`/api/settings/keys/${providerId}`, {
      method: "PUT",
      body: JSON.stringify({ secret: val }),
    });
    setKeyEdits((prev) => ({ ...prev, [providerId]: "" }));
    setStatusMsg(locale === "ar" ? `تم حفظ مفتاح ${providerId} مشفراً.` : `Saved encrypted key for ${providerId}.`);
    load();
  };

  const toggleProvider = async (p: ProviderItem) => {
    await apiFetch(`/api/providers/${p.id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled: !p.enabled }),
    });
    load();
  };

  const testProvider = async (providerId: string) => {
    const res = await apiFetch<{ ok: boolean; error?: string; latency_ms?: number }>(`/api/providers/${providerId}/test`, {
      method: "POST",
    });
    setStatusMsg(res.ok ? `${providerId}: OK (${res.latency_ms ?? 0}ms)` : `${providerId}: ${res.error}`);
  };

  const fetchModels = async () => {
    const res = await apiFetch<{ ok: boolean; models: { id: string; name: string }[]; error?: string }>(
      "/api/providers/fetch-models",
      {
        method: "POST",
        body: JSON.stringify({ api_base: provBase, api_key: provKey || null, protocol: provProtocol }),
      }
    );
    if (res.ok && res.models.length > 0) {
      setFetchedModels(res.models);
      setProvModelId(res.models[0].id);
      setStatusMsg(locale === "ar" ? `تم جلب ${res.models.length} موديل.` : `Fetched ${res.models.length} models.`);
    } else {
      setStatusMsg(res.error || "No models returned");
    }
  };

  const createProvider = async (e: React.FormEvent) => {
    e.preventDefault();
    const chosenModel = provModelId.trim() || fetchedModels[0]?.id || "gpt-4o-mini";
    await apiFetch("/api/providers", {
      method: "POST",
      body: JSON.stringify({
        id: provId.trim().toLowerCase(),
        name: provName.trim(),
        connect_type: provConnect,
        protocol: provProtocol,
        path_id: provPathId.trim() || null,
        api_base: provBase.trim(),
        api_key: provKey.trim() || null,
        models: { cheap: chosenModel, mid: chosenModel },
        enabled: true,
      }),
    });
    setShowAddProv(false);
    setProvId("");
    setProvName("");
    setProvKey("");
    setFetchedModels([]);
    setStatusMsg(locale === "ar" ? "تمت إضافة المزود بنجاح." : "Provider added.");
    load();
  };

  const addAccountToProvider = async (providerId: string) => {
    const label = (acctLabels[providerId] || "").trim();
    const apiKey = (acctKeys[providerId] || "").trim();
    if (!label || !apiKey) return;
    await apiFetch(`/api/providers/${providerId}/accounts`, {
      method: "POST",
      body: JSON.stringify({ label, auth_type: "api_key", api_key: apiKey }),
    });
    setAcctLabels((p) => ({ ...p, [providerId]: "" }));
    setAcctKeys((p) => ({ ...p, [providerId]: "" }));
    load();
  };

  const rotateAccountFailover = async (providerId: string) => {
    await apiFetch(`/api/providers/${providerId}/failover`, { method: "POST" });
    setStatusMsg(locale === "ar" ? `تم تبديل حساب ${providerId}.` : `Rotated failover account for ${providerId}.`);
    load();
  };

  const setDefaultMode = async (key: string) => {
    await apiFetch(`/api/modes/${key}`, { method: "PATCH", body: JSON.stringify({ is_default: true }) });
    load();
  };

  const createCustomMode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!modeKey.trim() || !modeNameAr.trim()) return;
    await apiFetch("/api/modes", {
      method: "POST",
      body: JSON.stringify({
        key: modeKey.trim().toLowerCase(),
        name_ar: modeNameAr.trim(),
        name_en: modeKey.trim(),
        system_prompt: modePrompt.trim(),
      }),
    });
    setModeKey("");
    setModeNameAr("");
    setModePrompt("");
    load();
  };

  const addGoogleAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    await apiFetch("/api/google-accounts", {
      method: "POST",
      body: JSON.stringify({
        label: gLabel || "حساب جوجل",
        email: gEmail,
        password: gPassword || null,
        allow_gmail_send: gAllowSend,
      }),
    });
    setGLabel("");
    setGEmail("");
    setGPassword("");
    setStatusMsg(locale === "ar" ? "تم حفظ حساب Google مشفراً مع بروفايل متصفح مخصص." : "Google account encrypted & profile initialized.");
    load();
  };

  const addMemoryFact = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!memText.trim()) return;
    await apiFetch("/api/memory", {
      method: "POST",
      body: JSON.stringify({ category: "preference", content: memText, allow_training: false }),
    });
    setMemText("");
    load();
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">{t("nav.settings")}</h1>

      {statusMsg && (
        <div className="flex items-center gap-2 rounded-xl border border-success/30 bg-success/10 p-3 text-xs font-semibold text-success">
          <CheckCircle2 className="h-4 w-4" />
          <span>{statusMsg}</span>
        </div>
      )}

      {/* 1. Google Accounts (§2.3) */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold">
            <Shield className="h-4 w-4 text-primary" />
            <span>{t("google.title")}</span>
          </h2>
          <p className="mt-1 text-xs text-muted">
            {locale === "ar"
              ? "يُستخدم الحساب فقط للتسجيل/الدخول وقراءة رسائل التفعيل في المواقع التي تحددها أنت. لا يغيّر الوكيل كلمة المرور أو إعدادات الأمان أبداً."
              : "Used strictly to sign up/in and read verification mail on sites you assign. Never alters security settings."}
          </p>
        </div>

        <form onSubmit={addGoogleAccount} className="grid gap-3 rounded-xl border border-borderline bg-canvas p-4 sm:grid-cols-2">
          <div className="sm:col-span-2 text-xs font-bold">{t("google.add_title")}</div>
          <input
            type="text"
            required
            value={gLabel}
            onChange={(e) => setGLabel(e.target.value)}
            placeholder={t("google.label_field")}
            className="rounded-lg border border-borderline bg-surface px-3 py-2 text-xs"
          />
          <input
            type="email"
            dir="ltr"
            required
            value={gEmail}
            onChange={(e) => setGEmail(e.target.value)}
            placeholder="name@gmail.com"
            className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end text-xs"
          />
          <input
            type="password"
            dir="ltr"
            value={gPassword}
            onChange={(e) => setGPassword(e.target.value)}
            placeholder={t("google.password_field")}
            className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end text-xs"
          />
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={gAllowSend} onChange={(e) => setGAllowSend(e.target.checked)} />
            <span>{locale === "ar" ? "السماح بإرسال تحقق فقط (افتراضي مغلق)" : "Allow verification send (default OFF)"}</span>
          </label>
          <button type="submit" className="min-h-[40px] rounded-lg bg-primary px-4 text-xs font-semibold text-white sm:col-span-2">
            {t("common.save")}
          </button>
        </form>

        <div className="space-y-2">
          {googleAccounts.map((g) => (
            <div key={g.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-borderline bg-canvas p-3 text-xs">
              <div>
                <div className="font-bold">
                  {g.label} — <bdi dir="ltr" className="font-mono">{g.email_masked}</bdi>
                </div>
                <div className="text-muted">
                  scopes: <bdi dir="ltr">{g.oauth_scopes}</bdi> · profile: <bdi dir="ltr">{g.active_profile_id || "—"}</bdi>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={g.status} />
                <button
                  onClick={() => apiFetch(`/api/google-accounts/${g.id}/test`, { method: "POST" }).then(load)}
                  className="rounded border border-borderline bg-surface px-2.5 py-1"
                >
                  {t("common.test")}
                </button>
                <button
                  onClick={() => apiFetch(`/api/google-accounts/${g.id}/revoke-session`, { method: "POST" }).then(load)}
                  className="rounded border border-warning/40 bg-surface px-2.5 py-1 text-warning"
                >
                  {t("google.revoke_session")}
                </button>
                <button
                  onClick={() => apiFetch(`/api/google-accounts/${g.id}/disconnect`, { method: "POST" }).then(load)}
                  className="rounded border border-danger/40 bg-surface px-2.5 py-1 text-danger"
                >
                  {t("common.disconnect")}
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 2. Multi-Provider Registry & Zadloop 3-Way Add + Multi-Account Failover */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-2 text-base font-bold">
              <Cpu className="h-4 w-4 text-primary" />
              <span>{locale === "ar" ? "مزودو الذكاء الاصطناعي والراوتر" : "Multi-Provider LLM Registry"}</span>
            </h2>
            <p className="mt-1 text-xs text-muted">
              {locale === "ar"
                ? "مفاتيح الـ API تُحفظ مشفرة Write-only (يظهر آخر 4 أحرف فقط) مع دعم تبديل الحسابات التلقائي."
                : "Write-only Fernet encrypted API keys (last 4 shown) with automatic multi-account failover."}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowAddProv((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white"
          >
            <Plus className="h-4 w-4" />
            <span>{t("providers_ui.add_provider")}</span>
          </button>
        </div>

        {showAddProv && (
          <form onSubmit={createProvider} className="space-y-3 rounded-xl border border-primary/40 bg-canvas p-4 text-xs">
            <div className="grid grid-cols-3 gap-2">
              {(["signin", "api_key", "custom_endpoint"] as const).map((way) => (
                <button
                  key={way}
                  type="button"
                  onClick={() => setProvConnect(way)}
                  className={`rounded-lg border px-2 py-2 font-semibold ${
                    provConnect === way ? "border-primary bg-primary/15 text-primary" : "border-borderline bg-surface"
                  }`}
                >
                  {way === "signin"
                    ? t("providers_ui.connect_signin")
                    : way === "api_key"
                    ? t("providers_ui.connect_api_key")
                    : t("providers_ui.connect_custom")}
                </button>
              ))}
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <input
                type="text"
                dir="ltr"
                required
                value={provId}
                onChange={(e) => setProvId(e.target.value)}
                placeholder="provider id (e.g. my-llm)"
                className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end font-mono"
              />
              <input
                type="text"
                required
                value={provName}
                onChange={(e) => setProvName(e.target.value)}
                placeholder={locale === "ar" ? "اسم المزود" : "Provider Name"}
                className="rounded-lg border border-borderline bg-surface px-3 py-2"
              />
              <input
                type="text"
                dir="ltr"
                value={provBase}
                onChange={(e) => setProvBase(e.target.value)}
                placeholder="https://api.example.com/v1"
                className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end font-mono"
              />
              <select
                value={provProtocol}
                onChange={(e) => setProvProtocol(e.target.value)}
                className="rounded-lg border border-borderline bg-surface px-3 py-2 font-mono"
              >
                <option value="openai-completions">openai-completions</option>
                <option value="openai-responses">openai-responses</option>
                <option value="anthropic">anthropic</option>
                <option value="gemini">gemini</option>
                <option value="ollama">ollama</option>
              </select>
              {provConnect !== "signin" && (
                <input
                  type="password"
                  dir="ltr"
                  value={provKey}
                  onChange={(e) => setProvKey(e.target.value)}
                  placeholder="API Key..."
                  className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end font-mono"
                />
              )}
              <input
                type="text"
                dir="ltr"
                value={provPathId}
                onChange={(e) => setProvPathId(e.target.value)}
                placeholder="Path ID (optional)"
                className="rounded-lg border border-borderline bg-surface px-3 py-2 text-end font-mono"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                dir="ltr"
                value={provModelId}
                onChange={(e) => setProvModelId(e.target.value)}
                placeholder="model id (e.g. gpt-4o-mini)"
                className="flex-1 rounded-lg border border-borderline bg-surface px-3 py-2 text-end font-mono"
              />
              <button type="button" onClick={fetchModels} className="rounded-lg border border-borderline bg-surface px-3 py-2 font-semibold hover:border-primary">
                {t("providers_ui.fetch_models")}
              </button>
              <button type="submit" className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
                {t("common.save")}
              </button>
            </div>

            {fetchedModels.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {fetchedModels.slice(0, 12).map((fm) => (
                  <button
                    key={fm.id}
                    type="button"
                    onClick={() => setProvModelId(fm.id)}
                    className={`rounded border px-2 py-1 font-mono text-[11px] ${
                      provModelId === fm.id ? "border-primary bg-primary/15 text-primary" : "border-borderline bg-surface"
                    }`}
                  >
                    {fm.id}
                  </button>
                ))}
              </div>
            )}
          </form>
        )}

        <div className="space-y-3">
          {providers.map((p) => (
            <div key={p.id} className="space-y-2 rounded-lg border border-borderline bg-canvas p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2 font-bold">
                    <span>
                      {p.name} (<bdi dir="ltr" className="font-mono">{p.id}</bdi>)
                    </span>
                    {p.local_only && (
                      <span className="rounded bg-info/15 px-1.5 py-0.5 text-[10px] font-semibold text-info">
                        {locale === "ar" ? "محلي فقط" : "Local only"}
                      </span>
                    )}
                  </div>
                  <div className="text-muted">
                    {p.connect_type || "api_key"} · {p.protocol || "openai-completions"} · daily cap: ${p.budget_usd_daily} ·{" "}
                    {p.has_key ? `key ****${p.key_last4}` : "no saved key"}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {p.kind === "llm" && (
                    <>
                      <input
                        type="password"
                        dir="ltr"
                        value={keyEdits[p.id] || ""}
                        onChange={(e) => setKeyEdits((prev) => ({ ...prev, [p.id]: e.target.value }))}
                        placeholder="API Key..."
                        className="w-36 rounded border border-borderline bg-surface px-2 py-1 text-end font-mono"
                      />
                      <button onClick={() => saveProviderKey(p.id)} className="rounded bg-primary px-2.5 py-1 text-white">
                        <KeyRound className="me-1 inline h-3 w-3" />
                        {t("common.save")}
                      </button>
                    </>
                  )}
                  <button onClick={() => testProvider(p.id)} className="rounded border border-borderline bg-surface px-2.5 py-1">
                    {t("common.test")}
                  </button>
                  <button onClick={() => toggleProvider(p)} className={`rounded px-2.5 py-1 font-semibold ${p.enabled ? "bg-success/15 text-success" : "bg-surface text-muted"}`}>
                    {p.enabled ? t("status.active") : t("status.disabled")}
                  </button>
                </div>
              </div>

              {p.kind === "llm" && (
                <div className="border-t border-borderline pt-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {(p.accounts || []).map((a) => (
                        <span
                          key={a.id}
                          className={`rounded border px-2 py-0.5 text-[11px] ${
                            a.status === "active" ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-amber-500/10 text-warning"
                          }`}
                        >
                          {a.label} ({a.status})
                        </span>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <input
                        type="text"
                        value={acctLabels[p.id] || ""}
                        onChange={(e) => setAcctLabels((prev) => ({ ...prev, [p.id]: e.target.value }))}
                        placeholder={locale === "ar" ? "اسم الحساب" : "Account label"}
                        className="w-28 rounded border border-borderline bg-surface px-2 py-1"
                      />
                      <input
                        type="password"
                        dir="ltr"
                        value={acctKeys[p.id] || ""}
                        onChange={(e) => setAcctKeys((prev) => ({ ...prev, [p.id]: e.target.value }))}
                        placeholder="Key..."
                        className="w-28 rounded border border-borderline bg-surface px-2 py-1 text-end font-mono"
                      />
                      <button type="button" onClick={() => addAccountToProvider(p.id)} className="rounded border border-borderline bg-surface px-2 py-1">
                        {t("providers_ui.add_account")}
                      </button>
                      {(p.accounts || []).length > 0 && (
                        <button type="button" onClick={() => rotateAccountFailover(p.id)} className="inline-flex items-center gap-1 rounded border border-warning/40 bg-surface px-2 py-1 text-warning">
                          <RefreshCw className="h-3 w-3" />
                          <span>{t("providers_ui.failover")}</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 2b. Modes Presets (Zadloop + AFH + DeepSeek Harness) */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-4">
        <h2 className="flex items-center gap-2 text-base font-bold">
          <Sliders className="h-4 w-4 text-primary" />
          <span>{t("modes.title")}</span>
        </h2>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {modes.map((m) => (
            <div key={m.key} className={`rounded-xl border p-3 text-xs ${m.is_default ? "border-primary bg-primary/5" : "border-borderline bg-canvas"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold">{locale === "ar" ? m.name_ar : m.name_en}</span>
                {m.is_default ? (
                  <span className="rounded bg-primary px-2 py-0.5 text-[10px] font-semibold text-white">{t("modes.default_badge")}</span>
                ) : (
                  <button onClick={() => setDefaultMode(m.key)} className="rounded border border-borderline bg-surface px-2 py-0.5 text-[10px] hover:border-primary">
                    {t("modes.set_default")}
                  </button>
                )}
              </div>
              <p className="mt-1.5 text-muted">{locale === "ar" ? m.description_ar : m.description_en}</p>
              <div className="mt-2 font-mono text-[10px] text-muted">
                <bdi dir="ltr">{m.key}</bdi> · {m.sandbox_mode}
              </div>
            </div>
          ))}
        </div>

        <form onSubmit={createCustomMode} className="grid gap-2 rounded-xl border border-borderline bg-canvas p-3 text-xs sm:grid-cols-4">
          <input
            type="text"
            dir="ltr"
            value={modeKey}
            onChange={(e) => setModeKey(e.target.value)}
            placeholder="mode-key"
            className="rounded-lg border border-borderline bg-surface px-2.5 py-1.5 text-end font-mono"
          />
          <input
            type="text"
            value={modeNameAr}
            onChange={(e) => setModeNameAr(e.target.value)}
            placeholder={locale === "ar" ? "اسم الوضع" : "Mode Name"}
            className="rounded-lg border border-borderline bg-surface px-2.5 py-1.5"
          />
          <input
            type="text"
            value={modePrompt}
            onChange={(e) => setModePrompt(e.target.value)}
            placeholder={locale === "ar" ? "تعليمات النظام..." : "System instructions..."}
            className="rounded-lg border border-borderline bg-surface px-2.5 py-1.5"
          />
          <button type="submit" className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white">
            {t("modes.create_title")}
          </button>
        </form>
      </section>

      {/* 3. Manual CAPTCHA & noVNC Tunnel */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-2 text-xs">
        <h2 className="text-sm font-bold">{locale === "ar" ? "الكابتشا اليدوية (noVNC خلف SSH Tunnel فقط)" : "Manual CAPTCHA (noVNC behind SSH Tunnel)"}</h2>
        <div className="text-muted">CAPTCHA_SOLVER=manual · CAPTCHA_MAX_SPEND_USD=0</div>
        {tunnelCmd && (
          <div className="rounded-lg border border-borderline bg-canvas p-2.5 font-mono">
            <bdi dir="ltr">{tunnelCmd}</bdi>
          </div>
        )}
      </section>

      {/* 4. Memory & Locale / Numerals / Calendar */}
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Brain className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "ذاكرة الوكيل المشفرة (تُصدّر وتُحذف بالكامل)" : "Encrypted Memory (Exportable & Deletable)"}</span>
          </h2>
          <form onSubmit={addMemoryFact} className="flex gap-2">
            <input
              type="text"
              value={memText}
              onChange={(e) => setMemText(e.target.value)}
              placeholder={locale === "ar" ? "أضف تفضيل أو حقيقة..." : "Add preference or fact..."}
              className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
            />
            <button type="submit" className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-white">
              {t("common.save")}
            </button>
          </form>
          <div className="space-y-1.5">
            {memories.map((m) => (
              <div key={m.id} className="flex items-center justify-between rounded bg-canvas p-2 text-xs">
                <span>{m.content}</span>
                <button onClick={() => apiFetch(`/api/memory/${m.id}`, { method: "DELETE" }).then(load)} className="text-danger">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Globe2 className="h-4 w-4 text-primary" />
            <span>{locale === "ar" ? "الأرقام والتقويم والمنطقة الزمنية" : "Numerals, Calendar & Timezone"}</span>
          </h2>
          <div>
            <label className="mb-1 block font-medium">{locale === "ar" ? "شكل الأرقام" : "Numeral Style"}</label>
            <select
              value={numeralStyle}
              onChange={(e) => setNumeralStyle(e.target.value as "western" | "arabic_indic")}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
            >
              <option value="western">0-9 (Western — الافتراضي للجداول)</option>
              <option value="arabic_indic">٠-٩ (Arabic-Indic)</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block font-medium">{locale === "ar" ? "التقويم" : "Calendar"}</label>
            <select
              value={calendarType}
              onChange={(e) => setCalendarType(e.target.value as "gregorian" | "hijri")}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
            >
              <option value="gregorian">{locale === "ar" ? "ميلادي (افتراضي)" : "Gregorian (Default)"}</option>
              <option value="hijri">{locale === "ar" ? "هجري" : "Hijri"}</option>
            </select>
          </div>
        </section>
      </div>
    </div>
  );
}
