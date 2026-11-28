"use client";

import React, { useEffect, useState } from "react";
import { Download, Eye, Image as ImageIcon, Layers, Presentation, Sparkles } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface ArtifactRecord {
  id: string;
  kind: string;
  title: string;
  size: number;
  preview_url: string;
  download_url: string;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
}

export default function ArtifactsPage() {
  const { t, locale, formatDate } = useApp();
  const isAr = locale === "ar";

  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([]);
  const [selected, setSelected] = useState<ArtifactRecord | null>(null);
  const [previewHtml, setPreviewHtml] = useState<string>("");
  const [previewBlobUrl, setPreviewBlobUrl] = useState<string>("");

  const [appPrompt, setAppPrompt] = useState("");
  const [deckTitle, setDeckTitle] = useState("");
  const [deckTopic, setDeckTopic] = useState("");
  const [imgPrompt, setImgPrompt] = useState("");
  const [imgMockMode, setImgMockMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const load = async () => {
    const res = await apiFetch<{ artifacts: ArtifactRecord[] }>("/api/artifacts");
    setArtifacts(res.artifacts || []);
    if (!selected && res.artifacts?.length) {
      setSelected(res.artifacts[0]);
    }
  };

  useEffect(() => {
    load().catch(() => null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let active = true;
    let createdUrl = "";
    setPreviewHtml("");
    setPreviewBlobUrl("");
    if (!selected) return;

    (async () => {
      const token = typeof window !== "undefined" ? localStorage.getItem("uas_token") : null;
      const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
      const resp = await fetch(selected.preview_url, { headers, credentials: "include" }).catch(() => null);
      if (!active || !resp || !resp.ok) return;
      const ctype = resp.headers.get("content-type") || "";
      if (ctype.includes("text/html") || selected.kind === "web_app" || selected.kind === "html_app" || selected.kind === "presentation") {
        const htmlText = await resp.text();
        if (active) setPreviewHtml(htmlText);
      } else {
        const blob = await resp.blob();
        createdUrl = URL.createObjectURL(blob);
        if (active) setPreviewBlobUrl(createdUrl);
      }
    })();

    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [selected]);

  const handleDownload = async (art: ArtifactRecord) => {
    const token = typeof window !== "undefined" ? localStorage.getItem("uas_token") : null;
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    const resp = await fetch(art.download_url, { headers, credentials: "include" });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${art.title || art.id}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleBuildApp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!appPrompt.trim() || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const res = await apiFetch<{ artifact: ArtifactRecord }>("/api/artifacts/build-app", {
        method: "POST",
        body: JSON.stringify({ prompt: appPrompt.trim() }),
      });
      setAppPrompt("");
      setSelected(res.artifact);
      setNotice(isAr ? "تم بناء تطبيق الويب التفاعلي وجاهز للمعاينة." : "Interactive web app built & ready.");
      await load();
    } finally {
      setBusy(false);
    }
  };

  const handleBuildDeck = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!deckTitle.trim() || !deckTopic.trim() || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const res = await apiFetch<{ artifact: ArtifactRecord }>("/api/artifacts/presentations", {
        method: "POST",
        body: JSON.stringify({ title: deckTitle.trim(), topic: deckTopic.trim() }),
      });
      setDeckTitle("");
      setDeckTopic("");
      setSelected(res.artifact);
      setNotice(isAr ? "تم إنشاء العرض التقديمي HTML بنجاح." : "HTML Presentation deck generated.");
      await load();
    } finally {
      setBusy(false);
    }
  };

  const handleGenImage = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!imgPrompt.trim() || busy) return;
    setBusy(true);
    setNotice("");
    try {
      const res = await apiFetch<{ status: string; artifact?: ArtifactRecord; message_ar?: string }>("/api/images/generate", {
        method: "POST",
        body: JSON.stringify({ prompt: imgPrompt.trim(), mock_mode: imgMockMode }),
      });
      if (res.artifact) {
        setSelected(res.artifact);
        setImgPrompt("");
      }
      setNotice(res.message_ar || res.status);
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Layers className="h-5 w-5 text-primary" />
          <span>{t("phase8.artifacts_title")}</span>
        </h1>
        <p className="text-xs text-muted">{t("phase8.artifacts_sub")}</p>
      </div>

      {notice && (
        <div className="rounded-xl border border-primary/30 bg-primary/5 px-4 py-2 text-xs text-primary">{notice}</div>
      )}

      {/* 3 Quick Generators */}
      <div className="grid gap-4 lg:grid-cols-3">
        <form onSubmit={handleBuildApp} className="bento-card space-y-2.5 p-4 text-xs">
          <div className="flex items-center gap-1.5 font-bold">
            <Sparkles className="h-4 w-4 text-primary" />
            <span>{isAr ? "تطبيق ويب حي (Web App)" : "Live Web App"}</span>
          </div>
          <input
            value={appPrompt}
            onChange={(e) => setAppPrompt(e.target.value)}
            placeholder={isAr ? "مثال: ابني صفحة هبوط لوكالة سيو عربية" : "Build an Arabic SaaS landing page..."}
            className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
          />
          <button type="submit" disabled={busy} className="w-full rounded-lg bg-primary py-2 font-semibold text-white">
            {isAr ? "إنشاء ومعاينة" : "Build & Preview"}
          </button>
        </form>

        <form onSubmit={handleBuildDeck} className="bento-card space-y-2.5 p-4 text-xs">
          <div className="flex items-center gap-1.5 font-bold">
            <Presentation className="h-4 w-4 text-primary" />
            <span>{isAr ? "عرض تقديمي (HTML Deck)" : "Slide Deck"}</span>
          </div>
          <input
            value={deckTitle}
            onChange={(e) => setDeckTitle(e.target.value)}
            placeholder={isAr ? "عنوان العرض" : "Deck title"}
            className="w-full rounded-lg border border-borderline bg-canvas px-3 py-1.5"
          />
          <input
            value={deckTopic}
            onChange={(e) => setDeckTopic(e.target.value)}
            placeholder={isAr ? "الموضوع والمحاور" : "Topic & outline"}
            className="w-full rounded-lg border border-borderline bg-canvas px-3 py-1.5"
          />
          <button type="submit" disabled={busy} className="w-full rounded-lg bg-primary py-2 font-semibold text-white">
            {isAr ? "توليد الشرائح" : "Generate Deck"}
          </button>
        </form>

        <form onSubmit={handleGenImage} className="bento-card space-y-2.5 p-4 text-xs">
          <div className="flex items-center justify-between font-bold">
            <span className="flex items-center gap-1.5">
              <ImageIcon className="h-4 w-4 text-primary" />
              <span>{isAr ? "توليد صورة (PNG)" : "Image Generator"}</span>
            </span>
            <label className="flex items-center gap-1 text-[10px] font-normal text-muted">
              <input type="checkbox" checked={imgMockMode} onChange={(e) => setImgMockMode(e.target.checked)} />
              <span>{isAr ? "محاكاة" : "Mock"}</span>
            </label>
          </div>
          <input
            value={imgPrompt}
            onChange={(e) => setImgPrompt(e.target.value)}
            placeholder={isAr ? "وصف الصورة..." : "Describe the image..."}
            className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2"
          />
          <button type="submit" disabled={busy} className="w-full rounded-lg bg-primary py-2 font-semibold text-white">
            {isAr ? "توليد الصورة" : "Generate Image"}
          </button>
        </form>
      </div>

      {/* Gallery + Preview Split */}
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="bento-card space-y-2 p-4 lg:col-span-4">
          <div className="text-sm font-bold">{isAr ? `كل المخرجات (${artifacts.length})` : `Artifacts (${artifacts.length})`}</div>
          {artifacts.length === 0 ? (
            <div className="rounded-lg border border-dashed border-borderline p-4 text-center text-xs text-muted">
              {t("common.empty")}
            </div>
          ) : (
            <div className="max-h-[480px] space-y-2 overflow-y-auto">
              {artifacts.map((art) => (
                <button
                  key={art.id}
                  type="button"
                  onClick={() => setSelected(art)}
                  className={`w-full rounded-xl border p-3 text-start text-xs transition ${
                    selected?.id === art.id ? "border-primary bg-primary/10" : "border-borderline bg-canvas hover:border-primary/40"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 font-semibold">
                    <span className="truncate">{art.title}</span>
                    <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-primary">{art.kind}</span>
                  </div>
                  <div className="mt-1 text-[10px] text-muted">{formatDate(art.created_at)}</div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="bento-card space-y-3 p-4 lg:col-span-8">
          {!selected ? (
            <div className="py-16 text-center text-xs text-muted">{isAr ? "اختر مخرجاً لمعاينته" : "Select an artifact to preview"}</div>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-borderline pb-2">
                <div className="flex items-center gap-2 text-sm font-bold">
                  <Eye className="h-4 w-4 text-primary" />
                  <span>{selected.title}</span>
                </div>
                <button
                  type="button"
                  onClick={() => handleDownload(selected)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-borderline bg-canvas px-3 py-1.5 text-xs font-semibold hover:border-primary"
                >
                  <Download className="h-3.5 w-3.5" />
                  <span>{isAr ? "تنزيل" : "Download"}</span>
                </button>
              </div>
              {previewHtml ? (
                <iframe
                  title={selected.title}
                  sandbox="allow-scripts"
                  srcDoc={previewHtml}
                  className="h-[460px] w-full rounded-xl border border-borderline bg-white"
                />
              ) : previewBlobUrl ? (
                <div className="flex justify-center rounded-xl border border-borderline bg-canvas p-4">
                  <img src={previewBlobUrl} alt={selected.title} className="max-h-[440px] rounded-lg object-contain" />
                </div>
              ) : (
                <div className="py-12 text-center text-xs text-muted">{t("common.loading")}</div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
