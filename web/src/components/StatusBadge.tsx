"use client";

import React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  PauseCircle,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { useApp } from "@/i18n/context";

export function StatusBadge({ status }: { status: string }) {
  const { t } = useApp();
  const label = t(`status.${status}`) !== `status.${status}` ? t(`status.${status}`) : status;

  switch (status) {
    case "running":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 px-2.5 py-0.5 text-xs font-medium text-info">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span>{label}</span>
        </span>
      );
    case "done":
    case "live":
    case "active":
    case "ok":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-success">
          <CheckCircle2 className="h-3.5 w-3.5" />
          <span>{label}</span>
        </span>
      );
    case "awaiting_approval":
    case "awaiting_captcha_manual":
    case "awaiting_verification_code":
    case "needs_captcha_manual":
    case "needs_manual":
    case "pending_review":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium text-warning">
          <ShieldAlert className="h-3.5 w-3.5" />
          <span>{label}</span>
        </span>
      );
    case "paused":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-yellow-500/15 px-2.5 py-0.5 text-xs font-medium text-warning">
          <PauseCircle className="h-3.5 w-3.5" />
          <span>{label}</span>
        </span>
      );
    case "failed":
    case "blocked":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/10 px-2.5 py-0.5 text-xs font-medium text-danger">
          <AlertTriangle className="h-3.5 w-3.5" />
          <span>{label}</span>
        </span>
      );
    case "canceled":
    case "disconnected":
    case "excluded_noindex":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-500/15 px-2.5 py-0.5 text-xs font-medium text-muted">
          <XCircle className="h-3.5 w-3.5" />
          <span>{label}</span>
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-500/10 px-2.5 py-0.5 text-xs font-medium text-muted">
          <Clock className="h-3.5 w-3.5" />
          <span>{label}</span>
        </span>
      );
  }
}
