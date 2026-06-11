"use client";

import { useState } from "react";
import { AuthGate } from "@/components/auth-gate";
import { WorkbenchShell } from "@/components/workbench-shell";
import type { AuthSession } from "@/lib/auth";
import type { Language } from "@/lib/i18n";

export default function Home() {
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [language, setLanguage] = useState<Language>("en");

  if (auth === null) {
    return <AuthGate language={language} onLanguageChange={setLanguage} onAuthenticated={setAuth} />;
  }

  return <WorkbenchShell auth={auth} language={language} onLanguageChange={setLanguage} onSignOut={() => setAuth(null)} />;
}
