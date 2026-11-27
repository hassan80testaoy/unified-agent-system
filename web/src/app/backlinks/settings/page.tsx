"use client";

import React, { useEffect, useState } from "react";
import { Plus, Trash2, Save } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface SettingsData {
  identity: { display_name: string; extended_name: string; photo_url: string; title: string };
  targets: { id: string; kind: string; url: string }[];
  bios: { id: string; text: string; angle: string; times_used: number }[];
  anchors: { id: string; target_kind: string; text: string; weight: number; times_used: number }[];
}

export default function BacklinksSettingsPage() {
  const { t, locale, formatNum } = useApp();
  const [data, setData] = useState<SettingsData | null>(null);
  const [newBio, setNewBio] = useState("");
  const [newBioAngle, setNewBioAngle] = useState("ecommerce");
  const [newAnchorText, setNewAnchorText] = useState("");
  const [newAnchorKind, setNewAnchorKind] = useState("homepage");
  const [newAnchorWeight, setNewAnchorWeight] = useState(1.0);
  const [savedMsg, setSavedMsg] = useState("");

  const load = () => {
    apiFetch<SettingsData>("/api/backlinks/settings").then(setData).catch(() => null);
  };

  useEffect(() => {
    load();
  }, []);

  const saveIdentity = async () => {
    if (!data) return;
    await apiFetch("/api/backlinks/identity", {
      method: "PUT",
      body: JSON.stringify(data.identity),
    });
    for (const tgt of data.targets) {
      await apiFetch("/api/backlinks/targets", {
        method: "POST",
        body: JSON.stringify({ kind: tgt.kind, url: tgt.url }),
      });
    }
    setSavedMsg(locale === "ar" ? "تم حفظ الهوية والروابط الهدف." : "Identity and targets saved.");
    load();
  };

  const addBio = async () => {
    if (!newBio.trim()) return;
    await apiFetch("/api/backlinks/bios", {
      method: "POST",
      body: JSON.stringify({ text: newBio, angle: newBioAngle }),
    });
    setNewBio("");
    load();
  };

  const addAnchor = async () => {
    if (!newAnchorText.trim()) return;
    await apiFetch("/api/backlinks/anchors", {
      method: "POST",
      body: JSON.stringify({ target_kind: newAnchorKind, text: newAnchorText, weight: newAnchorWeight }),
    });
    setNewAnchorText("");
    load();
  };

  if (!data) return <div className="p-6 text-sm text-muted">{t("common.loading")}</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">{t("nav.backlink_identity")}</h1>
      {savedMsg && <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-xs text-success">{savedMsg}</div>}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Identity & Targets */}
        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
          <h2 className="text-sm font-bold">{locale === "ar" ? "الهوية والروابط المستهدفة" : "Identity & Target URLs"}</h2>
          <div>
            <label className="mb-1 block text-xs text-muted">{locale === "ar" ? "الاسم المعروض" : "Display Name"}</label>
            <input
              type="text"
              value={data.identity.display_name}
              onChange={(e) => setData({ ...data, identity: { ...data.identity, display_name: e.target.value } })}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">{locale === "ar" ? "الاسم الموسع (عند اتساع المساحة)" : "Extended Name"}</label>
            <input
              type="text"
              value={data.identity.extended_name || ""}
              onChange={(e) => setData({ ...data, identity: { ...data.identity, extended_name: e.target.value } })}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted">{locale === "ar" ? "اللقب" : "Title"}</label>
            <input
              type="text"
              value={data.identity.title || ""}
              onChange={(e) => setData({ ...data, identity: { ...data.identity, title: e.target.value } })}
              className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-sm"
            />
          </div>
          {data.targets.map((tgt, idx) => (
            <div key={tgt.id}>
              <label className="mb-1 block text-xs text-muted">{tgt.kind === "homepage" ? "Homepage URL" : "Google Maps URL"}</label>
              <input
                type="text"
                dir="ltr"
                value={tgt.url}
                onChange={(e) => {
                  const copy = [...data.targets];
                  copy[idx] = { ...tgt, url: e.target.value };
                  setData({ ...data, targets: copy });
                }}
                className="w-full rounded-lg border border-borderline bg-canvas px-3 py-2 text-end font-mono text-xs"
              />
            </div>
          ))}
          <button onClick={saveIdentity} className="inline-flex min-h-[40px] items-center gap-2 rounded-lg bg-primary px-4 text-xs font-semibold text-white">
            <Save className="h-4 w-4" />
            <span>{t("common.save")}</span>
          </button>
        </div>

        {/* Live Profile Preview */}
        <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
          <h2 className="text-sm font-bold">{locale === "ar" ? "معاينة كيف هيظهر البروفايل" : "Live Profile Preview"}</h2>
          <div className="rounded-xl border border-borderline bg-canvas p-4 space-y-2">
            <div className="text-base font-bold text-primary">{data.identity.display_name}</div>
            <div className="text-xs font-semibold text-muted">{data.identity.title}</div>
            <p className="text-xs">{data.bios[0]?.text || ""}</p>
            <div className="flex flex-wrap gap-2 pt-2 text-xs">
              {data.anchors.slice(0, 4).map((a) => (
                <span key={a.id} className="rounded-full bg-primary/10 px-2.5 py-0.5 text-primary underline">
                  {a.text}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Bios */}
      <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
        <h2 className="text-sm font-bold">{locale === "ar" ? "نسخ البايو المتنوعة" : "Bio Variations"}</h2>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="text"
            value={newBio}
            onChange={(e) => setNewBio(e.target.value)}
            placeholder={locale === "ar" ? "أضف بايو جديد..." : "Add new bio variant..."}
            className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
          />
          <input
            type="text"
            value={newBioAngle}
            onChange={(e) => setNewBioAngle(e.target.value)}
            placeholder="angle"
            className="w-32 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
          />
          <button onClick={addBio} className="inline-flex items-center gap-1 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white">
            <Plus className="h-4 w-4" />
            <span>{locale === "ar" ? "إضافة" : "Add"}</span>
          </button>
        </div>
        <div className="space-y-2">
          {data.bios.map((b) => (
            <div key={b.id} className="flex items-center justify-between gap-3 rounded-lg border border-borderline bg-canvas p-3 text-xs">
              <div>
                <span className="me-2 rounded bg-surface px-1.5 py-0.5 font-mono text-[10px]">{b.angle}</span>
                <span>{b.text}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted">({formatNum(b.times_used)})</span>
                <button
                  onClick={() => apiFetch(`/api/backlinks/bios/${b.id}`, { method: "DELETE" }).then(load)}
                  className="text-danger"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Anchors */}
      <div className="rounded-xl border border-borderline bg-surface p-4 space-y-3">
        <h2 className="text-sm font-bold">{locale === "ar" ? "الأنكرات والأوزان" : "Weighted Anchors"}</h2>
        <div className="flex flex-wrap gap-2">
          <select value={newAnchorKind} onChange={(e) => setNewAnchorKind(e.target.value)} className="rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs">
            <option value="homepage">homepage</option>
            <option value="maps">maps</option>
          </select>
          <input
            type="text"
            value={newAnchorText}
            onChange={(e) => setNewAnchorText(e.target.value)}
            placeholder={locale === "ar" ? "نص الأنكر..." : "Anchor text..."}
            className="flex-1 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
          />
          <input
            type="number"
            step="0.1"
            value={newAnchorWeight}
            onChange={(e) => setNewAnchorWeight(Number(e.target.value))}
            className="w-24 rounded-lg border border-borderline bg-canvas px-3 py-2 text-xs"
          />
          <button onClick={addAnchor} className="inline-flex items-center gap-1 rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-white">
            <Plus className="h-4 w-4" />
            <span>{locale === "ar" ? "إضافة" : "Add"}</span>
          </button>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {data.anchors.map((a) => (
            <div key={a.id} className="flex items-center justify-between rounded-lg border border-borderline bg-canvas p-2.5 text-xs">
              <div>
                <span className="me-2 rounded bg-surface px-1.5 py-0.5 font-mono text-[10px]">{a.target_kind}</span>
                <span className="font-semibold">{a.text}</span>
              </div>
              <div className="flex items-center gap-2 text-muted">
                <span>w:{a.weight}</span>
                <span>u:{formatNum(a.times_used)}</span>
                <button onClick={() => apiFetch(`/api/backlinks/anchors/${a.id}`, { method: "DELETE" }).then(load)} className="text-danger">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
