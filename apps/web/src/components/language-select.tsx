"use client";

import { Languages } from "lucide-react";
import type { Language } from "@/lib/i18n";
import { languageLabel, text, uiText } from "@/lib/i18n";
import { Select } from "./ui/select";

export function LanguageSelect({
  language,
  onLanguageChange
}: Readonly<{ language: Language; onLanguageChange: (language: Language) => void }>) {
  return (
    <label className="language-select">
      <Languages aria-hidden="true" />
      <span className="sr-only">{text(uiText.language, language)}</span>
      <Select
        aria-label={text(uiText.language, language)}
        value={language}
        onChange={(event) => onLanguageChange(event.target.value as Language)}
        className="language-select-control min-h-9 w-auto min-w-[112px] rounded-md bg-transparent py-1 pl-1.5 pr-7 text-sm shadow-none"
      >
        <option value="en">{languageLabel.en}</option>
        <option value="zh">{languageLabel.zh}</option>
      </Select>
    </label>
  );
}
