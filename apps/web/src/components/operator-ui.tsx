"use client";

import type { ReactNode } from "react";
import type { OperatorError } from "@/hooks/use-operator-request";
import type { Language } from "@/lib/i18n";
import { SafeErrorBanner, StatusPill } from "./primitives";
import { Card, CardHeader } from "./ui/card";
import { Input } from "./ui/input";
export const operationText = (language: Language, en: string, zh: string) => language === "zh" ? zh : en;
export function OperationCard({
  title,
  help,
  permission,
  children
}: Readonly<{
  title: string;
  help: string;
  permission: string;
  children: ReactNode;
}>) {
  return <Card as="section" aria-label={title}><CardHeader><div><h2 className="surface-title">{title}</h2><p className="muted">{help}</p></div><StatusPill tone="source">{permission}</StatusPill></CardHeader>{children}</Card>;
}
export function OperationFeedback({
  busy,
  error,
  language
}: Readonly<{
  busy: boolean;
  error: OperatorError | null;
  language: Language;
}>) {
  return <div aria-live="polite">{busy && <p role="status">{operationText(language, "Loading…", "正在处理…")}</p>}{error && <SafeErrorBanner code={error.code} requestId={error.requestId} message={operationText(language, "The operation was not confirmed. Refresh current state before trying again.", "操作未确认。请刷新当前状态后再试。 ")} />}</div>;
}
export function OperationField({
  label,
  value
}: Readonly<{
  label: string;
  value: string | number | null | undefined;
}>) {
  return <div><span className="scope-label">{label}</span><span className="id-text wrap">{value ?? "unknown"}</span></div>;
}
export function OperationInput({
  label,
  value,
  onChange,
  type = "text",
  min,
  max,
  disabled = false
}: Readonly<{
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  min?: number;
  max?: number;
  disabled?: boolean;
}>) {
  return <label><span className="scope-label">{label}</span><Input aria-label={label} value={value} type={type} min={min} max={max} disabled={disabled} onChange={event => onChange(event.target.value)} /></label>;
}
