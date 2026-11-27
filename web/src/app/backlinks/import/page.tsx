"use client";

import React, { useState } from "react";
import { Upload, CheckCircle2 } from "lucide-react";
import { useApp } from "@/i18n/context";
import { apiFetch } from "@/lib/api";

interface PreviewResp {
  headers: string[];
  total_rows: number;
  suggested_mapping: Record<string, string>;
  preview: { row_num: number; data: Record<string, unknown> }[];
}

export default function BacklinksImportPage() {
  const { t, locale, formatNum } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResp | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<{ imported: number; duplicate: number; missing: number } | null>(null);

  const handleFileSelect = async (selected: File) => {
    setFile(selected);
    setResult(null);
    const fd = new FormData();
    fd.append("file", selected);
    const prev = await apiFetch<PreviewResp>("/api/backlinks/import/preview", {
      method: "POST",
      body: fd,
    });
    setPreview(prev);
    setMapping(prev.suggested_mapping || {});
  };

  const handleCommit = async () => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mapping_json", JSON.stringify(mapping));
    const res = await apiFetch<{ imported: number; duplicate: number; missing: number }>("/api/backlinks/import/commit", {
      method: "POST",
      body: fd,
    });
    setResult(res);
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">{t("nav.backlink_import")}</h1>

      <div className="rounded-xl border-2 border-dashed border-borderline bg-surface p-6 text-center space-y-3">
        <Upload className="mx-auto h-8 w-8 text-primary" />
        <div className="text-sm font-semibold">
          {locale === "ar" ? "ارفع شيت Backlinks Farm (.xlsx أو .csv)" : "Upload Backlinks Farm (.xlsx or .csv)"}
        </div>
        <input
          type="file"
          accept=".xlsx,.xlsm,.csv"
          onChange={(e) => e.target.files?.[0] && handleFileSelect(e.target.files[0])}
          className="mx-auto block text-xs"
        />
      </div>

      {result && (
        <div className="flex items-center gap-3 rounded-xl border border-success/30 bg-success/10 p-4 text-sm text-success">
          <CheckCircle2 className="h-5 w-5" />
          <span>
            {locale === "ar"
              ? `تم الاستيراد: ${formatNum(result.imported)} | مكرر: ${formatNum(result.duplicate)} | ناقص: ${formatNum(result.missing)}`
              : `Imported: ${result.imported} | Duplicate: ${result.duplicate} | Missing: ${result.missing}`}
          </span>
        </div>
      )}

      {preview && (
        <div className="space-y-4 rounded-xl border border-borderline bg-surface p-4">
          <div className="text-sm font-bold">
            {locale === "ar" ? `مطابقة الأعمدة (${formatNum(preview.total_rows)} صف)` : `Column Mapping (${preview.total_rows} rows)`}
          </div>
          <div className="grid gap-3 sm:grid-cols-4 text-xs">
            {["domain", "signup_url", "category", "status"].map((field) => (
              <div key={field}>
                <label className="mb-1 block font-mono font-semibold">{field}</label>
                <select
                  value={mapping[field] || ""}
                  onChange={(e) => setMapping((m) => ({ ...m, [field]: e.target.value }))}
                  className="w-full rounded border border-borderline bg-canvas px-2 py-2"
                >
                  <option value="">—</option>
                  {preview.headers.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>

          <button onClick={handleCommit} className="min-h-[44px] rounded-lg bg-primary px-5 text-sm font-semibold text-white">
            {locale === "ar" ? "تأكيد الاستيراد" : "Commit Import"}
          </button>

          <div className="overflow-x-auto">
            <table className="w-full text-start text-xs">
              <thead className="border-b border-borderline bg-canvas text-muted">
                <tr>
                  <th className="p-2 text-start">#</th>
                  {preview.headers.map((h) => (
                    <th key={h} className="p-2 text-start">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.preview.map((row) => (
                  <tr key={row.row_num} className="border-b border-borderline last:border-0">
                    <td className="p-2">{row.row_num}</td>
                    {preview.headers.map((h) => (
                      <td key={h} className="p-2 font-mono">
                        <bdi dir="ltr">{String(row.data[h] ?? "")}</bdi>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
