"use client";

import { useState } from "react";
import { AuthGate } from "@/components/auth-gate";
import { WorkbenchShell } from "@/components/workbench-shell";
import { useAuthSession } from "@/hooks/use-auth-session";
import type { Language } from "@/lib/i18n";

export default function Home() {
  const { auth, notice, authenticate, signOut } = useAuthSession();
  const [language, setLanguage] = useState<Language>("en");

  if (auth === null) {
    return <AuthGate language={language} onLanguageChange={setLanguage} onAuthenticated={authenticate} notice={notice} />;
  }

  return <WorkbenchShell auth={auth} language={language} onLanguageChange={setLanguage} onSignOut={signOut} />;
}
