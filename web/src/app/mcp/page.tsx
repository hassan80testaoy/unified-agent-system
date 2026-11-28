"use client";

import React, { useEffect, useState } from "react";
import { Download, Play, Plus, RefreshCw, Shield, Terminal, Trash2 } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface McpServerItem {
  id: string;
  name: string;
  source_type: string;
  command: string;
  url: string;
  env_keys: string[];
  scope: string;
  project_key?: string | null;
  namespace_prefix: string;
  working_dir: string;
  is_remote: boolean;
  status: string;
  last_error?: string | null;
  tools: { name: string; namespaced_name: string; description: string; read_only: boolean }[];
}

interface McpPermissionItem {
  id: string;
  session_id: string;
  server_id: string;
  tool_name: string;
  grant_mode: string;
  revoked: boolean;
}

interface StoreSkillItem {
  slug: string;
  name: string;
  description: string;
  references: string[];
}

interface InstalledSkillItem {
  id: string;
  slug: string;
  name: string;
  description: string;
  source: string;
  has_script: boolean;
  tools: { name: string; description: string }[];
  references: string[];
}

export default function McpSkillsPage() {
  const { t, locale } = useApp();
  const isAr = locale === "ar";

  const [servers, setServers] = useState<McpServerItem[]>([]);
  const [tools, setTools] = useState<{ namespaced_name: string; server_name: string; description: string; read_only: boolean }[]>([]);
  const [permissions, setPermissions] = useState<McpPermissionItem[]>([]);
  const [readOnlyAuto, setReadOnlyAuto] = useState(false);

  const [storeSkills, setStoreSkills] = useState<StoreSkillItem[]>([]);
  const [installedSkills, setInstalledSkills] = useState<InstalledSkillItem[]>([]);
  const [refPreview, setRefPreview] = useState<string>("");

  // 3-way Add Server form
  const [srcType, setSrcType] = useState<"command" | "link" | "json">("command");
  const [srvName, setSrvName] = useState("");
  const [srvCmd, setSrvCmd] = useState("");
  const [srvUrl, setSrvUrl] = useState("");
  const [srvJson, setSrvJson] = useState("");
  const [srvScope, setSrvScope] = useState<"all_sessions" | "project">("all_sessions");
  const [srvProjKey, setSrvProjKey] = useState("");
  const [srvPrefix, setSrvPrefix] = useState("");
  const [srvCwd, setSrvCwd] = useState("");
  const [envPairs, setEnvPairs] = useState<{ key: string; val: string }[]>([{ key: "", val: "" }]);
  const [notice, setNotice] = useState("");

  const load = () => {
    apiFetch<{ servers: McpServerItem[]; tools: typeof tools; read_only_auto_allow: boolean }>("/api/mcp/servers")
      .then((r) => {
        setServers(r.servers || []);
        setTools(r.tools || []);
        setReadOnlyAuto(Boolean(r.read_only_auto_allow));
      })
      .catch(() => null);
    apiFetch<{ permissions: McpPermissionItem[] }>("/api/mcp/permissions")
      .then((r) => setPermissions(r.permissions || []))
      .catch(() => null);
    apiFetch<{ store: StoreSkillItem[] }>("/api/skills/store")
      .then((r) => setStoreSkills(r.store || []))
      .catch(() => null);
    apiFetch<{ skills: InstalledSkillItem[] }>("/api/skills/installed")
      .then((r) => setInstalledSkills(r.skills || []))
      .catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const addServer = async (e: React.FormEvent) => {
    e.preventDefault();
    const envObj: Record<string, string> = {};
    for (const pair of envPairs) {
      if (pair.key.trim()) envObj[pair.key.trim()] = pair.val;
    }
    const res = await apiFetch<{ server: McpServerItem }>("/api/mcp/servers", {
      method: "POST",
      body: JSON.stringify({
        source_type: srcType,
        name: srvName,
        command: srvCmd,
        url: srvUrl,
        raw_json: srcType === "json" ? srvJson : null,
        env: envObj,
        scope: srvScope,
        project_key: srvScope === "project" ? srvProjKey : null,
        namespace_prefix: srvPrefix || null,
        working_dir: srvCwd,
      }),
    });
    setNotice(`${res.server.name}: ${res.server.status}`);
    setSrvName("");
    setSrvCmd("");
    setSrvUrl("");
    setSrvJson("");
    setSrvPrefix("");
    load();
  };

  const toggleReadOnlyAuto = async () => {
    const res = await apiFetch<{ read_only_auto_allow: boolean }>("/api/mcp/settings", {
      method: "PUT",
      body: JSON.stringify({ read_only_auto_allow: !readOnlyAuto }),
    });
    setReadOnlyAuto(res.read_only_auto_allow);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">{t("phase5.mcp_title")}</h1>
        <p className="text-xs text-muted">
          {isAr
            ? "إضافة خوادم MCP بأمر أو رابط أو JSON، صلاحيات دقيقة مرتبطة بالجلسة، ومتجر مهارات بضغطة واحدة."
            : "Add local/remote MCP servers by Command, Link, or JSON, manage session-scoped tool permissions & 1-click skills."}
        </p>
      </div>

      {notice && <div className="rounded-xl border border-primary/40 bg-primary/10 p-3 text-xs font-semibold text-primary">{notice}</div>}

      {/* Add MCP Server (Zadloop 3-way) */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
        <div className="grid grid-cols-3 gap-2">
          {(["command", "link", "json"] as const).map((way) => (
            <button
              key={way}
              type="button"
              onClick={() => setSrcType(way)}
              className={`rounded-lg border py-2 font-semibold ${
                srcType === way ? "border-primary bg-primary/15 text-primary" : "border-borderline bg-canvas"
              }`}
            >
              {way === "command" ? (isAr ? "أمر (Command)" : "Command") : way === "link" ? (isAr ? "رابط (Link)" : "Link") : "Paste JSON"}
            </button>
          ))}
        </div>

        <form onSubmit={addServer} className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-3">
            <input
              value={srvName}
              onChange={(e) => setSrvName(e.target.value)}
              placeholder={isAr ? "اسم الخادم" : "Server Name"}
              className="rounded-lg border border-borderline bg-canvas px-3 py-2"
            />
            <input
              dir="ltr"
              value={srvPrefix}
              onChange={(e) => setSrvPrefix(e.target.value)}
              placeholder="Namespace prefix (max 32: github_mcp)"
              className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
            />
            <select
              value={srvScope}
              onChange={(e) => setSrvScope(e.target.value as "all_sessions" | "project")}
              className="rounded-lg border border-borderline bg-canvas px-3 py-2"
            >
              <option value="all_sessions">{isAr ? "كل الجلسات" : "All Sessions"}</option>
              <option value="project">{isAr ? "مشروع محدد" : "Single Project"}</option>
            </select>
          </div>

          {srcType === "command" && (
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                dir="ltr"
                value={srvCmd}
                onChange={(e) => setSrvCmd(e.target.value)}
                placeholder="npx -y @modelcontextprotocol/server-filesystem ."
                className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
              />
              <input
                dir="ltr"
                value={srvCwd}
                onChange={(e) => setSrvCwd(e.target.value)}
                placeholder="Working directory (optional)"
                className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
              />
            </div>
          )}

          {srcType === "link" && (
            <input
              dir="ltr"
              value={srvUrl}
              onChange={(e) => setSrvUrl(e.target.value)}
              placeholder="http://127.0.0.1:8010/api/mcp/bridge"
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
            />
          )}

          {srcType === "json" && (
            <textarea
              dir="ltr"
              rows={3}
              value={srvJson}
              onChange={(e) => setSrvJson(e.target.value)}
              placeholder='{"mcpServers": {"my-srv": {"command": "npx", "args": ["-y", "@scope/server"]}}}'
              className="w-full rounded-lg border border-borderline bg-canvas p-2 text-end font-mono"
            />
          )}

          {srvScope === "project" && (
            <input
              dir="ltr"
              value={srvProjKey}
              onChange={(e) => setSrvProjKey(e.target.value)}
              placeholder="project_key"
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono"
            />
          )}

          {/* Env vars + add variable */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-muted">{isAr ? "متغيرات البيئة" : "Environment Variables"}</span>
              <button
                type="button"
                onClick={() => setEnvPairs((prev) => [...prev, { key: "", val: "" }])}
                className="inline-flex items-center gap-1 rounded border border-borderline bg-canvas px-2 py-0.5"
              >
                <Plus className="h-3 w-3" />
                <span>{isAr ? "متغير" : "Variable"}</span>
              </button>
            </div>
            {envPairs.map((pair, idx) => (
              <div key={idx} className="grid grid-cols-2 gap-2">
                <input
                  dir="ltr"
                  value={pair.key}
                  onChange={(e) => {
                    const copy = [...envPairs];
                    copy[idx] = { ...copy[idx], key: e.target.value };
                    setEnvPairs(copy);
                  }}
                  placeholder="KEY"
                  className="rounded border border-borderline bg-canvas px-2 py-1 text-end font-mono"
                />
                <input
                  type="password"
                  dir="ltr"
                  value={pair.val}
                  onChange={(e) => {
                    const copy = [...envPairs];
                    copy[idx] = { ...copy[idx], val: e.target.value };
                    setEnvPairs(copy);
                  }}
                  placeholder="VALUE"
                  className="rounded border border-borderline bg-canvas px-2 py-1 text-end font-mono"
                />
              </div>
            ))}
          </div>

          <button type="submit" className="rounded-lg bg-primary px-4 py-2 font-semibold text-white">
            {isAr ? "إضافة وفحص الخادم" : "Add & Probe Server"}
          </button>
        </form>
      </section>

      {/* Servers + Permissions */}
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Terminal className="h-4 w-4 text-primary" />
            <span>{isAr ? "الخوادم والأدوات المسمّاة" : "Servers & Namespaced Tools"}</span>
          </h2>
          {servers.map((s) => (
            <div key={s.id} className="rounded-lg border border-borderline bg-canvas p-3 space-y-1.5">
              <div className="flex items-center justify-between">
                <div className="font-bold">
                  {s.name} · <bdi dir="ltr" className="font-mono text-primary">{s.namespace_prefix}</bdi>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`rounded px-2 py-0.5 font-mono text-[10px] ${s.status === "active" ? "bg-success/15 text-success" : "bg-danger/15 text-danger"}`}>
                    {s.status}
                  </span>
                  <button
                    type="button"
                    onClick={() => apiFetch(`/api/mcp/servers/${s.id}/probe`, { method: "POST" }).then(load)}
                    className="rounded border border-borderline bg-surface p-1"
                  >
                    <RefreshCw className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => apiFetch(`/api/mcp/servers/${s.id}`, { method: "DELETE" }).then(load)}
                    className="text-danger"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              {s.last_error && <div className="text-[11px] text-danger">{s.last_error}</div>}
              {s.tools.length > 0 && (
                <div className="flex flex-wrap gap-1 pt-1">
                  {s.tools.map((tl) => (
                    <span key={tl.namespaced_name} className="rounded bg-surface px-2 py-0.5 font-mono text-[10px]">
                      {tl.namespaced_name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </section>

        <section className="rounded-xl border border-borderline bg-surface p-4 space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-bold">
              <Shield className="h-4 w-4 text-primary" />
              <span>{isAr ? "صلاحيات أدوات الجلسات" : "Session Tool Permissions"}</span>
            </h2>
            <label className="flex items-center gap-1.5 text-[11px]">
              <input type="checkbox" checked={readOnlyAuto} onChange={toggleReadOnlyAuto} />
              <span>{isAr ? "تشغيل القراءة فقط تلقائياً" : "Auto-run read-only tools"}</span>
            </label>
          </div>
          {permissions.map((perm) => (
            <div key={perm.id} className="flex items-center justify-between rounded bg-canvas p-2">
              <div>
                <div className="font-mono font-semibold">{perm.tool_name}</div>
                <div className="text-[10px] text-muted">
                  session: {perm.session_id.slice(0, 8)} · {perm.grant_mode} · {perm.revoked ? "REVOKED" : "ACTIVE"}
                </div>
              </div>
              {!perm.revoked && (
                <button
                  type="button"
                  onClick={() => apiFetch(`/api/mcp/permissions/${perm.id}/revoke`, { method: "POST" }).then(load)}
                  className="rounded border border-danger/40 px-2 py-0.5 text-danger"
                >
                  {isAr ? "سحب" : "Revoke"}
                </button>
              )}
            </div>
          ))}
          <div className="rounded-lg border border-borderline bg-canvas p-2.5 text-[11px] text-muted">
            StreamableHTTP MCP Bridge: <bdi dir="ltr" className="font-mono">POST /api/mcp/bridge</bdi> (6 safe arms only)
          </div>
        </section>
      </div>

      {/* Skills Store + Installed Skills */}
      <section className="rounded-xl border border-borderline bg-surface p-4 space-y-4 text-xs">
        <h2 className="text-sm font-bold">{isAr ? "متجر المهارات (skills.sh — تثبيت بضغطة واحدة)" : "Skills Store (skills.sh — 1-click install)"}</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {storeSkills.map((st) => (
            <div key={st.slug} className="flex items-center justify-between gap-2 rounded-lg border border-borderline bg-canvas p-3">
              <div>
                <div className="font-bold">{st.name} (<bdi dir="ltr" className="font-mono">{st.slug}</bdi>)</div>
                <div className="text-[11px] text-muted">{st.description}</div>
              </div>
              <button
                type="button"
                onClick={() => apiFetch("/api/skills/store/install", { method: "POST", body: JSON.stringify({ slug: st.slug }) }).then(load)}
                className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-semibold text-white"
              >
                <Download className="h-3.5 w-3.5" />
                <span>{isAr ? "تثبيت" : "Install"}</span>
              </button>
            </div>
          ))}
        </div>

        <div className="space-y-2 border-t border-borderline pt-3">
          <div className="font-semibold">{isAr ? "المهارات المثبتة والمكتشفة (~/.agent/skills + المشروع)" : "Installed & Discovered Skills"}</div>
          {installedSkills.map((sk) => (
            <div key={sk.id} className="flex flex-wrap items-center justify-between gap-2 rounded bg-canvas p-2.5">
              <div>
                <span className="font-bold">{sk.name}</span>{" "}
                <span className="font-mono text-[10px] text-muted">[{sk.source}]</span>
              </div>
              <div className="flex gap-1.5">
                {sk.references.map((ref) => (
                  <button
                    key={ref}
                    type="button"
                    onClick={() => apiFetch<{ content: string }>(`/api/skills/installed/${sk.slug}/references/${ref}`).then((r) => setRefPreview(r.content))}
                    className="rounded border border-borderline bg-surface px-2 py-0.5 text-[10px]"
                  >
                    {ref}
                  </button>
                ))}
                {sk.has_script && sk.tools[0] && (
                  <button
                    type="button"
                    onClick={() =>
                      apiFetch(`/api/skills/installed/${sk.slug}/run`, {
                        method: "POST",
                        body: JSON.stringify({ tool_name: sk.tools[0].name, arguments: { text: "Hello Unified Agent", payload: { ok: true } } }),
                      }).then((res) => setNotice(JSON.stringify(res)))
                    }
                    className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-0.5 text-[10px] text-white"
                  >
                    <Play className="h-3 w-3" />
                    <span>{sk.tools[0].name}</span>
                  </button>
                )}
              </div>
            </div>
          ))}
          {refPreview && <pre className="whitespace-pre-wrap rounded bg-canvas p-2 font-mono text-[11px]">{refPreview}</pre>}
        </div>
      </section>
    </div>
  );
}
